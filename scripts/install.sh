#!/bin/sh
# Installe Sentinel sur le système (Debian, Ubuntu, Kali et dérivées).
#   sudo scripts/install.sh
# N'active PAS le visage pour sudo : c'est une étape séparée et volontaire
# (sudo sentinel-pam enable sudo), après l'enregistrement du visage.
set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX=/opt/sentinel
MODELS=/usr/share/sentinel/models

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "À lancer avec sudo : sudo $0"; exit 1; }
command -v apt-get >/dev/null || { echo "Version 1 : distributions de la famille Debian/Ubuntu uniquement."; exit 1; }

say "1/7 Dépendances système"
export DEBIAN_FRONTEND=noninteractive
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 \
    libpam0g-dev libsystemd0 gcc make curl ca-certificates >/dev/null

say "2/7 Environnement Python ($PREFIX)"
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT
# Copie des seules sources : rien n'est écrit par root dans le dépôt de l'utilisateur.
cp -r "$REPO/pyproject.toml" "$REPO/README.md" "$REPO/src" "$REPO/pam" "$BUILD/"
python3 -m venv --system-site-packages "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --quiet --upgrade pip
"$PREFIX/venv/bin/pip" install --quiet "$BUILD"

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
exec $PREFIX/venv/bin/python -m sentinel.gui "\$@"
SH
chmod 0755 /usr/local/bin/sentinel-setup
install -m 0644 "$REPO/packaging/sentinel-setup.desktop" /usr/share/applications/sentinel-setup.desktop

say "7/7 Service sentineld"
install -m 0644 "$REPO/packaging/sentineld.service" /etc/systemd/system/sentineld.service
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload
    systemctl enable --now sentineld >/dev/null 2>&1
    sleep 3
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
       sudo sentinel-pam enable sudo
     Puis testez dans un nouveau terminal : sudo -k && sudo true
  Désactiver à tout moment : sudo sentinel-pam disable sudo
TXT
