#!/bin/sh
# Fabrique et teste les paquets .deb pour toutes les distributions prises en charge.
#   scripts/build-all-debs.sh            -> dist/*.deb + dist/rapport.txt
set -u
cd "$(dirname "$0")/.."
IMAGES="${IMAGES:-ubuntu:22.04 ubuntu:24.04 debian:12 debian:13 kalilinux/kali-rolling:latest}"
: > dist/rapport.txt
for image in $IMAGES; do
    log="dist/$(echo "$image" | tr '/:' '__').log"
    scripts/build-deb.sh "$image" > "$log" 2>&1
    deb="$(grep -o 'sentinel_[^ ]*\.deb' "$log" | tail -1)"
    if [ -z "$deb" ]; then
        echo "FABRICATION $image : ÉCHEC (voir $log)" >> dist/rapport.txt
        continue
    fi
    scripts/test-deb-docker.sh "$image" "dist/$deb" >> "$log" 2>&1
    grep -h "^RÉSULTAT" "$log" | sed "s|\$| ($deb)|" >> dist/rapport.txt
done
cat dist/rapport.txt
