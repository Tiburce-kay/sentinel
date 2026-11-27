"""Activation de Sentinel dans la configuration PAM d'un service (sudo, écran…).

    sentinel-pam status
    sentinel-pam sync --sudo on|off --screen on|off
    sentinel-pam enable sudo
    sentinel-pam disable sudo

Le paquet branche Sentinel à l'installation (sync on/on). C'est sans effet pour un
compte qui n'a pas enregistré son visage, ou pour un usage qu'il n'a pas choisi : le
service répond « non enregistré » ou « désactivé », et le module se tait. Chaque
utilisateur choisit ses usages avec son seul mot de passe.

Deux lignes, marquées « # sentinel », sont ajoutées autour de l'inclusion du mot de
passe (disposition Debian/Ubuntu : « @include common-auth ») :

    auth  sufficient  pam_sentinel.so          # sentinel   <- visage d'abord
    @include common-auth                                     <- sinon, mot de passe
    auth  optional    pam_sentinel.so notify   # sentinel   <- atteinte seulement si le
                                                               mot de passe était correct

Avec common-auth, un mot de passe faux s'arrête sur « requisite pam_deny » : la ligne
« notify » n'est jamais atteinte. Les autres dispositions (Fedora, Arch…) ne sont pas
encore prises en charge : le programme refuse plutôt que de deviner.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

MARK = "# sentinel"
SERVICES = ("sudo", "sudo-i", "polkit-1", "gdm-password", "kde", "kde-fingerprint")
# Écran de verrouillage de KDE Plasma : « kde » (mot de passe) et, depuis Plasma 6,
# « kde-fingerprint », lancée EN PARALLÈLE dès l'affichage de l'écran, sans attendre
# la saisie. Avec Plasma 6, le visage prend place dans cette pile parallèle, juste
# avant l'empreinte digitale : le mot de passe reste disponible à tout moment.
SCREEN_SERVICES = ("gdm-password", "kde", "kde-fingerprint")
FPRINT = re.compile(r"^\s*-?auth\s+\S+\s+pam_fprintd\.so\b")
# Succès du visage : saute l'empreinte digitale (ligne suivante) ; échec : l'empreinte
# est essayée comme avant.
FINGERPRINT_LINE = f"auth\t[success=1 default=ignore]\tpam_sentinel.so\t\t{MARK}"
PAM_DIR = Path("/etc/pam.d")
INCLUDE = re.compile(r"^\s*@include\s+common-auth\s*$")
FACE_LINE = f"auth\tsufficient\tpam_sentinel.so\t\t{MARK}"
NOTIFY_LINE = f"auth\toptional\tpam_sentinel.so notify\t{MARK}"

# Écran de connexion (gdm-password) : avec « sufficient », un succès du visage
# arrêterait la pile AVANT pam_gnome_keyring, et le trousseau resterait verrouillé.
# Ici, le succès passe par pam_gnome_keyring (qui reçoit le mot de passe rendu par
# Sentinel), puis termine ; un échec saute ces deux lignes et mène au mot de passe.
# En phase d'authentification, un saut ne modifie pas le résultat de la pile : un
# échec du visage ne peut donc pas faire échouer un mot de passe correct.
KEYRING_LINES = [
    f"auth\t[success=ok default=2]\tpam_sentinel.so\t\t{MARK}",
    f"auth\toptional\tpam_gnome_keyring.so\t\t{MARK}",
    f"auth\t[success=done default=die]\tpam_permit.so\t{MARK}",
]
KEYRING_SERVICES = ("gdm-password",)
MODULE_DIRS = ("/usr/lib/x86_64-linux-gnu/security", "/lib/x86_64-linux-gnu/security",
               "/usr/lib/aarch64-linux-gnu/security", "/lib/security", "/usr/lib/security",
               "/usr/lib64/security")


def keyring_module_present() -> bool:
    return any(Path(d, "pam_gnome_keyring.so").exists() for d in MODULE_DIRS)


class PamConfError(Exception):
    pass


def is_enabled(text: str) -> bool:
    return any("pam_sentinel.so" in line for line in text.splitlines())


def enable(text: str, keyring: bool = False) -> str:
    if is_enabled(text):
        return text
    lines = text.splitlines()
    idx = [i for i, line in enumerate(lines) if INCLUDE.match(line)]
    if len(idx) != 1:
        raise PamConfError("disposition non prise en charge : « @include common-auth » attendu une seule fois")
    i = idx[0]
    face = KEYRING_LINES if keyring else [FACE_LINE]
    out = lines[:i] + face + [lines[i], NOTIFY_LINE] + lines[i + 1:]
    return "\n".join(out) + "\n"


def enable_parallel(text: str) -> str:
    """kde-fingerprint (Plasma 6) : visage juste avant pam_fprintd."""
    if is_enabled(text):
        return text
    lines = text.splitlines()
    idx = [i for i, line in enumerate(lines) if FPRINT.match(line)]
    if len(idx) != 1:
        raise PamConfError("disposition non prise en charge : « auth … pam_fprintd.so » attendu une seule fois")
    i = idx[0]
    return "\n".join(lines[:i] + [FINGERPRINT_LINE] + lines[i:]) + "\n"


def enable_notify_only(text: str) -> str:
    """kde (Plasma 6) : la pile du mot de passe ne reçoit que la ligne « mot de passe
    correct » ; le visage est dans la pile parallèle kde-fingerprint."""
    if is_enabled(text):
        return text
    lines = text.splitlines()
    idx = [i for i, line in enumerate(lines) if INCLUDE.match(line)]
    if len(idx) != 1:
        raise PamConfError("disposition non prise en charge : « @include common-auth » attendu une seule fois")
    i = idx[0]
    return "\n".join(lines[:i + 1] + [NOTIFY_LINE] + lines[i + 1:]) + "\n"


def layout(service: str, text: str, pam_dir: Path) -> str:
    """Texte activé pour un service, selon sa nature."""
    if service == "kde-fingerprint":
        return enable_parallel(text)
    if service == "kde" and (pam_dir / "kde-fingerprint").exists():
        return enable_notify_only(text)
    keyring = service in KEYRING_SERVICES and keyring_module_present()
    return enable(text, keyring)


def disable(text: str) -> str:
    lines = [line for line in text.splitlines() if not line.rstrip().endswith(MARK)]
    return "\n".join(lines) + "\n"


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.sentinel-tmp")
    tmp.write_text(text)
    st = path.stat()
    os.chmod(tmp, st.st_mode & 0o777)
    os.chown(tmp, st.st_uid, st.st_gid)
    os.replace(tmp, path)


def apply(service: str, on: bool, pam_dir: Path = PAM_DIR) -> str:
    if service not in SERVICES:
        raise PamConfError(f"service non pris en charge : {service} (possibles : {', '.join(SERVICES)})")
    path = pam_dir / service
    if not path.exists():
        raise PamConfError(f"{path} introuvable")
    text = path.read_text()
    # Réactiver remplace une ancienne disposition par la disposition actuelle.
    new = layout(service, disable(text), pam_dir) if on else disable(text)
    if new == text:
        return "déjà activé" if on else "déjà désactivé"
    backup = path.with_name(f"{service}.sentinel-backup")
    if on and not backup.exists():
        backup.write_text(text)
        os.chmod(backup, path.stat().st_mode & 0o777)
    _write(path, new)
    return "activé" if on else "désactivé"


def status(pam_dir: Path = PAM_DIR) -> dict[str, str]:
    out = {}
    for svc in SERVICES:
        p = pam_dir / svc
        out[svc] = ("activé" if is_enabled(p.read_text()) else "désactivé") if p.exists() else "absent"
    return out


# Désactivation volontaire par un administrateur (« sentinel-pam sync --sudo off
# --screen off ») : retenue ici, pour que les mises à jour du paquet ne réactivent pas
# Sentinel. Le retrait du paquet (--from-package) ne la crée pas.
OPT_OUT = Path("/etc/sentinel/pam-desactive")


def sync(sudo: bool, screen: bool, pam_dir: Path = PAM_DIR) -> dict[str, str]:
    """Applique en une fois les choix de l'assistant : sudo (et sudo -i), écrans de
    connexion et de verrouillage (GNOME : gdm-password ; KDE Plasma : kde et
    kde-fingerprint). Les services absents sont ignorés."""
    wanted = {"sudo": sudo, "sudo-i": sudo, **{svc: screen for svc in SCREEN_SERVICES}}
    done = {}
    for svc, on in wanted.items():
        if not (pam_dir / svc).exists():
            continue
        try:
            done[svc] = apply(svc, on, pam_dir)
        except PamConfError:
            if svc == "sudo-i":
                # Disposition sans common-auth (ex. « @include sudo ») : couverte par sudo.
                done[svc] = "ignoré (inclut la configuration de sudo)"
            elif svc in ("kde", "kde-fingerprint"):
                # Disposition KDE inattendue : on ne devine pas, l'écran reste au mot de passe.
                done[svc] = "ignoré (disposition non prise en charge)"
            else:
                raise
    return done


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sentinel-pam", description="Active Sentinel dans PAM (root).")
    ap.add_argument("action", choices=("status", "enable", "disable", "sync"))
    ap.add_argument("service", nargs="?", choices=SERVICES)
    ap.add_argument("--sudo", choices=("on", "off"), help="sync : visage pour sudo")
    ap.add_argument("--screen", choices=("on", "off"), help="sync : visage pour l'écran de connexion")
    ap.add_argument("--json", action="store_true", help="status : sortie JSON")
    ap.add_argument("--from-package", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--opt-out", type=Path, default=OPT_OUT, help=argparse.SUPPRESS)
    ap.add_argument("--pam-dir", type=Path, default=PAM_DIR, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.action == "status":
        st = status(args.pam_dir)
        if args.json:
            import json
            print(json.dumps(st, ensure_ascii=False))
        else:
            for svc, state in st.items():
                print(f"{svc:<14}{state}")
        return 0
    if os.geteuid() != 0 and args.pam_dir == PAM_DIR:
        print("À lancer avec sudo.", file=sys.stderr)
        return 2
    try:
        if args.action == "sync":
            if args.sudo is None or args.screen is None:
                ap.error("sync demande --sudo on|off et --screen on|off")
            on = (args.sudo == "on", args.screen == "on")
            if args.from_package and any(on) and args.opt_out.exists():
                # Installation ou mise à jour : un administrateur a désactivé Sentinel.
                print(f"Sentinel reste désactivé (voir {args.opt_out}).")
                return 0
            for svc, result in sync(*on, args.pam_dir).items():
                print(f"{svc} : {result}")
            if not args.from_package:
                if any(on):
                    args.opt_out.unlink(missing_ok=True)
                elif args.opt_out.parent.is_dir():
                    args.opt_out.write_text("Sentinel désactivé par un administrateur "
                                            "(sentinel-pam sync --sudo off --screen off).\n")
            return 0
        if not args.service:
            ap.error("préciser le service, par exemple : sudo")
        print(f"{args.service} : {apply(args.service, args.action == 'enable', args.pam_dir)}")
    except PamConfError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
