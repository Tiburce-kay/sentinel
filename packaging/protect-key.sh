#!/bin/sh
# Sentinel : confie la clé de chiffrement au coffre de systemd (systemd-creds) : scellée
# dans la puce TPM 2.0 si elle est utilisable, sinon liée au secret du système.
# Utilisé par scripts/install.sh et par le paquet .deb (postinst).
#   protect-key.sh [PREFIX]      (défaut : /opt/sentinel)
#
# Règles de sûreté (incident réel du 27/11 : une clé neuve avait remplacé la bonne) :
#  1. La clé du coffre, si elle existe, est LA clé : elle n'est jamais remplacée par
#     une clé différente. Si un fichier de clé diffère d'elle, rien n'est modifié.
#  2. Tout changement est suivi d'un contrôle (le service doit déchiffrer tous les
#     gabarits avec la clé du coffre) ; en cas d'échec, retour exact à l'état précédent.
#  3. La copie en clair de la clé n'est effacée qu'après un contrôle réussi.
set -eu
PREFIX="${1:-${PREFIX:-/opt/sentinel}}"
CRED="${SENTINEL_CRED:-/etc/credstore.encrypted/sentinel-master-key}"
MARK="$CRED.mode"
DROPIN="${SENTINEL_DROPIN:-/etc/systemd/system/sentineld.service.d/50-sentinel-key.conf}"
KEY="${SENTINEL_KEY:-/var/lib/sentinel/master.key}"
TMPDIR_KEY="${SENTINEL_TMPDIR:-/run}"

if [ -z "${SENTINEL_FORCE_SYSTEMD:-}" ] && [ ! -d /run/systemd/system ]; then
    echo "systemd absent : étape ignorée"; exit 0
fi
if ! systemd-creds --help 2>/dev/null | grep -q "encrypt"; then
    echo "systemd-creds indisponible (systemd < 250) : clé conservée dans un fichier root (0600)"
    exit 0
fi

if systemd-analyze has-tpm2 >/dev/null 2>&1; then
    MODE=tpm2+host; LABEL="puce TPM 2.0 + secret du système"
else
    MODE=host; LABEL="secret du système (pas de puce TPM 2.0 utilisable)"
    if systemd-analyze has-tpm2 2>/dev/null | grep -q "^+firmware" && \
       systemd-analyze has-tpm2 2>/dev/null | grep -q "^-libraries"; then
        echo "Une puce TPM 2.0 est présente, mais ses bibliothèques manquent. Pour l'utiliser :"
        echo "  sudo apt install tpm2-tools   puis relancez l'installation."
    elif [ "$(cat /sys/class/tpm/tpm0/tpm_version_major 2>/dev/null)" = "1" ]; then
        echo "Puce TPM 1.2 détectée : si le BIOS propose une TPM 2.0 (ex. Intel PTT, AMD fTPM),"
        echo "l'activer puis relancer l'installation scellera la clé dans la puce."
    fi
fi

new_tmp() { t="$(mktemp -p "$TMPDIR_KEY" sentinel-key.XXXXXX)"; chmod 600 "$t"; echo "$t"; }
wipe() { for f in "$@"; do [ -e "$f" ] && { shred -u "$f" 2>/dev/null || rm -f "$f"; }; done; return 0; }
decrypt_vault() { systemd-creds decrypt --name=sentinel-master-key "$CRED" "$1" >/dev/null; }
encrypt_to() {  # $1 = clé en clair, $2 = destination, $3 = mode
    opts=""; [ "$3" = "tpm2+host" ] && opts="--tpm2-pcrs="
    # Scellée à CETTE puce, sans lien avec l'état du démarrage (--tpm2-pcrs= vide) :
    # une mise à jour du BIOS ou du Secure Boot ne rend pas la clé illisible.
    systemd-creds encrypt --name=sentinel-master-key --with-key="$3" $opts "$1" "$2" >/dev/null
}
use_vault() {  # relie le service au coffre, avec le mode indiqué, puis redémarre
    install -d -m 0755 "$(dirname "$DROPIN")"
    printf '[Service]\nLoadCredentialEncrypted=sentinel-master-key:%s\nEnvironment=SENTINEL_KEY_PROTECTION=%s\n' \
        "$CRED" "$1" > "$DROPIN"
    systemctl daemon-reload
    systemctl restart sentineld
    sleep "${SENTINEL_WAIT:-4}"
}
no_vault() { rm -f "$DROPIN"; systemctl daemon-reload; systemctl restart sentineld; }
health_once() {  # 0 : clé protégée valide ; 1 : échec ; 3 : service pas encore prêt
    "$PREFIX/venv/bin/python" - <<'PY'
import sys
from sentinel.client import Client, ServiceError
try:
    r = Client("/run/sentinel/sentinel.sock", timeout=30).call("health")
except ServiceError:
    sys.exit(3)
print("contrôle :", r.get("users", 0), "visage(s) déchiffré(s) avec la clé protégée")
sys.exit(0 if r.get("ok") and r.get("key") == "coffre" else 1)
PY
}
health() {
    # Le service peut mettre plusieurs secondes à démarrer : l'ouverture de la clé par la
    # puce TPM est lente (4,2 s mesurées sur un ThinkPad T570, 27/11/2026). On attend
    # qu'il réponde, jusqu'à SENTINEL_HEALTH_TRIES essais espacés d'une seconde, avant
    # de conclure ; un service tombé en échec arrête l'attente tout de suite.
    tries="${SENTINEL_HEALTH_TRIES:-60}"
    while :; do
        health_once
        code=$?
        [ "$code" -ne 3 ] && return "$code"
        tries=$((tries - 1))
        if [ "$tries" -le 0 ]; then
            echo "le service ne répond pas"
            return 1
        fi
        if [ -z "${SENTINEL_FORCE_SYSTEMD:-}" ] && systemctl is-failed --quiet sentineld 2>/dev/null; then
            echo "le service n'a pas pu démarrer avec la clé protégée"
            return 1
        fi
        sleep "${SENTINEL_HEALTH_DELAY:-1}"
    done
}

