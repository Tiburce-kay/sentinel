#!/bin/sh
# Fabrique le paquet .deb de Sentinel pour une distribution, dans un conteneur jetable.
#
#   scripts/build-deb.sh ubuntu:24.04        -> dist/sentinel_0.2.0-1~ubuntu24.04_amd64.deb
#
# Le paquet embarque son environnement Python (onnxruntime, OpenCV… absents des dépôts
# Debian/Ubuntu) : il est donc propre à la version de Python de la distribution visée,
# d'où un paquet par distribution. Les modèles d'IA (licences MIT et Apache-2.0) sont
# inclus et vérifiés par leur empreinte SHA-256.
set -eu

if [ "${SENTINEL_IN_CONTAINER:-}" != 1 ]; then
    image="${1:?usage : scripts/build-deb.sh IMAGE (ex. ubuntu:24.04)}"
    repo="$(cd "$(dirname "$0")/.." && pwd)"
    mkdir -p "$repo/dist"
    exec docker run --rm -e SENTINEL_IN_CONTAINER=1 -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
        -v "$repo:/src:ro" -v "$repo/dist:/dist" "$image" sh /src/scripts/build-deb.sh
fi

. /etc/os-release
case "$ID" in
    ubuntu|debian) TAG="$ID$VERSION_ID" ;;
    *) TAG="$ID" ;;   # distributions en mise à jour continue (Kali…)
esac
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null
apt-get install -y -qq --no-install-recommends python3 python3-venv gcc make libpam0g-dev \
    dpkg-dev ca-certificates curl >/dev/null

VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' /src/pyproject.toml)"
PKGVER="$VERSION-1~$TAG"
PYMINOR="$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
PYNEXT="$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1] + 1}")')"
W=/tmp/work
STAGE=/tmp/stage
rm -rf "$W" "$STAGE"
mkdir -p "$W" "$STAGE/DEBIAN"
cp -r /src/pyproject.toml /src/README.md /src/src /src/pam /src/packaging /src/scripts "$W/"

echo "== Python $PYMINOR, paquet $PKGVER"
# Environnement Python construit à son emplacement définitif (chemins absolus).
python3 -m venv /opt/sentinel/venv
/opt/sentinel/venv/bin/pip install -q --upgrade pip
/opt/sentinel/venv/bin/pip install -q --no-cache-dir "$W"
find /opt/sentinel/venv -name '__pycache__' -prune -exec rm -rf {} +
mkdir -p "$STAGE/opt/sentinel"
cp -a /opt/sentinel/venv "$STAGE/opt/sentinel/"
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
PAMDIR="$(dirname "$(dpkg -L libpam-modules | grep '/pam_unix.so$' | head -1)")"
install -D -m 0644 "$W/pam/pam_sentinel.so" "$STAGE$PAMDIR/pam_sentinel.so"

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
cat > "$STAGE/usr/share/doc/sentinel/THIRD-PARTY" <<'TXT'
Modèles d'IA inclus :
  YuNet (OpenCV Zoo, Shiqi Yu)                       licence MIT
  SFace (OpenCV Zoo)                                 licence Apache-2.0
  MiniFASNet V2 SE (facenox/face-antispoof-onnx)    licence Apache-2.0
Bibliothèques Python incluses dans /opt/sentinel/venv : voir leurs métadonnées
(numpy, opencv-python-headless, onnxruntime, cryptography…).
TXT

# Scripts de maintenance et fichiers de configuration protégés.
install -m 0755 "$W/packaging/deb/postinst" "$W/packaging/deb/prerm" "$W/packaging/deb/postrm" "$STAGE/DEBIAN/"
printf '/etc/sentinel/config.toml\n/etc/pam.d/sentinel-confirm\n' > "$STAGE/DEBIAN/conffiles"

# pkexec : paquet « pkexec » récent, ou « policykit-1 » sur les distributions plus anciennes.
PKEXEC_DEP="pkexec | policykit-1"
SIZE="$(du -sk "$STAGE" --exclude=DEBIAN | cut -f1)"
cat > "$STAGE/DEBIAN/control" <<CTRL
Package: sentinel
Version: $PKGVER
Architecture: amd64
Maintainer: Tiburce-kay <Tiburce-kay@users.noreply.github.com>
Installed-Size: $SIZE
Depends: python3 (>= $PYMINOR), python3 (<< $PYNEXT), python3-gi, python3-gi-cairo, gir1.2-gtk-4.0, gir1.2-adw-1, libpam0g, systemd, $PKEXEC_DEP
Recommends: gnome-shell
Suggests: tpm2-tools
Section: admin
Priority: optional
Homepage: https://github.com/Tiburce-kay/sentinel
Description: authentification faciale locale, résistante à l'usurpation
 Sentinel déverrouille sudo, l'écran de verrouillage et la session au démarrage
 par reconnaissance faciale, avec une webcam ordinaire : détection du vivant par
 IA, défi adaptatif (tourner la tête) et contrôle du relief 3D contre les photos
 et vidéos. Tout reste sur la machine : service sans accès réseau, aucune image
 conservée, empreintes chiffrées dont la clé est scellée dans la puce TPM 2.0.
 Le mot de passe reste toujours utilisable.
CTRL

OUT="/dist/sentinel_${PKGVER}_amd64.deb"
dpkg-deb --build --root-owner-group -Zxz "$STAGE" "$OUT" >/dev/null
chown "${HOST_UID:-0}:${HOST_GID:-0}" "$OUT"
echo "== paquet : $(basename "$OUT") ($(du -h "$OUT" | cut -f1))"
