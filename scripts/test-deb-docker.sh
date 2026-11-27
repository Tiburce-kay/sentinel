#!/bin/sh
# Teste un paquet .deb de Sentinel dans un conteneur vierge (sans systemd ni caméra).
#
#   scripts/test-deb-docker.sh ubuntu:24.04 dist/sentinel_0.2.0-1~ubuntu24.04_amd64.deb
#
# Vérifie : installation par apt (dépendances résolues), activation via sentinel-pam,
# vrai sudo (bon puis mauvais mot de passe), assistant graphique, puis retrait :
# PAM restauré et sudo TOUJOURS fonctionnel au mot de passe, enfin purge des données.
set -eu
if [ "${SENTINEL_IN_CONTAINER:-}" != 1 ]; then
    image="${1:?usage : scripts/test-deb-docker.sh IMAGE PAQUET.deb}"
    deb="$(cd "$(dirname "${2:?paquet .deb}")" && pwd)/$(basename "$2")"
    repo="$(cd "$(dirname "$0")/.." && pwd)"
    exec docker run --rm -e SENTINEL_IN_CONTAINER=1 -e SENTINEL_IMAGE="$image" \
        -e DEB="/pkg/$(basename "$deb")" -v "$(dirname "$deb"):/pkg:ro" -v "$repo:/src:ro" \
        "$image" sh /src/scripts/test-deb-docker.sh
fi

IMAGE="${SENTINEL_IMAGE:-?}"
fail() { echo "!! $*"; echo "RÉSULTAT PAQUET $IMAGE : ÉCHEC ($*)"; exit 1; }
ok() { echo "ok  $*"; }
export DEBIAN_FRONTEND=noninteractive
. /etc/os-release
echo "=== $PRETTY_NAME — $(basename "$DEB")"
apt-get update -qq >/dev/null 2>&1
apt-get upgrade -y -qq >/dev/null 2>&1 || fail "mise à jour du conteneur"
apt-get install -y -qq sudo passwd xvfb xauth >/dev/null 2>&1 || fail "outils de test"

echo "=== 1. installation du paquet (apt résout les dépendances)"
apt-get install -y "$DEB" > /tmp/apt.log 2>&1 || { tail -20 /tmp/apt.log; fail "installation"; }
ok "$(sentinel --version)"
for f in /usr/sbin/sentinel-pam /usr/bin/sentinel-setup /usr/lib/systemd/system/sentineld.service \
         /etc/pam.d/sentinel-confirm /usr/share/sentinel/models/face_recognition_sface_2021dec.onnx; do
    [ -e "$f" ] || fail "fichier manquant : $f"
done
PAMMOD="$(dpkg -L sentinel | grep 'pam_sentinel.so$')"
[ -e "$PAMMOD" ] || fail "module PAM absent"
ok "fichiers en place (module : $PAMMOD)"

echo "=== 2. activation et vrai sudo"
useradd -m alice && echo 'alice:Bon-Mdp-42' | chpasswd && usermod -aG sudo alice
sentinel-pam sync --sudo on --screen off >/dev/null || fail "sentinel-pam sync"
[ "$(grep -c pam_sentinel /etc/pam.d/sudo)" = 2 ] || fail "activation PAM"
SENTINEL_CONFIG=/etc/sentinel/config.toml /opt/sentinel/venv/bin/sentineld > /tmp/d.log 2>&1 &
for i in $(seq 120); do grep -q prêt /tmp/d.log 2>/dev/null && break; sleep 0.25; done
grep -q prêt /tmp/d.log || { cat /tmp/d.log; fail "démarrage du service"; }
su alice -c "echo Bon-Mdp-42 | sudo -S -k -p '' true" 2>/dev/null || fail "sudo, bon mot de passe"
su alice -c "echo faux | sudo -S -k -p '' true" 2>/dev/null && fail "sudo a accepté un mauvais mot de passe"
ok "sudo : bon mot de passe accepté, mauvais refusé"

echo "=== 2b. mise à jour (réinstallation du paquet) : Sentinel reste actif"
apt-get install --reinstall -y "$DEB" > /tmp/up.log 2>&1 || { tail -20 /tmp/up.log; fail "réinstallation"; }
[ "$(grep -c pam_sentinel /etc/pam.d/sudo)" = 2 ] || fail "mise à jour : Sentinel désactivé dans PAM"
su alice -c "echo Bon-Mdp-42 | sudo -S -k -p '' true" 2>/dev/null || fail "sudo après mise à jour"
ok "après mise à jour : toujours activé dans PAM, sudo fonctionne"

echo "=== 3. assistant graphique"
GTK_IM_MODULE=gtk-im-context-simple GSK_RENDERER=cairo GDK_BACKEND=x11 timeout 60 \
    xvfb-run -a -s "-screen 0 1280x1024x24" sentinel-setup --demo --snapshot /tmp/snap > /tmp/gui.log 2>&1 || true
pages=$(grep '^page: ' /tmp/gui.log | cut -d' ' -f2 | sort -u | tr '\n' ' ')
for p in welcome capture verify confirm done; do
    echo "$pages" | grep -qw "$p" || { tail -15 /tmp/gui.log; fail "page $p de l'assistant"; }
done
grep -qE "Traceback|Segmentation" /tmp/gui.log && { tail -15 /tmp/gui.log; fail "erreur dans l'assistant"; }
ok "pages : $pages"

echo "=== 4. retrait du paquet"
pkill -f sentineld || true
apt-get remove -y sentinel > /tmp/rm.log 2>&1 || { tail -20 /tmp/rm.log; fail "retrait"; }
grep -q pam_sentinel /etc/pam.d/sudo && fail "PAM non restauré : sudo référence un module supprimé"
[ -e "$PAMMOD" ] && fail "module PAM resté"
su alice -c "echo Bon-Mdp-42 | sudo -S -k -p '' true" 2>/dev/null || fail "sudo cassé après le retrait"
ok "PAM restauré, sudo fonctionne toujours au mot de passe"

echo "=== 5. purge"
apt-get purge -y sentinel >/dev/null 2>&1 || fail "purge"
[ -e /var/lib/sentinel ] && fail "données de visage restantes après la purge"
[ -e /opt/sentinel ] && fail "fichiers restants dans /opt/sentinel"
ok "données et fichiers supprimés"

echo "RÉSULTAT PAQUET $IMAGE : OK"
