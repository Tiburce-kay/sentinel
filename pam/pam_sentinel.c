/*
 * pam_sentinel.so — module PAM de Sentinel.
 *
 * Ce module ne fait presque rien, et c'est voulu : il ne charge ni modèle, ni
 * caméra, ni Python. Il demande simplement au service sentineld (socket Unix local)
 * si l'utilisateur est bien devant la caméra, et transmet la réponse à PAM.
 *
 * Toujours à déclarer en « sufficient », jamais en « required » : en cas d'échec,
 * de service absent ou de délai dépassé, PAM passe au mot de passe.
 *
 *   auth  sufficient  pam_sentinel.so            # avant le mot de passe
 *   auth  optional    pam_sentinel.so notify     # après : signale un mot de passe correct
 *
 * Options : socket=CHEMIN  timeout=SECONDES  quiet  debug
 */

#define PAM_SM_AUTH
#include <security/pam_ext.h>
#include <security/pam_modules.h>

#include <ctype.h>
#include <errno.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <syslog.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <time.h>
#include <strings.h>
#include <unistd.h>

#define DEFAULT_SOCKET "/run/sentinel/sentinel.sock"
#define DEFAULT_TIMEOUT_S 12 /* 4 s d'analyse + 3 s de défi éventuel + caméra + marge */
#define MAX_RESPONSE 2048

struct options {
    const char *socket;
    int timeout_s;
    int notify;
    int quiet;
    int debug;
};

static void parse_options(int argc, const char **argv, struct options *o)
{
    o->socket = DEFAULT_SOCKET;
    o->timeout_s = DEFAULT_TIMEOUT_S;
    o->notify = o->quiet = o->debug = 0;
    for (int i = 0; i < argc; i++) {
        if (strncmp(argv[i], "socket=", 7) == 0 && argv[i][7] == '/')
            o->socket = argv[i] + 7;
        else if (strncmp(argv[i], "timeout=", 8) == 0) {
            int t = atoi(argv[i] + 8);
            if (t >= 1 && t <= 30)
                o->timeout_s = t;
        } else if (strcmp(argv[i], "notify") == 0)
            o->notify = 1;
        else if (strcmp(argv[i], "quiet") == 0)
            o->quiet = 1;
        else if (strcmp(argv[i], "debug") == 0)
            o->debug = 1;
    }
}

/* Noms d'utilisateur POSIX simples, comme le service les accepte : aucun caractère
 * qui pourrait casser la requête JSON. */
static int valid_user(const char *s)
{
    size_t n = s ? strlen(s) : 0;
    if (n == 0 || n > 32 || !(islower((unsigned char)s[0]) || s[0] == '_'))
        return 0;
    for (size_t i = 1; i < n; i++) {
        unsigned char c = (unsigned char)s[i];
        if (!(islower(c) || isdigit(c) || c == '_' || c == '-' || (c == '$' && i == n - 1)))
            return 0;
    }
    return 1;
}

static int valid_service(const char *s)
{
    size_t n = s ? strlen(s) : 0;
    if (n == 0 || n > 64)
        return 0;
    for (size_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)s[i];
        if (!(islower(c) || isdigit(c) || c == '_' || c == '-' || c == '.'))
            return 0;
    }
    return 1;
}

static long now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1000L + ts.tv_nsec / 1000000L;
}

/* Connexion au service, lecture ligne à ligne avec un délai global. */
struct conn {
    int fd;
    long deadline;
    char buf[MAX_RESPONSE];
    size_t len;
};

static int conn_open(struct conn *c, const char *path, const char *request, int timeout_s)
{
    struct sockaddr_un addr;
    c->fd = -1;
    c->len = 0;
    c->deadline = now_ms() + timeout_s * 1000L;
    if (strlen(path) >= sizeof(addr.sun_path))
        return -1;
    c->fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (c->fd < 0)
        return -1;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    memcpy(addr.sun_path, path, strlen(path) + 1);
    if (connect(c->fd, (struct sockaddr *)&addr, sizeof(addr)) < 0)
        return -1;
    size_t len = strlen(request), sent = 0;
    while (sent < len) {
        ssize_t w = send(c->fd, request + sent, len - sent, MSG_NOSIGNAL);
        if (w < 0 && errno == EINTR)
            continue;
        if (w < 0)
            return -1;
        sent += (size_t)w;
    }
    return 0;
}

