/* Charge une pile PAM depuis un dossier de configuration de test (pam_start_confdir)
 * et affiche le résultat : sert à tester pam_sentinel.so sans toucher à /etc/pam.d.
 * Usage : pam-harness DOSSIER SERVICE UTILISATEUR */
#include <security/pam_appl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int conv(int n, const struct pam_message **msg, struct pam_response **resp, void *data)
{
    (void)data;
    struct pam_response *r = calloc((size_t)n, sizeof(*r));
    if (!r)
        return PAM_BUF_ERR;
    for (int i = 0; i < n; i++) {
        if (msg[i]->msg_style == PAM_TEXT_INFO || msg[i]->msg_style == PAM_ERROR_MSG)
            printf("  [message] %s\n", msg[i]->msg);
        else
            r[i].resp = strdup(""); /* aucun mot de passe : on ne teste que le visage */
    }
    *resp = r;
    return PAM_SUCCESS;
}

int main(int argc, char **argv)
{
    if (argc != 4) {
        fprintf(stderr, "usage : %s DOSSIER SERVICE UTILISATEUR\n", argv[0]);
        return 2;
    }
    struct pam_conv c = {conv, NULL};
    pam_handle_t *h = NULL;
    int rc = pam_start_confdir(argv[2], argv[3], &c, argv[1], &h);
    if (rc == PAM_SUCCESS && getenv("PAM_RHOST"))  /* simule une connexion à distance */
        pam_set_item(h, PAM_RHOST, getenv("PAM_RHOST"));
    if (rc == PAM_SUCCESS)
        rc = pam_authenticate(h, 0);
    printf("  résultat PAM : %s\n", pam_strerror(h, rc));
    pam_end(h, rc);
    return rc == PAM_SUCCESS ? 0 : 1;
}
