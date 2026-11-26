#!/bin/sh
# Installe Sentinel sur le système (Debian, Ubuntu, Kali et dérivées).
#   sudo scripts/install.sh
# N'active PAS le visage pour sudo : c'est une étape séparée et volontaire
# (sudo /usr/local/sbin/sentinel-pam enable sudo), après l'enregistrement du visage.
set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX=/opt/sentinel
MODELS=/usr/share/sentinel/models

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "À lancer avec sudo : sudo $0"; exit 1; }
command -v apt-get >/dev/null || { echo "Version 1 : distributions de la famille Debian/Ubuntu uniquement."; exit 1; }

say "1/8 Dépendances système"
# Règle : Sentinel n'installe que ce qui manque, et ne met JAMAIS à jour ni ne supprime
# un paquet sans votre accord. Sur une distribution en mise à jour continue (Kali,
# Debian testing…), installer un seul paquet peut entraîner la mise à jour de
# centaines d'autres : dans ce cas, l'installation s'arrête et vous laisse décider.
PKGS="python3 python3-venv python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 libpam0g-dev gcc make curl ca-certificates"
MISSING=""
for p in $PKGS; do
    dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "ok installed" || MISSING="$MISSING $p"
done
if [ -z "$MISSING" ]; then
    echo "déjà présentes"
else
    echo "à installer :$MISSING"
    PLAN="$(apt-get install -s --no-install-recommends $MISSING 2>&1)" || { echo "$PLAN" | tail -5; exit 1; }
    UPGRADES="$(echo "$PLAN" | grep -E '^Inst [^ ]+ \[' | awk '{print $2}' || true)"
    REMOVALS="$(echo "$PLAN" | grep '^Remv' | awk '{print $2}' || true)"
    if [ -n "$UPGRADES$REMOVALS" ]; then
        nu=$(echo "$UPGRADES" | grep -c . || true); nr=$(echo "$REMOVALS" | grep -c . || true)
        echo
        echo "ARRÊT : installer ces paquets mettrait à jour $nu paquet(s) et en supprimerait $nr :"
        [ -n "$REMOVALS" ] && echo "  supprimés : $(echo $REMOVALS)"
        [ -n "$UPGRADES" ] && echo "  mis à jour : $(echo $UPGRADES | cut -c1-300)…"
        echo
        echo "Sentinel ne modifie pas votre système sans votre accord. Au choix :"
        echo "  - mettez d'abord votre système à jour vous-même (sudo apt full-upgrade), puis relancez ;"
        echo "  - ou installez ces paquets en connaissance de cause : sudo apt install$MISSING"
        exit 1
    fi
    apt-get install -y --no-install-recommends $MISSING
fi

say "2/8 Environnement Python du service ($PREFIX)"
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT
# Copie des seules sources : rien n'est écrit par root dans le dépôt de l'utilisateur.
cp -r "$REPO/pyproject.toml" "$REPO/README.md" "$REPO/src" "$REPO/pam" "$BUILD/"
# Environnement isolé (sans les paquets Python du système) : aucune interférence.
python3 -m venv --clear "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --quiet --upgrade pip
"$PREFIX/venv/bin/pip" install --quiet "$BUILD"
echo "Python $("$PREFIX/venv/bin/python" -c 'import sys; print(sys.version.split()[0])')"
# L'assistant graphique utilise le Python du système (GTK) et seulement le code de
# Sentinel, qui ne dépend que de la bibliothèque standard pour cette partie.
rm -rf "$PREFIX/gui" && install -d -m 0755 "$PREFIX/gui"
cp -r "$BUILD/src/sentinel" "$PREFIX/gui/sentinel"

