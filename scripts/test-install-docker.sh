#!/bin/sh
# Test d'installation de bout en bout dans un conteneur jetable (sans systemd ni caméra).
#
#   scripts/test-install-docker.sh [image]      (défaut : kalilinux/kali-rolling)
#
# Vérifie, sur la distribution de l'image :
#   1. l'installation complète (scripts/install.sh) ;
#   2. toute la suite de tests Python, avec le Python de la distribution ;
#   3. l'assistant graphique (GTK/libadwaita de la distribution), dans un écran virtuel ;
#   4. le VRAI sudo avec le module PAM : mot de passe correct, puis faux ;
#   5. la désinstallation complète.
# Dernière ligne : « RÉSULTAT <image> : OK » ou « ÉCHEC (…) ».
set -eu
if [ "${SENTINEL_IN_CONTAINER:-}" != 1 ]; then
    image="${1:-kalilinux/kali-rolling:latest}"
    repo="$(cd "$(dirname "$0")/.." && pwd)"
    exec docker run --rm -e SENTINEL_IN_CONTAINER=1 -e SENTINEL_IMAGE="$image" \
        -v "$repo:/src:ro" "$image" sh /src/scripts/test-install-docker.sh
fi

IMAGE="${SENTINEL_IMAGE:-?}"
fail() { echo "!! $*"; echo "RÉSULTAT $IMAGE : ÉCHEC ($*)"; exit 1; }
ok() { echo "ok  $*"; }

. /etc/os-release
echo "=== $PRETTY_NAME"
cp -r /src /tmp/repo && cd /tmp/repo && rm -rf .venv dev-data
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null 2>&1
apt-get install -y -qq sudo passwd xvfb xauth >/dev/null 2>&1 || fail "outils de test"

echo "=== 1. installation"
sh scripts/install.sh > /tmp/install.log 2>&1 || { tail -25 /tmp/install.log; fail "install.sh"; }
PY=/opt/sentinel/venv/bin/python
ok "installé — $($PY --version), libadwaita $($PY -c 'import gi; gi.require_version("Adw","1"); from gi.repository import Adw; print(f"{Adw.get_major_version()}.{Adw.get_minor_version()}")'), GTK $($PY -c 'import gi; gi.require_version("Gtk","4.0"); from gi.repository import Gtk; print(f"{Gtk.get_major_version()}.{Gtk.get_minor_version()}")')"

echo "=== 2. suite de tests"
/opt/sentinel/venv/bin/pip install -q pytest >/dev/null 2>&1
$PY -m pytest -q -p no:cacheprovider tests > /tmp/pytest.log 2>&1 || { tail -30 /tmp/pytest.log; fail "tests"; }
ok "$(tail -1 /tmp/pytest.log)"

echo "=== 3. assistant graphique (démonstration, écran virtuel)"
GSK_RENDERER=cairo GDK_BACKEND=x11 timeout 60 xvfb-run -a -s "-screen 0 1024x768x24" \
    sentinel-setup --demo --snapshot /tmp/snap > /tmp/gui.log 2>&1 || true
pages=$(ls /tmp/snap 2>/dev/null | sed 's/^[0-9]*-//; s/\.png$//' | sort -u | tr '\n' ' ')
for p in welcome capture verify confirm done; do
    echo "$pages" | grep -qw "$p" || { grep -v -E 'libEGL|^$' /tmp/gui.log | tail -15; fail "page $p de l'assistant"; }
done
ok "pages : $pages"

echo "=== 4. sudo réel avec le module PAM"
useradd -m alice && echo 'alice:Bon-Mdp-42' | chpasswd && usermod -aG sudo alice
sentinel-pam enable sudo >/dev/null
[ "$(grep -c pam_sentinel /etc/pam.d/sudo)" = 2 ] || fail "activation PAM"
SENTINEL_CONFIG=/etc/sentinel/config.toml /opt/sentinel/venv/bin/sentineld > /tmp/d.log 2>&1 &
for i in $(seq 120); do grep -q prêt /tmp/d.log 2>/dev/null && break; sleep 0.25; done
grep -q prêt /tmp/d.log || { cat /tmp/d.log; fail "démarrage du service"; }
[ "$(stat -c %a /var/lib/sentinel)" = 700 ] || fail "droits de /var/lib/sentinel"
su alice -c "echo Bon-Mdp-42 | sudo -S -k -p '' true" 2>/dev/null || fail "sudo avec le bon mot de passe"
ok "bon mot de passe accepté"
su alice -c "echo faux | sudo -S -k -p '' true" 2>/dev/null && fail "sudo a accepté un mauvais mot de passe"
ok "mauvais mot de passe refusé"
[ "$(grep -c 'mot de passe validé user=alice' /tmp/d.log)" = 1 ] || { cat /tmp/d.log; fail "notification du mot de passe"; }
ok "service prévenu une seule fois (bon mot de passe uniquement)"

echo "=== 5. désinstallation"
pkill -f sentineld || true
sh scripts/uninstall.sh >/dev/null
grep -q pam_sentinel /etc/pam.d/sudo && fail "PAM non restauré"
[ -e /opt/sentinel ] && fail "fichiers restants"
[ -e /var/lib/sentinel ] && fail "données de visage restantes"
ok "système restauré, données supprimées"

echo "RÉSULTAT $IMAGE : OK"
