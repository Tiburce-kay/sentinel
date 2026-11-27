#!/bin/sh
# Fabrique LE paquet .deb de Sentinel, unique pour les distributions de la famille
# Debian livrées avec GNOME 45 ou plus récent (Ubuntu 24.04+, Debian 13+, Kali…),
# dans un conteneur jetable.
#
#   scripts/build-deb.sh            -> dist/sentinel_<version>-1_amd64.deb
#
# Le service embarque son propre Python (version autonome, épinglée par son empreinte
# SHA-256) et ses bibliothèques d'IA : il ne dépend pas du Python de la distribution.
# Fabriqué sur la plus ANCIENNE distribution prise en charge (Ubuntu 24.04, glibc
# 2.39). Le paquet déclare ce minimum (libc6 >= 2.39) et refuse les GNOME antérieurs
# à 45 (Breaks: gnome-shell << 45) : apt refuse de l'installer sur Ubuntu 22.04,
# Debian 12 ou plus ancien.
set -eu

BUILDER=ubuntu:24.04
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20261124/cpython-3.12.16%2B20261124-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
PY_SHA256=7e1adaea26c959d13aa1120be1ae62a1ea1bb52adcb382679fffe9217e22adff
MIN_GLIBC=2.39

if [ "${SENTINEL_IN_CONTAINER:-}" != 1 ]; then
    image="$BUILDER"
    repo="$(cd "$(dirname "$0")/.." && pwd)"
    mkdir -p "$repo/dist"
    exec docker run --rm -e SENTINEL_IN_CONTAINER=1 -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
        -v "$repo:/src:ro" -v "$repo/dist:/dist" "$image" sh /src/scripts/build-deb.sh
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null
apt-get install -y -qq --no-install-recommends gcc make libpam0g-dev dpkg-dev ca-certificates curl >/dev/null

VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' /src/pyproject.toml)"
PKGVER="$VERSION-1"
W=/tmp/work
STAGE=/tmp/stage
rm -rf "$W" "$STAGE"
mkdir -p "$W" "$STAGE/DEBIAN"
cp -r /src/pyproject.toml /src/README.md /src/LICENSE /src/src /src/pam /src/packaging /src/scripts "$W/"

# Python autonome, vérifié par son empreinte, puis environnement du service.
curl -fsSL -o /tmp/python.tar.gz "$PY_URL"
echo "$PY_SHA256  /tmp/python.tar.gz" | sha256sum -c --status || { echo "ERREUR : empreinte de Python invalide"; exit 1; }
mkdir -p /opt/sentinel
tar -xzf /tmp/python.tar.gz -C /opt/sentinel   # -> /opt/sentinel/python
echo "== $(/opt/sentinel/python/bin/python3 --version), paquet $PKGVER"
/opt/sentinel/python/bin/python3 -m venv /opt/sentinel/venv
/opt/sentinel/venv/bin/pip install -q --upgrade pip
/opt/sentinel/venv/bin/pip install -q --no-cache-dir "$W"
# Allègement : parties de Python inutiles à un service sans interface (tests internes,
# IDLE, Tk), et pip, qui ne sert qu'à l'installation.
P=/opt/sentinel/python/lib/python3.12
rm -rf "$P/test" "$P/idlelib" "$P/tkinter" "$P/turtledemo" "$P/ensurepip" "$P/lib-dynload/_tkinter"* \
       /opt/sentinel/python/lib/tcl* /opt/sentinel/python/lib/tk* /opt/sentinel/python/lib/itcl* \
       /opt/sentinel/python/lib/thread* /opt/sentinel/python/lib/libtcl* /opt/sentinel/python/lib/libtk*
/opt/sentinel/venv/bin/pip uninstall -q -y pip
find /opt/sentinel -name '__pycache__' -prune -exec rm -rf {} +
mkdir -p "$STAGE/opt/sentinel"
cp -a /opt/sentinel/python /opt/sentinel/venv "$STAGE/opt/sentinel/"
# Assistant graphique : code de Sentinel seul, exécuté par le Python du système (GTK).
mkdir -p "$STAGE/opt/sentinel/gui" "$STAGE/opt/sentinel/lib"
cp -r "$W/src/sentinel" "$STAGE/opt/sentinel/gui/sentinel"
find "$STAGE/opt/sentinel/gui" -name '__pycache__' -prune -exec rm -rf {} +
install -m 0755 "$W/packaging/protect-key.sh" "$STAGE/opt/sentinel/lib/protect-key.sh"

