#!/bin/sh
# Régénère les captures de l'assistant (docs/images) dans un écran virtuel.
set -eu
here="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
GSK_RENDERER=cairo GDK_BACKEND=x11 PYTHONPATH="$here/src" \
    xvfb-run -a -s "-screen 0 1024x768x24" /usr/bin/python3 -m sentinel.gui --demo --snapshot "$tmp" >/dev/null 2>&1
mkdir -p "$here/docs/images"
for page in welcome capture confirm done; do
    cp "$(ls "$tmp"/*-"$page".png | tail -1)" "$here/docs/images/assistant-$page.png"
done
rm -rf "$tmp"
echo "captures mises à jour dans docs/images/"
