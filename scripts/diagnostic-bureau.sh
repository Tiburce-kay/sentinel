#!/bin/sh
# Diagnostic du bureau, pour préparer la prise en charge de nouveaux bureaux (KDE…).
# LECTURE SEULE : ne modifie rien, ne demande pas sudo, n'affiche aucun mot de passe.
# Les fichiers PAM affichés ne contiennent que des noms de modules, lisibles par tous.
#
#   sh diagnostic-bureau.sh > diagnostic.txt
echo "== Système"
grep -E '^(PRETTY_NAME|VERSION_ID)=' /etc/os-release
echo "noyau : $(uname -r)   architecture : $(uname -m)"
echo
echo "== Bureau"
echo "XDG_CURRENT_DESKTOP=${XDG_CURRENT_DESKTOP:-?}  XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-?}"
command -v plasmashell >/dev/null && plasmashell --version 2>/dev/null
command -v gnome-shell >/dev/null && gnome-shell --version 2>/dev/null
echo "écran de connexion : $(cat /etc/X11/default-display-manager 2>/dev/null || echo '?')"
for dm in sddm plasmalogin plasma-login-manager gdm3 lightdm; do
    systemctl is-active --quiet "$dm" 2>/dev/null && echo "service actif : $dm"
done
echo
echo "== Paquets utiles"
dpkg-query -W -f='${Package} ${Version} ${Status}\n' kscreenlocker libkscreenlocker5 libkscreenlocker6 \
    sddm plasma-login-manager kwallet-pam libpam-kwallet5 libpam-kwallet6 libpam-fprintd \
    gdm3 gnome-shell sentinel 2>/dev/null | grep 'install ok installed' | cut -d' ' -f1,2
echo
echo "== Fichiers PAM des écrans de connexion et de verrouillage"
ls /etc/pam.d | grep -E '^(kde|sddm|plasma|gdm|lightdm|login|sudo)' | tr '\n' ' '
echo
for f in kde kde-fingerprint kde-smartcard sddm sddm-greeter sddm-autologin plasmalogin \
         plasma-login plasma-login-manager; do
    if [ -f "/etc/pam.d/$f" ]; then
        echo "--- /etc/pam.d/$f"
        grep -v '^[[:space:]]*#' "/etc/pam.d/$f" | grep -v '^[[:space:]]*$'
    fi
done
echo
echo "== Modules PAM disponibles"
for m in pam_kwallet5.so pam_kwallet.so pam_gnome_keyring.so pam_fprintd.so pam_sentinel.so; do
    p="$(ls /usr/lib/x86_64-linux-gnu/security/$m /lib/x86_64-linux-gnu/security/$m 2>/dev/null | head -1)"
    echo "$m : ${p:-absent}"
done
echo
echo "== Fin du diagnostic"
