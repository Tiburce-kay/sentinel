#!/bin/sh
# Test d'installation de bout en bout dans un conteneur jetable (sans systemd ni caméra) :
# installation, activation pour le VRAI sudo, mot de passe correct puis faux, désinstallation.
#   scripts/test-install-docker.sh [image]      (défaut : kalilinux/kali-rolling)
# Le conteneur reçoit le dépôt en lecture seule.
set -eu
if [ "${SENTINEL_IN_CONTAINER:-}" != 1 ]; then
    image="${1:-kalilinux/kali-rolling:latest}"
    repo="$(cd "$(dirname "$0")/.." && pwd)"
    exec docker run --rm -e SENTINEL_IN_CONTAINER=1 -v "$repo:/src:ro" "$image" sh /src/scripts/test-install-docker.sh
fi
set -eu
cp -r /src /tmp/repo && cd /tmp/repo && rm -rf .venv dev-data
apt-get update -qq >/dev/null && apt-get install -y -qq sudo passwd >/dev/null
sh scripts/install.sh 2>&1 | grep -vE '^\s*$' | tail -12
echo "=== utilisateur de test"
useradd -m alice && echo 'alice:Bon-Mdp-42' | chpasswd && usermod -aG sudo alice
echo "=== activation pour sudo"
sentinel-pam enable sudo
grep -n sentinel /etc/pam.d/sudo
echo "=== démarrage du service (root, configuration de production)"
SENTINEL_CONFIG=/etc/sentinel/config.toml /opt/sentinel/venv/bin/sentineld > /tmp/d.log 2>&1 &
for i in $(seq 80); do grep -q prêt /tmp/d.log 2>/dev/null && break; sleep 0.25; done
ls -la /var/lib/sentinel /run/sentinel
echo "=== sudo avec le BON mot de passe (alice non enrôlée : le module doit rester silencieux)"
su alice -c "echo Bon-Mdp-42 | sudo -S -k -p '[invite mot de passe] ' true" && echo "-> sudo ACCEPTÉ"
echo "=== sudo avec un MAUVAIS mot de passe"
su alice -c "echo faux | sudo -S -k -p '[invite mot de passe] ' true" 2>&1 | head -3 || true
echo "=== journal du service"
cat /tmp/d.log
echo "=== désinstallation"
kill %1 2>/dev/null || pkill -f sentineld || true
sh scripts/uninstall.sh
grep -c sentinel /etc/pam.d/sudo || echo "plus aucune ligne sentinel dans /etc/pam.d/sudo"
ls /opt/sentinel /var/lib/sentinel 2>&1 | head -2
