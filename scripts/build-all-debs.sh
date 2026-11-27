#!/bin/sh
# Fabrique LE paquet .deb, puis le teste tel quel sur chaque distribution prise en charge.
#   scripts/build-all-debs.sh            -> dist/sentinel_<version>-1_amd64.deb + dist/rapport.txt
set -u
cd "$(dirname "$0")/.."
IMAGES="${IMAGES:-ubuntu:22.04 ubuntu:24.04 debian:12 debian:13 kalilinux/kali-rolling:latest}"
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
cat dist/rapport.txt