# --- Cas 1 : pas encore de coffre -> y placer la clé existante --------------------
if [ ! -f "$CRED" ]; then
    [ -f "$KEY" ] || { echo "aucune clé à protéger pour l'instant"; exit 0; }
    install -d -m 0700 "$(dirname "$CRED")"
    if ! encrypt_to "$KEY" "$CRED.new" "$MODE"; then
        rm -f "$CRED.new"; echo "!! chiffrement impossible : clé conservée dans un fichier root"; exit 0
    fi
    chmod 600 "$CRED.new"; mv "$CRED.new" "$CRED"; echo "$MODE" > "$MARK"
    use_vault "$MODE"
    if health; then
        wipe "$KEY"; echo "clé protégée : $LABEL ; copie en clair effacée"; exit 0
    fi
    rm -f "$CRED" "$MARK"; no_vault
    echo "!! la clé protégée ne fonctionne pas : retour à la clé en fichier (rien n'est perdu)"; exit 1
fi

# --- Le coffre existe : c'est LA clé -------------------------------------------------
CUR="$(cat "$MARK" 2>/dev/null || echo host)"
if [ -f "$KEY" ]; then
    T="$(new_tmp)"
    if ! decrypt_vault "$T"; then
        wipe "$T"; echo "!! impossible de relire le coffre : rien n'est modifié"; exit 1
    fi
    if ! cmp -s "$T" "$KEY"; then
        wipe "$T"
        echo "!! la clé du coffre et le fichier de clé diffèrent : rien n'est modifié."
        echo "   (Les visages enregistrés avec l'une des deux ne peuvent pas être déchiffrés par l'autre.)"
        exit 1
    fi
    wipe "$T"
fi

# Service pas (ou plus) relié au coffre (ex. réinstallation après désinstallation
# en gardant les données) : on le relie, sans toucher à la clé.
if [ ! -f "$DROPIN" ]; then
    use_vault "$CUR"
    if ! health; then
        no_vault
        echo "!! le coffre ne déchiffre pas les visages enregistrés : service laissé sans coffre"; exit 1
    fi
    echo "service relié au coffre existant"
fi

if [ "$CUR" = "$MODE" ]; then
    [ -f "$KEY" ] && wipe "$KEY" && echo "copie en clair de la clé effacée"
    echo "clé protégée : $LABEL"; exit 0
fi

# --- Protection plus forte disponible (ex. TPM 2.0 activée depuis) : même clé, rescellée
T="$(new_tmp)"
if ! decrypt_vault "$T"; then wipe "$T"; echo "!! impossible de relire le coffre : rien n'est modifié"; exit 1; fi
[ -f "$KEY" ] || install -m 0600 "$T" "$KEY"     # filet de sécurité pendant l'opération
cp -p "$CRED" "$CRED.bak"
if ! encrypt_to "$T" "$CRED.new" "$MODE"; then
    wipe "$T"; rm -f "$CRED.new" "$CRED.bak"; echo "!! rescellement impossible : protection inchangée ($CUR)"; exit 0
fi
wipe "$T"
chmod 600 "$CRED.new"; mv "$CRED.new" "$CRED"; echo "$MODE" > "$MARK"
use_vault "$MODE"
if health; then
    rm -f "$CRED.bak"; wipe "$KEY"
    echo "clé protégée : $LABEL ; copie en clair effacée"; exit 0
fi
mv "$CRED.bak" "$CRED"; echo "$CUR" > "$MARK"; use_vault "$CUR"
echo "!! la nouvelle protection ne fonctionne pas : retour à la précédente ($CUR)"; exit 1