say "3/8 Modèles d'IA (empreintes SHA-256 vérifiées)"
install -d -m 0755 "$MODELS"
[ -d "$REPO/models" ] && cp "$REPO"/models/*.onnx "$MODELS/" 2>/dev/null || true
sh "$REPO/scripts/fetch_models.sh" "$MODELS"
chmod 0644 "$MODELS"/*.onnx

say "4/8 Configuration"
install -d -m 0755 /etc/sentinel
[ -e /etc/sentinel/config.toml ] || install -m 0644 "$REPO/packaging/config.toml" /etc/sentinel/config.toml
install -m 0644 "$REPO/packaging/sentinel-confirm.pam" /etc/pam.d/sentinel-confirm

say "5/8 Module PAM"
make -s -C "$BUILD/pam" pam_sentinel.so
PAMDIR=""
for d in /usr/lib/x86_64-linux-gnu/security /lib/x86_64-linux-gnu/security \
         /usr/lib/aarch64-linux-gnu/security /lib/security /usr/lib/security /usr/lib64/security; do
    [ -e "$d/pam_unix.so" ] && { PAMDIR="$d"; break; }
done
[ -n "$PAMDIR" ] || { echo "Dossier des modules PAM introuvable."; exit 1; }
install -m 0644 "$BUILD/pam/pam_sentinel.so" "$PAMDIR/pam_sentinel.so"
echo "installé dans $PAMDIR"

say "6/8 Commandes et assistant"
ln -sf "$PREFIX/venv/bin/sentinel" /usr/local/bin/sentinel
ln -sf "$PREFIX/venv/bin/sentinel-pam" /usr/local/sbin/sentinel-pam
cat > /usr/local/bin/sentinel-setup <<SH
#!/bin/sh
PYTHONPATH=$PREFIX/gui exec /usr/bin/python3 -m sentinel.gui "\$@"
SH
chmod 0755 /usr/local/bin/sentinel-setup
install -m 0644 "$REPO/packaging/sentinel-setup.desktop" /usr/share/applications/sentinel-setup.desktop
# Extension GNOME Shell : bouton « utiliser le mot de passe » sur l'écran de
# verrouillage. Activée pour chaque utilisateur par l'assistant (prise en compte à la
# prochaine ouverture de session).
EXT=sentinel@tiburce-kay.github.io
rm -rf "/usr/share/gnome-shell/extensions/$EXT"
install -d -m 0755 "/usr/share/gnome-shell/extensions/$EXT"
install -m 0644 "$REPO/packaging/gnome-shell-extension/$EXT/"* "/usr/share/gnome-shell/extensions/$EXT/"
# Écran de connexion (GDM, Debian/Ubuntu) : extension activée pour le compte système de
# GDM, via le mécanisme prévu par la distribution (fichiers compilés au démarrage de GDM).
if [ -x /usr/share/gdm/generate-config ] && [ -d /usr/share/gdm/dconf ]; then
    install -m 0644 "$REPO/packaging/gdm-dconf-95-sentinel" /usr/share/gdm/dconf/95-sentinel
    # Verrou : l'écran de connexion (GDM 49+) a sa propre base de réglages, qui
    # primerait sinon sur ce fichier.
    install -d -m 0755 /usr/share/gdm/dconf/locks
    install -m 0644 "$REPO/packaging/gdm-dconf-locks-95-sentinel" /usr/share/gdm/dconf/locks/95-sentinel
    /usr/share/gdm/generate-config >/dev/null 2>&1 || true
    echo "extension activée pour l'écran de connexion (au prochain démarrage de GDM)"
fi

say "7/8 Service sentineld"
install -m 0644 "$REPO/packaging/sentineld.service" /etc/systemd/system/sentineld.service
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload
    systemctl enable sentineld >/dev/null 2>&1
    systemctl restart sentineld        # relance aussi après une mise à jour
    sleep 4
    systemctl is-active --quiet sentineld && echo "service actif" || {
        echo "Le service n'a pas démarré : journalctl -u sentineld"; exit 1; }
else
    echo "systemd absent : service non démarré (environnement de test ?)"
fi

say "8/8 Protection de la clé de chiffrement"
# La clé qui chiffre les visages et le mot de passe conservé est confiée au coffre de
# systemd (systemd-creds) : scellée dans la puce TPM 2.0 quand il y en a une, sinon
# liée à un secret propre à ce système. Prudence : l'ancienne clé en clair n'est
# effacée qu'après avoir vérifié que la clé protégée déchiffre bien tous les gabarits ;
# sinon, retour automatique à l'état précédent.
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

say "Installation terminée."
cat <<'TXT'
Étapes suivantes :
  1. Enregistrez votre visage : lancez « Sentinel » depuis le menu des applications
     (ou la commande sentinel-setup).
  2. Activez le visage pour sudo, en gardant un terminal root ouvert par sécurité :
       sudo -i                       (dans un AUTRE terminal, à laisser ouvert)
       sudo /usr/local/sbin/sentinel-pam enable sudo
     Puis testez dans un nouveau terminal : sudo -k && sudo true
  Désactiver à tout moment : sudo /usr/local/sbin/sentinel-pam disable sudo
TXT
