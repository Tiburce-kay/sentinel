#!/bin/sh
# Sentinel : confie la clé de chiffrement au coffre de systemd (systemd-creds) :
# scellée dans la puce TPM 2.0 si elle est utilisable, sinon liée au secret du
# système. L'ancienne clé en clair n'est effacée qu'après avoir vérifié que la clé
# protégée déchiffre tous les gabarits ; sinon, retour automatique à l'état précédent.
# Utilisé par scripts/install.sh et par le paquet .deb (postinst).
#   protect-key.sh [PREFIX]      (défaut : /opt/sentinel)
set -eu
PREFIX="${1:-${PREFIX:-/opt/sentinel}}"

protect_key() {
    [ -d /run/systemd/system ] || { echo "systemd absent : étape ignorée"; return 0; }
    if ! systemd-creds --help 2>/dev/null | grep -q "encrypt"; then
        echo "systemd-creds indisponible (systemd < 250) : clé conservée dans un fichier root (0600)"
        return 0
    fi
    CRED=/etc/credstore.encrypted/sentinel-master-key
    MARK="$CRED.mode"
    DROPIN=/etc/systemd/system/sentineld.service.d/50-sentinel-key.conf
    KEY=/var/lib/sentinel/master.key
    if systemd-analyze has-tpm2 >/dev/null 2>&1; then
        MODE=tpm2+host; LABEL="puce TPM 2.0 + secret du système"
    else
        MODE=host; LABEL="secret du système (pas de puce TPM 2.0 utilisable)"
        # Puce TPM 2.0 présente mais bibliothèques absentes (installation minimale) :
        # on le signale, sans rien installer d'office.
        if systemd-analyze has-tpm2 2>/dev/null | grep -q "^+firmware" && \
           systemd-analyze has-tpm2 2>/dev/null | grep -q "^-libraries"; then
            echo "Une puce TPM 2.0 est présente, mais ses bibliothèques manquent. Pour l'utiliser :"
            echo "  sudo apt install tpm2-tools   puis relancez cet installateur."
        elif [ "$(cat /sys/class/tpm/tpm0/tpm_version_major 2>/dev/null)" = "1" ]; then
            echo "Puce TPM 1.2 détectée : si le BIOS propose une TPM 2.0 (ex. Intel PTT, AMD fTPM),"
            echo "l'activer puis relancer cet installateur scellera la clé dans la puce."
        fi
    fi
    if [ -f "$CRED" ] && [ -f "$DROPIN" ] && [ "$(cat "$MARK" 2>/dev/null)" = "$MODE" ] && [ ! -f "$KEY" ]; then
        echo "clé déjà protégée : $LABEL"
        return 0
    fi
    TMP="$(mktemp -p /run sentinel-key.XXXXXX)"
    chmod 600 "$TMP"
    if [ -f "$KEY" ]; then
        cp "$KEY" "$TMP"
    elif [ -f "$CRED" ]; then
        # Passage à une protection plus forte (ex. TPM 2.0 activée depuis) : on relit la
        # clé actuelle et on la remet provisoirement en fichier, le temps de la vérification.
        if ! systemd-creds decrypt --name=sentinel-master-key "$CRED" "$TMP" >/dev/null; then
            rm -f "$TMP"; echo "!! impossible de relire la clé protégée : rien n'est modifié"; return 1
        fi
        install -m 0600 "$TMP" "$KEY"
    else
        rm -f "$TMP"; echo "aucune clé à protéger pour l'instant"; return 0
    fi
    install -d -m 0700 /etc/credstore.encrypted
    # Scellée à CETTE puce, sans lien avec l'état du démarrage (--tpm2-pcrs= vide) : une
    # mise à jour du BIOS ou du Secure Boot ne rend pas la clé illisible, mais une copie
    # du disque sur une autre machine ne peut pas la déchiffrer.
    TPM_OPTS=""
    [ "$MODE" = "tpm2+host" ] && TPM_OPTS="--tpm2-pcrs="
    if ! systemd-creds encrypt --name=sentinel-master-key --with-key="$MODE" $TPM_OPTS "$TMP" "$CRED.new" >/dev/null; then
        shred -u "$TMP" 2>/dev/null || rm -f "$TMP"
        rm -f "$CRED.new"; echo "!! chiffrement impossible : clé conservée dans un fichier root"; return 0
    fi
    shred -u "$TMP" 2>/dev/null || rm -f "$TMP"
    chmod 600 "$CRED.new"
    mv "$CRED.new" "$CRED"
    echo "$MODE" > "$MARK"
    install -d -m 0755 "$(dirname "$DROPIN")"
    printf '[Service]\nLoadCredentialEncrypted=sentinel-master-key:%s\nEnvironment=SENTINEL_KEY_PROTECTION=%s\n' \
        "$CRED" "$MODE" > "$DROPIN"
    systemctl daemon-reload
    systemctl restart sentineld
    sleep 4
    if "$PREFIX/venv/bin/python" - <<'PY'
import sys
from sentinel.client import Client
r = Client("/run/sentinel/sentinel.sock", timeout=30).call("health")
print("contrôle :", r.get("users", 0), "visage(s) déchiffré(s) avec la clé protégée")
sys.exit(0 if r.get("ok") and r.get("key") == "coffre" else 1)
PY
    then
        shred -u "$KEY" 2>/dev/null || rm -f "$KEY"
        echo "clé protégée : $LABEL ; ancienne clé en clair effacée"
    else
        rm -f "$DROPIN"
        systemctl daemon-reload
        systemctl restart sentineld
        echo "!! la clé protégée ne fonctionne pas : retour à la clé en fichier (rien n'est perdu)"
    fi
}

protect_key
