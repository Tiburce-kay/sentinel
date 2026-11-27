#!/bin/sh
# Écran de verrouillage de Plasma 5 (Kubuntu 22.04), de bout en bout, sans écran :
# le VRAI kcheckpass de KDE 5.24 (celui qu'appelle l'écran de verrouillage) vérifie un
# mot de passe avec Sentinel installé par le paquet, le service tournant sans caméra.
#
#   scripts/test-kcheckpass-docker.sh dist/sentinel_<version>_amd64.deb
#
# Vérifie : /etc/pam.d/kde créé par le paquet ; kcheckpass (lancé par l'utilisateur,
# comme l'écran de verrouillage) charge le module ; visage impossible -> bon mot de
# passe accepté, mauvais refusé ; pause : le 2e essai ne rallume pas la caméra ;
# login SDDM reçoit la ligne « mot de passe correct » ; retrait : tout est restauré.
set -eu
if [ "${SENTINEL_IN_CONTAINER:-}" != 1 ]; then
    deb="$(cd "$(dirname "${1:?paquet .deb}")" && pwd)/$(basename "$1")"
    repo="$(cd "$(dirname "$0")/.." && pwd)"
    exec docker run --rm -e SENTINEL_IN_CONTAINER=1 -e DEB="/pkg/$(basename "$deb")" \
        -v "$(dirname "$deb"):/pkg:ro" -v "$repo:/src:ro" ubuntu:22.04 sh /src/scripts/test-kcheckpass-docker.sh
fi
fail() { echo "!! $*"; echo "RÉSULTAT KCHECKPASS : ÉCHEC ($*)"; exit 1; }
ok() { echo "ok  $*"; }
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null 2>&1
apt-get install -y -qq --no-install-recommends libkscreenlocker5 sddm passwd >/dev/null 2>&1 || fail "paquets KDE"
KCP="$(dpkg -L libkscreenlocker5 | grep '/kcheckpass$')"
[ -x "$KCP" ] || fail "kcheckpass introuvable"
[ -e /etc/pam.d/kde ] && fail "/etc/pam.d/kde existe déjà : le test n'a pas de sens"
ok "Plasma 5.24 : kcheckpass présent, /etc/pam.d/kde absent (comme sur Kubuntu 22.04)"

apt-get install -y "$DEB" > /tmp/apt.log 2>&1 || { tail -20 /tmp/apt.log; fail "installation"; }
head -1 /etc/pam.d/kde | grep -q "Créé par Sentinel" || fail "/etc/pam.d/kde non créé"
grep -q "pam_sentinel.so" /etc/pam.d/kde || fail "visage absent de /etc/pam.d/kde"
[ "$(grep -c pam_sentinel /etc/pam.d/sddm)" = 1 ] && grep -q "pam_sentinel.so notify" /etc/pam.d/sddm \
    || fail "SDDM : seule la ligne « mot de passe correct » attendue"
ok "paquet : /etc/pam.d/kde créé (copie de other + visage), SDDM : ligne notify seule"

useradd -m alice && echo 'alice:Bon-Mdp-42' | chpasswd
# Service réel avec session simulée et caméra filmant une pièce vide (sentineld_essai.py).
SENTINEL_CONFIG=/etc/sentinel/config.toml /opt/sentinel/venv/bin/python -I /src/scripts/sentineld_essai.py alice > /tmp/d.log 2>&1 &
for i in $(seq 120); do grep -q prêt /tmp/d.log 2>/dev/null && break; sleep 0.25; done
grep -q prêt /tmp/d.log || { cat /tmp/d.log; fail "démarrage du service"; }

# Le vrai protocole de l'écran de verrouillage 5.24 (scripts/kcheckpass_driver.py),
# lancé par l'utilisateur comme l'écran. Le service n'a pas de caméra : le visage échoue
# toujours, c'est le pire cas pour le mot de passe.
drive() { su alice -c "python3 /src/scripts/kcheckpass_driver.py $KCP $*" > /tmp/kcp.out 2>&1; r=$?; cat /tmp/kcp.out | sed 's/^/    /'; return $r; }
echo "--- Entrée à vide, puis mot de passe (scénario réel après un échec du visage)"
drive "''" Bon-Mdp-42 || fail "mot de passe refusé après l'échec du visage"
grep -q "regardez la caméra" /tmp/kcp.out || fail "le visage n'a pas été vérifié au premier essai"
grep -q "essai 1 (champ vide) : refusé en [1-4]" /tmp/kcp.out || fail "durée du premier essai inattendue"
grep -q "essai 2 (mot de passe tapé) : accepté en 0" /tmp/kcp.out || fail "mot de passe pas vérifié immédiatement (pause)"
ok "Entrée à vide : caméra et visage vérifiés ; puis mot de passe accepté immédiatement (pause)"
echo "--- Mauvais mot de passe"
drive faux && fail "mauvais mot de passe accepté" || true
grep -q "refusé" /tmp/kcp.out || fail "refus attendu"
ok "mauvais mot de passe refusé"
echo "--- journal du service"
grep -E "service=kde|pause" /tmp/d.log | cut -c1-150 | sed 's/^/    /'

apt-get remove -y sentinel >/dev/null 2>&1 || fail "retrait"
[ -e /etc/pam.d/kde ] && fail "/etc/pam.d/kde resté après le retrait"
grep -q pam_sentinel /etc/pam.d/sddm && fail "SDDM non restauré"
drive Bon-Mdp-42 || fail "kcheckpass cassé après le retrait"
ok "retrait : /etc/pam.d/kde supprimé, SDDM restauré, kcheckpass fonctionne"
echo "RÉSULTAT KCHECKPASS : OK"
