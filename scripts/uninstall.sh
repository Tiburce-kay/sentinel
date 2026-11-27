#!/bin/sh
# Désinstalle Sentinel. Les données de visage sont SUPPRIMÉES, sauf avec --keep-data.
#   sudo scripts/uninstall.sh [--keep-data]
set -eu
[ "$(id -u)" -eq 0 ] || { echo "À lancer avec sudo : sudo $0"; exit 1; }

# D'abord PAM : plus aucun service ne doit appeler le module.
if [ -x /opt/sentinel/venv/bin/sentinel-pam ]; then
    for svc in sudo sudo-i polkit-1 gdm-password; do
        [ -e "/etc/pam.d/$svc" ] && /opt/sentinel/venv/bin/sentinel-pam disable "$svc" || true
    done
fi
if [ -d /run/systemd/system ]; then
    systemctl disable --now sentineld >/dev/null 2>&1 || true
fi
rm -f /etc/systemd/system/sentineld.service
rm -rf /etc/systemd/system/sentineld.service.d
[ -d /run/systemd/system ] && systemctl daemon-reload || true
for d in /usr/lib/x86_64-linux-gnu/security /lib/x86_64-linux-gnu/security \
         /usr/lib/aarch64-linux-gnu/security /lib/security /usr/lib/security /usr/lib64/security; do
    rm -f "$d/pam_sentinel.so"
done
rm -f /etc/pam.d/sentinel-confirm /usr/local/bin/sentinel /usr/local/sbin/sentinel-pam \
      /usr/local/bin/sentinel-setup /usr/share/applications/sentinel-setup.desktop
if [ -e /usr/share/gdm/dconf/95-sentinel ]; then
    rm -f /usr/share/gdm/dconf/95-sentinel /usr/share/gdm/dconf/locks/95-sentinel
    [ -x /usr/share/gdm/generate-config ] && /usr/share/gdm/generate-config >/dev/null 2>&1 || true
fi
rm -rf /opt/sentinel /usr/share/sentinel /usr/share/gnome-shell/extensions/sentinel@tiburce-kay.github.io
if [ "${1:-}" = "--keep-data" ]; then
    echo "Données conservées : /var/lib/sentinel, /etc/sentinel et la clé (/etc/credstore.encrypted)"
else
    rm -rf /var/lib/sentinel /etc/sentinel
    # La clé n'a de sens qu'avec les données : supprimée avec elles, jamais sans.
    rm -f /etc/credstore.encrypted/sentinel-master-key /etc/credstore.encrypted/sentinel-master-key.mode
    echo "Données de visage et clé de chiffrement supprimées."
fi
echo "Sentinel est désinstallé."
