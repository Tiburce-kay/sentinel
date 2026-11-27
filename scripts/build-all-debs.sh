#!/bin/sh
# Fabrique LE paquet .deb, puis le teste tel quel sur chaque distribution prise en charge.
#   scripts/build-all-debs.sh            -> dist/sentinel_<version>-1_amd64.deb + dist/rapport.txt
set -u
cd "$(dirname "$0")/.."
IMAGES="${IMAGES:-ubuntu:22.04 ubuntu:24.04 debian:12 debian:13 kalilinux/kali-rolling:latest}"
# Distributions trop anciennes : apt doit REFUSER le paquet.
REFUSED="${REFUSED:-ubuntu:20.04 debian:11}"
mkdir -p dist
: > dist/rapport.txt
scripts/build-deb.sh > dist/fabrication.log 2>&1
deb="$(grep -o 'sentinel_[^ ]*\.deb' dist/fabrication.log | tail -1)"
if [ -z "$deb" ]; then
    echo "FABRICATION : ÉCHEC (voir dist/fabrication.log)" | tee -a dist/rapport.txt
    exit 1
fi
echo "PAQUET $deb ($(du -h "dist/$deb" | cut -f1))" >> dist/rapport.txt
for image in $IMAGES; do
    log="dist/test-$(echo "$image" | tr '/:' '__').log"
    scripts/test-deb-docker.sh "$image" "dist/$deb" > "$log" 2>&1
    grep -h "^RÉSULTAT" "$log" >> dist/rapport.txt || echo "RÉSULTAT PAQUET $image : ÉCHEC (voir $log)" >> dist/rapport.txt
done
# Écran de verrouillage de Plasma 5 (Kubuntu 22.04) : vrai kcheckpass, vrai protocole.
scripts/test-kcheckpass-docker.sh "dist/$deb" > dist/test-kcheckpass.log 2>&1
grep -h "^RÉSULTAT" dist/test-kcheckpass.log >> dist/rapport.txt || echo "RÉSULTAT KCHECKPASS : ÉCHEC (voir dist/test-kcheckpass.log)" >> dist/rapport.txt

# Sur un PC sans GNOME (KDE…), installer Sentinel ne doit tirer ni GNOME ni GDM :
# apt installe les recommandations par défaut (bug de la 0.2.4, signalé sous Kubuntu).
pulled="$(docker run --rm -v "$PWD/dist:/pkg:ro" ubuntu:24.04 sh -c "apt-get update -qq >/dev/null 2>&1;
    apt-get install -s /pkg/$deb 2>/dev/null" | grep -E '^Inst (gnome-shell|gdm3|mutter|gnome-session|sddm|lightdm|plasma)' | cut -d' ' -f2 | tr '\n' ' ')"
if [ -n "$pulled" ]; then
    echo "BUREAU NON TIRÉ : ÉCHEC (installerait : $pulled)" >> dist/rapport.txt
else
    echo "BUREAU NON TIRÉ : OK (ni GNOME ni écran de connexion installés)" >> dist/rapport.txt
fi
for image in $REFUSED; do
    if docker run --rm -v "$PWD/dist:/pkg:ro" "$image" sh -c "apt-get update -qq >/dev/null 2>&1;
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq /pkg/$deb >/dev/null 2>&1"; then
        echo "REFUS $image : ÉCHEC (installé alors que non pris en charge)" >> dist/rapport.txt
    else
        echo "REFUS $image : OK (apt refuse l'installation)" >> dist/rapport.txt
    fi
done
cat dist/rapport.txt
