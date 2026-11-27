#!/bin/sh
# Vérifie qu'une version de GNOME Shell contient tout ce dont l'extension de Sentinel a
# besoin, AVANT de l'ajouter à metadata.json (procédure : docs/MAINTENANCE.md).
#
#   scripts/check-gnome-compat.sh                    GNOME Shell installé sur ce PC
#   scripts/check-gnome-compat.sh /chemin/libshell-NN.so   bibliothèque d'une autre version
#
# Lit le code JavaScript embarqué dans GNOME Shell (ressources GResource) et cherche
# chaque élément utilisé par extension.js. Un « manquant » signifie que l'extension
# doit être adaptée ; tout « présent » ne remplace pas l'essai réel en machine virtuelle.
set -u
LIB="${1:-}"
if [ -z "$LIB" ]; then
    LIB="$(ls /usr/lib/gnome-shell/libshell-*.so /usr/lib/*/gnome-shell/libshell-*.so /usr/lib64/gnome-shell/libshell-*.so 2>/dev/null | head -1)"
fi
[ -f "$LIB" ] || { echo "bibliothèque de GNOME Shell introuvable (libshell-*.so)"; exit 2; }
command -v gresource >/dev/null || { echo "outil gresource manquant : sudo apt install libglib2.0-bin"; exit 2; }

META="$(dirname "$0")/../packaging/gnome-shell-extension/sentinel@tiburce-kay.github.io/metadata.json"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
gresource extract "$LIB" /org/gnome/shell/gdm/authPrompt.js > "$TMP/authPrompt.js" 2>/dev/null
gresource extract "$LIB" /org/gnome/shell/extensions/extension.js > "$TMP/extension.js" 2>/dev/null

missing=0
check() {  # fichier, motif (regex étendue), description
    if grep -Eq "$2" "$TMP/$1"; then
        echo "présent   $3"
    else
        echo "MANQUANT  $3"
        missing=$((missing + 1))
    fi
}
check extension.js 'export (class|\{[^}]*)\bInjectionManager\b|class InjectionManager' "InjectionManager (extensions/extension.js)"
check authPrompt.js 'export (const|class) AuthPrompt|export \{[^}]*AuthPrompt' "classe AuthPrompt (gdm/authPrompt.js)"
for m in begin setQuestion setMessage finish reset; do
    check authPrompt.js "^[[:space:]]*(async )?$m\(" "méthode AuthPrompt.$m()"
done
check authPrompt.js 'this\._entry\b' "champ du mot de passe this._entry"
check authPrompt.js 'this\._inputWell\b' "conteneur this._inputWell (bouton clé)"
check authPrompt.js 'userName' "paramètre userName de begin()"

if command -v gnome-shell >/dev/null && [ -z "${1:-}" ]; then
    major="$(gnome-shell --version | grep -o '[0-9]\+' | head -1)"
    if grep -q "\"$major\"" "$META"; then
        echo "présent   GNOME $major déclaré dans metadata.json"
    else
        echo "À AJOUTER GNOME $major absent de metadata.json (shell-version)"
    fi
fi
echo
if [ "$missing" -eq 0 ]; then
    echo "Compatible d'après le code de GNOME Shell. Étape suivante : essai réel en machine virtuelle."
else
    echo "$missing élément(s) manquant(s) : adapter extension.js avant de déclarer cette version."
fi
exit "$missing"
