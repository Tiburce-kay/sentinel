#!/bin/sh
# Télécharge les modèles d'IA et vérifie leur empreinte SHA-256.
# Usage : scripts/fetch_models.sh [dossier]   (défaut : ./models)
set -eu
dest="${1:-models}"
manifest="$(dirname "$0")/../src/sentinel/models.toml"
mkdir -p "$dest"
awk -F' = ' '/^file/{f=$2} /^sha256/{s=$2} /^url/{gsub(/"/,"",f); gsub(/"/,"",s); u=$2; gsub(/"/,"",u); print f, s, u}' "$manifest" |
while read -r file sha url; do
    if [ -f "$dest/$file" ] && echo "$sha  $dest/$file" | sha256sum -c --status; then
        echo "ok      $file"; continue
    fi
    echo "télécharge $file"
    curl -fsSL -o "$dest/$file.part" "$url"
    if echo "$sha  $dest/$file.part" | sha256sum -c --status; then
        mv "$dest/$file.part" "$dest/$file"; echo "vérifié $file"
    else
        rm -f "$dest/$file.part"; echo "ERREUR : empreinte invalide pour $file" >&2; exit 1
    fi
done