/* Lit la ligne suivante dans `line` (sans le saut de ligne). 0 si succès. */
static int conn_line(struct conn *c, char *line, size_t line_len)
{
    for (;;) {
        char *nl = memchr(c->buf, '\n', c->len);
        if (nl) {
            size_t n = (size_t)(nl - c->buf);
            if (n >= line_len)
                return -1;
            memcpy(line, c->buf, n);
            line[n] = '\0';
            memmove(c->buf, nl + 1, c->len - n - 1);
            c->len -= n + 1;
            return 0;
        }
        if (c->len >= sizeof(c->buf) - 1)
            return -1;
        long left = c->deadline - now_ms();
        if (left <= 0)
            return -1;
        struct pollfd p = {.fd = c->fd, .events = POLLIN};
        int r = poll(&p, 1, (int)left);
        if (r < 0 && errno == EINTR)
            continue;
        if (r <= 0)
            return -1;
        ssize_t got = recv(c->fd, c->buf + c->len, sizeof(c->buf) - 1 - c->len, 0);
        if (got < 0 && errno == EINTR)
            continue;
        if (got <= 0)
            return -1;
        c->len += (size_t)got;
    }
}

static void conn_close(struct conn *c)
{
    if (c->fd >= 0)
        close(c->fd);
    c->fd = -1;
}

/* Préfixe comparé sur sa longueur exacte (une longueur écrite à la main a déjà
 * causé un bug : le message « caméra » n'était jamais reconnu). */
static int starts_with(const char *s, const char *prefix)
{
    return strncmp(s, prefix, strlen(prefix)) == 0;
}

/* Extrait la valeur texte d'une clé (réponses JSON du service, format connu) en
 * décodant les échappements JSON. Renvoie -1 si la valeur est absente, trop longue
 * ou mal formée (dans ce cas `out` est vidé). */
static int json_string(const char *json, const char *key, char *out, size_t out_len)
{
    char pattern[32];
    snprintf(pattern, sizeof(pattern), "\"%s\": \"", key);
    out[0] = '\0';
    const char *p = strstr(json, pattern);
    if (!p)
        return -1;
    p += strlen(pattern);
    size_t i = 0;
    while (*p && *p != '"') {
        unsigned char ch = (unsigned char)*p++;
        if (ch == '\\') {
            switch (*p++) {
            case '"': ch = '"'; break;
            case '\\': ch = '\\'; break;
            case '/': ch = '/'; break;
            case 'b': ch = '\b'; break;
            case 'f': ch = '\f'; break;
            case 'n': ch = '\n'; break;
            case 'r': ch = '\r'; break;
            case 't': ch = '\t'; break;
            case 'u': {
                unsigned v = 0;
                for (int k = 0; k < 4; k++) {
                    char h = *p++;
                    v <<= 4;
                    if (h >= '0' && h <= '9') v |= (unsigned)(h - '0');
                    else if (h >= 'a' && h <= 'f') v |= (unsigned)(h - 'a' + 10);
                    else if (h >= 'A' && h <= 'F') v |= (unsigned)(h - 'A' + 10);
                    else goto bad;
                }
                if (v == 0 || v > 0x7f)  /* le service envoie l'UTF-8 brut : seuls les */
                    goto bad;            /* caractères de contrôle arrivent en \\uXXXX   */
                ch = (unsigned char)v;
                break;
            }
            default: goto bad;
            }
        }
        if (i >= out_len - 1)
            goto bad;
        out[i++] = (char)ch;
    }
    if (*p != '"')
        goto bad;
    out[i] = '\0';
    return 0;
bad:
    explicit_bzero(out, out_len);
    return -1;
}

/* Encode une chaîne en JSON (guillemets, barres obliques, caractères de contrôle). */
static int json_escape(const char *in, char *out, size_t out_len)
{
    size_t o = 0;
    for (const unsigned char *p = (const unsigned char *)in; *p; p++) {
        char tmp[8];
        const char *add;
        if (*p == '"') add = "\\\"";
        else if (*p == '\\') add = "\\\\";
        else if (*p < 0x20) { snprintf(tmp, sizeof(tmp), "\\u%04x", *p); add = tmp; }
        else { tmp[0] = (char)*p; tmp[1] = '\0'; add = tmp; }
        size_t n = strlen(add);
        if (o + n >= out_len)
            return -1;
        memcpy(out + o, add, n);
        o += n;
    }
    out[o] = '\0';
    return 0;
}

static int is_remote(pam_handle_t *pamh)
{
    const void *rhost = NULL;
    if (pam_get_item(pamh, PAM_RHOST, &rhost) != PAM_SUCCESS || !rhost)
        return 0;
    const char *h = rhost;
    return h[0] != '\0' && strcmp(h, "localhost") != 0 && strcmp(h, "127.0.0.1") != 0 && strcmp(h, "::1") != 0;
}