# Modèles d'IA, vérifiés.
mkdir -p "$STAGE/usr/share/sentinel/models"
cp /src/models/*.onnx "$STAGE/usr/share/sentinel/models/" 2>/dev/null || true
sh "$W/scripts/fetch_models.sh" "$STAGE/usr/share/sentinel/models"
chmod 0644 "$STAGE/usr/share/sentinel/models/"*.onnx

# Module PAM, compilé pour cette distribution, au même endroit que pam_unix.
make -s -C "$W/pam" pam_sentinel.so
# Chemin unique : avec /usr fusionné (toutes les distributions prises en charge), il
# désigne le même dossier que /lib/x86_64-linux-gnu/security.
install -D -m 0644 "$W/pam/pam_sentinel.so" "$STAGE/usr/lib/x86_64-linux-gnu/security/pam_sentinel.so"

# Fichiers du système.
install -D -m 0644 "$W/packaging/sentineld.service" "$STAGE/usr/lib/systemd/system/sentineld.service"
install -D -m 0644 "$W/packaging/sentinel-confirm.pam" "$STAGE/etc/pam.d/sentinel-confirm"
install -D -m 0644 "$W/packaging/config.toml" "$STAGE/etc/sentinel/config.toml"
install -D -m 0644 "$W/packaging/sentinel-setup.desktop" "$STAGE/usr/share/applications/sentinel-setup.desktop"
EXT=sentinel@tiburce-kay.github.io
install -d "$STAGE/usr/share/gnome-shell/extensions/$EXT"
install -m 0644 "$W/packaging/gnome-shell-extension/$EXT/"* "$STAGE/usr/share/gnome-shell/extensions/$EXT/"
install -D -m 0644 "$W/packaging/gdm-dconf-95-sentinel" "$STAGE/usr/share/gdm/dconf/95-sentinel"
install -D -m 0644 "$W/packaging/gdm-dconf-locks-95-sentinel" "$STAGE/usr/share/gdm/dconf/locks/95-sentinel"

# Commandes.
install -d "$STAGE/usr/bin" "$STAGE/usr/sbin"
ln -s /opt/sentinel/venv/bin/sentinel "$STAGE/usr/bin/sentinel"
ln -s /opt/sentinel/venv/bin/sentinel-pam "$STAGE/usr/sbin/sentinel-pam"
cat > "$STAGE/usr/bin/sentinel-setup" <<'SH'
#!/bin/sh
PYTHONPATH=/opt/sentinel/gui exec /usr/bin/python3 -m sentinel.gui "$@"
SH
chmod 0755 "$STAGE/usr/bin/sentinel-setup"

# Documentation et licences des composants tiers.
install -D -m 0644 "$W/README.md" "$STAGE/usr/share/doc/sentinel/README.md"
# Licence de Sentinel (PolyForm Noncommercial 1.0.0), emplacement habituel de Debian.
install -m 0644 "$W/LICENSE" "$STAGE/usr/share/doc/sentinel/copyright"
cat > "$STAGE/usr/share/doc/sentinel/THIRD-PARTY" <<'TXT'
Sentinel : Copyright (c) 2026 Tiburce-kay, licence PolyForm Noncommercial 1.0.0 (voir copyright).
Usage commercial : licence à obtenir auprès de l'auteur.

Modèles d'IA inclus :
  YuNet (OpenCV Zoo, Shiqi Yu)                       licence MIT
  SFace (OpenCV Zoo)                                 licence Apache-2.0
  MiniFASNet V2 SE (facenox/face-antispoof-onnx)    licence Apache-2.0
Python autonome (/opt/sentinel/python) : python-build-standalone (Astral), CPython sous
licence PSF ; composants inclus et licences : /opt/sentinel/python/share (le cas échéant).
Bibliothèques Python incluses dans /opt/sentinel/venv : voir leurs métadonnées
(numpy, opencv-python-headless, onnxruntime, cryptography…).
TXT

# Scripts de maintenance et fichiers de configuration protégés.
install -m 0755 "$W/packaging/deb/postinst" "$W/packaging/deb/prerm" "$W/packaging/deb/postrm" "$STAGE/DEBIAN/"
printf '/etc/sentinel/config.toml\n/etc/pam.d/sentinel-confirm\n' > "$STAGE/DEBIAN/conffiles"

SIZE="$(du -sk "$STAGE" --exclude=DEBIAN | cut -f1)"
cat > "$STAGE/DEBIAN/control" <<CTRL
Package: sentinel
Version: $PKGVER
Architecture: amd64
Maintainer: Tiburce-kay <Tiburce-kay@users.noreply.github.com>
Installed-Size: $SIZE
Depends: libc6 (>= $MIN_GLIBC), libpam0g, systemd, python3 (>= 3.12), python3-gi, python3-gi-cairo, gir1.2-gtk-4.0, gir1.2-adw-1, pkexec | policykit-1
Breaks: gnome-shell (<< 45)
Suggests: tpm2-tools
Section: admin
Priority: optional
Homepage: https://github.com/Tiburce-kay/sentinel
Description: authentification faciale locale, résistante à l'usurpation
 Sentinel déverrouille sudo, l'écran de verrouillage et la session au démarrage
 par reconnaissance faciale, avec une webcam ordinaire : détection du vivant par
 IA, défi adaptatif (tourner la tête) et contrôle du relief 3D contre les photos
 et vidéos. Tout reste sur la machine : service sans accès réseau, aucune image
 conservée, empreintes chiffrées dont la clé est scellée dans la puce TPM 2.0
 quand le PC en a une (systemd 250 ou plus récent).
 Le mot de passe reste toujours utilisable.
CTRL

OUT="/dist/sentinel_${PKGVER}_amd64.deb"
dpkg-deb --build --root-owner-group -Zxz "$STAGE" "$OUT" >/dev/null
chown "${HOST_UID:-0}:${HOST_GID:-0}" "$OUT"
echo "== paquet : $(basename "$OUT") ($(du -h "$OUT" | cut -f1))"
