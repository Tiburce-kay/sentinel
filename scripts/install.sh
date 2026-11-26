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

say "1/7 Dépendances système"
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

say "2/7 Environnement Python du service ($PREFIX)"
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

say "3/7 Modèles d'IA (empreintes SHA-256 vérifiées)"
install -d -m 0755 "$MODELS"
[ -d "$REPO/models" ] && cp "$REPO"/models/*.onnx "$MODELS/" 2>/dev/null || true
sh "$REPO/scripts/fetch_models.sh" "$MODELS"
chmod 0644 "$MODELS"/*.onnx

say "4/7 Configuration"
install -d -m 0755 /etc/sentinel
[ -e /etc/sentinel/config.toml ] || install -m 0644 "$REPO/packaging/config.toml" /etc/sentinel/config.toml
install -m 0644 "$REPO/packaging/sentinel-confirm.pam" /etc/pam.d/sentinel-confirm

say "5/7 Module PAM"
make -s -C "$BUILD/pam" pam_sentinel.so
PAMDIR=""
for d in /usr/lib/x86_64-linux-gnu/security /lib/x86_64-linux-gnu/security \
         /usr/lib/aarch64-linux-gnu/security /lib/security /usr/lib/security /usr/lib64/security; do
    [ -e "$d/pam_unix.so" ] && { PAMDIR="$d"; break; }
done
[ -n "$PAMDIR" ] || { echo "Dossier des modules PAM introuvable."; exit 1; }
install -m 0644 "$BUILD/pam/pam_sentinel.so" "$PAMDIR/pam_sentinel.so"
echo "installé dans $PAMDIR"

say "6/7 Commandes et assistant"
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

say "7/7 Service sentineld"
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