PAM_EXTERN int pam_sm_authenticate(pam_handle_t *pamh, int flags, int argc, const char **argv)
{
    (void)flags;
    struct options o;
    parse_options(argc, argv, &o);

    const char *user = NULL;
    if (pam_get_user(pamh, &user, NULL) != PAM_SUCCESS || !valid_user(user))
        return PAM_IGNORE;

    char request[160], resp[MAX_RESPONSE], msg[256];

    if (o.notify) {
        /* Placé après le mot de passe : n'est atteint que si celui-ci était correct.
         * Lève le verrouillage et satisfait la règle « mot de passe au démarrage ». */
        /* Le mot de passe qui vient d'être vérifié est transmis : le service le conserve
         * (chiffré) seulement si l'utilisateur a choisi le visage au démarrage, pour
         * ouvrir le trousseau. Sinon il l'ignore. */
        struct conn c;
        char escaped[1100], big[1300];
        const void *tok = NULL;
        escaped[0] = '\0';
        if (pam_get_item(pamh, PAM_AUTHTOK, &tok) == PAM_SUCCESS && tok && strlen(tok) <= 256)
            json_escape(tok, escaped, sizeof(escaped));
        if (escaped[0])
            snprintf(big, sizeof(big), "{\"op\": \"password_ok\", \"user\": \"%s\", \"authtok\": \"%s\"}\n",
                     user, escaped);
        else
            snprintf(big, sizeof(big), "{\"op\": \"password_ok\", \"user\": \"%s\"}\n", user);
        if (conn_open(&c, o.socket, big, 3) == 0)
            conn_line(&c, resp, sizeof(resp));
        conn_close(&c);
        explicit_bzero(escaped, sizeof(escaped));
        explicit_bzero(big, sizeof(big));
        return PAM_IGNORE;
    }

    /* Jamais de caméra pour une connexion à distance (SSH…). */
    if (is_remote(pamh))
        return PAM_IGNORE;

    const void *service = NULL;
    if (pam_get_item(pamh, PAM_SERVICE, &service) != PAM_SUCCESS || !valid_service(service))
        return PAM_IGNORE;

    snprintf(request, sizeof(request),
             "{\"op\": \"authenticate\", \"user\": \"%s\", \"service\": \"%s\", \"progress\": true}\n",
             user, (const char *)service);

    struct conn c;
    int ok = conn_open(&c, o.socket, request, o.timeout_s) == 0 && conn_line(&c, resp, sizeof(resp)) == 0;
    /* Messages intermédiaires du service, avant la réponse finale :
     *   camera    : la caméra s'allume ;
     *   challenge : défi à relever (« Tournez la tête vers votre gauche »). */
    while (ok && starts_with(resp, "{\"event\": ")) {
        if (starts_with(resp, "{\"event\": \"camera\"")) {
            if (!o.quiet)
                pam_info(pamh, "Sentinel : regardez la caméra…");
        } else if (starts_with(resp, "{\"event\": \"challenge\"")) {
            if (json_string(resp, "message", msg, sizeof(msg)) == 0 && msg[0])
                pam_info(pamh, "Sentinel : %s.", msg);
        }
        ok = conn_line(&c, resp, sizeof(resp)) == 0;
    }
    conn_close(&c);
    if (!ok) {
        if (o.debug)
            pam_syslog(pamh, LOG_DEBUG, "service sentineld injoignable ou trop lent");
        return PAM_AUTHINFO_UNAVAIL;
    }

    if (starts_with(resp, "{\"ok\": true")) {
        if (!o.quiet)
            pam_info(pamh, "Sentinel : visage reconnu.");
        {
            /* Connexion par le visage : mot de passe rendu par le service (option
             * « connexion au démarrage ») pour que pam_gnome_keyring ouvre le trousseau. */
            char tok[300];
            if (json_string(resp, "authtok", tok, sizeof(tok)) == 0 && tok[0])
                pam_set_item(pamh, PAM_AUTHTOK, tok);
            explicit_bzero(tok, sizeof(tok));
            explicit_bzero(resp, sizeof(resp));
        }
        return PAM_SUCCESS;
    }

    char status[64];
    json_string(resp, "status", status, sizeof(status));
    /* Visage non enregistré ou non activé pour ce service : rester silencieux, le mot
     * de passe est demandé comme d'habitude. */
    if (strcmp(status, "not_enrolled") == 0 || strcmp(status, "disabled") == 0 ||
        strcmp(status, "paused") == 0)
        return PAM_IGNORE;

    json_string(resp, "message", msg, sizeof(msg));
    if (!o.quiet && msg[0])
        pam_info(pamh, "Sentinel : %s.", msg);
    if (o.debug)
        pam_syslog(pamh, LOG_DEBUG, "refus pour %s : %s", user, status);
    return PAM_AUTH_ERR;
}

PAM_EXTERN int pam_sm_setcred(pam_handle_t *pamh, int flags, int argc, const char **argv)
{
    (void)pamh;
    (void)flags;
    (void)argc;
    (void)argv;
    return PAM_SUCCESS;
}
