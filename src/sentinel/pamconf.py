"""Activation de Sentinel dans la configuration PAM d'un service (sudo, écran…).

    sentinel-pam status
    sentinel-pam enable sudo
    sentinel-pam disable sudo

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
SERVICES = ("sudo", "sudo-i", "polkit-1", "gdm-password")
PAM_DIR = Path("/etc/pam.d")
INCLUDE = re.compile(r"^\s*@include\s+common-auth\s*$")
FACE_LINE = f"auth\tsufficient\tpam_sentinel.so\t\t{MARK}"
NOTIFY_LINE = f"auth\toptional\tpam_sentinel.so notify\t{MARK}"


class PamConfError(Exception):
    pass


def is_enabled(text: str) -> bool:
    return any("pam_sentinel.so" in line for line in text.splitlines())


def enable(text: str) -> str:
    if is_enabled(text):
        return text
    lines = text.splitlines()
    idx = [i for i, line in enumerate(lines) if INCLUDE.match(line)]
    if len(idx) != 1:
        raise PamConfError("disposition non prise en charge : « @include common-auth » attendu une seule fois")
    i = idx[0]
    out = lines[:i] + [FACE_LINE, lines[i], NOTIFY_LINE] + lines[i + 1:]
    return "\n".join(out) + "\n"


def disable(text: str) -> str:
    lines = [line for line in text.splitlines() if not ("pam_sentinel.so" in line and MARK in line)]
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
    new = enable(text) if on else disable(text)
    if new == text:
        return "déjà activé" if on else "déjà désactivé"
    backup = path.with_name(f"{service}.sentinel-backup")
    if on and not backup.exists():
        backup.write_text(text)
        os.chmod(backup, path.stat().st_mode & 0o777)
    _write(path, new)
    return "activé" if on else "désactivé"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sentinel-pam", description="Active Sentinel dans PAM (root).")
    ap.add_argument("action", choices=("status", "enable", "disable"))
    ap.add_argument("service", nargs="?", choices=SERVICES)
    ap.add_argument("--pam-dir", type=Path, default=PAM_DIR, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.action == "status":
        for svc in SERVICES:
            p = args.pam_dir / svc
            state = ("activé" if is_enabled(p.read_text()) else "désactivé") if p.exists() else "absent"
            print(f"{svc:<14}{state}")
        return 0
    if not args.service:
        ap.error("préciser le service, par exemple : sudo")
    if os.geteuid() != 0 and args.pam_dir == PAM_DIR:
        print("À lancer avec sudo.", file=sys.stderr)
        return 2
    try:
        print(f"{args.service} : {apply(args.service, args.action == 'enable', args.pam_dir)}")
    except PamConfError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
