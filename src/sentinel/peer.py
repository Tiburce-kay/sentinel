"""Qui appelle le service ? Identité fournie par le noyau et par logind.

- SO_PEERCRED : l'identifiant (uid) et le processus (pid) de l'appelant, garantis
  par le noyau ; impossible à falsifier depuis l'espace utilisateur.
- logind : l'appelant fait-il partie d'une session active, assise devant la machine
  (seat) ? Cela empêche un utilisateur connecté à distance (SSH) ou en arrière-plan
  d'allumer la caméra.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import pwd
import socket
import struct
from dataclasses import dataclass


@dataclass(frozen=True)
class Peer:
    pid: int
    uid: int
    gid: int

    @property
    def is_root(self) -> bool:
        return self.uid == 0

    @property
    def username(self) -> str:
        return pwd.getpwuid(self.uid).pw_name


def peer_of(sock: socket.socket) -> Peer:
    data = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    pid, uid, gid = struct.unpack("3i", data)
    return Peer(pid, uid, gid)


class _Logind:
    def __init__(self):
        name = ctypes.util.find_library("systemd")
        self.lib = ctypes.CDLL(name) if name else None
        self.libc = ctypes.CDLL(ctypes.util.find_library("c"))
        if self.lib:
            self.lib.sd_pid_get_session.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
            self.lib.sd_session_is_active.argtypes = [ctypes.c_char_p]
            self.lib.sd_session_is_remote.argtypes = [ctypes.c_char_p]
            self.lib.sd_session_get_seat.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
            self.lib.sd_session_get_uid.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint)]
            self.lib.sd_uid_get_sessions.argtypes = [ctypes.c_uint, ctypes.c_int,
                                                     ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))]
            self.lib.sd_uid_get_display.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)]
            for fn in ("sd_session_get_class", "sd_session_get_type", "sd_session_get_state"):
                getattr(self.lib, fn).argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
            self.libc.free.argtypes = [ctypes.c_void_p]

    def _take(self, ptr: ctypes.c_void_p) -> str:
        """Lit une chaîne allouée par libsystemd, puis la libère."""
        if not ptr.value:
            return ""
        value = ctypes.string_at(ptr.value).decode(errors="replace")
        self.libc.free(ptr.value)
        return value

    def session_of(self, pid: int) -> str | None:
        if not self.lib:
            return None
        out = ctypes.c_void_p()
        if self.lib.sd_pid_get_session(pid, ctypes.byref(out)) < 0:
            return None
        return self._take(out)

    def display_session(self, uid: int) -> str | None:
        """Session graphique principale de l'utilisateur (repli utilisé aussi par polkit,
        car sous GNOME les applications tournent hors de la session logind)."""
        if not self.lib:
            return None
        out = ctypes.c_void_p()
        if self.lib.sd_uid_get_display(uid, ctypes.byref(out)) < 0:
            return None
        return self._take(out)

    def is_active_local(self, session: str, uid: int) -> bool:
        s = session.encode()
        owner = ctypes.c_uint()
        if self.lib.sd_session_get_uid(s, ctypes.byref(owner)) < 0 or owner.value != uid:
            return False
        if self.lib.sd_session_is_active(s) <= 0 or self.lib.sd_session_is_remote(s) > 0:
            return False
        seat = ctypes.c_void_p()
        if self.lib.sd_session_get_seat(s, ctypes.byref(seat)) < 0:
            return False
        return bool(self._take(seat))

    def _prop(self, fn: str, session: str) -> str:
        out = ctypes.c_void_p()
        if getattr(self.lib, fn)(session.encode(), ctypes.byref(out)) < 0:
            return ""
        return self._take(out)

    def sessions(self, uid: int) -> list[dict]:
        """Sessions de l'utilisateur : classe, type, état."""
        if not self.lib:
            return []
        arr = ctypes.POINTER(ctypes.c_void_p)()
        n = self.lib.sd_uid_get_sessions(uid, 0, ctypes.byref(arr))
        names = []
        if n > 0 and arr:
            i = 0
            while arr[i]:
                names.append(ctypes.string_at(arr[i]).decode(errors="replace"))
                self.libc.free(arr[i])
                i += 1
        if arr:
            self.libc.free(ctypes.cast(arr, ctypes.c_void_p).value)
        return [{"name": s, "class": self._prop("sd_session_get_class", s),
                 "type": self._prop("sd_session_get_type", s),
                 "state": self._prop("sd_session_get_state", s)} for s in names]


def has_graphical_session(sessions: list[dict]) -> bool:
    """Une vraie session graphique ouverte (et non en cours de fermeture) ? C'est ce
    qui distingue un déverrouillage d'une nouvelle connexion. Les sessions « manager »
    (gestionnaire de services de l'utilisateur), qui peuvent survivre brièvement à une
    déconnexion, ne comptent pas."""
    return any(s["class"] == "user" and s["type"] in ("wayland", "x11") and s["state"] != "closing"
               for s in sessions)


_logind = _Logind()


def in_active_local_session(peer: Peer) -> bool:
    """Vrai si l'appelant appartient à une session active, locale, assise sur un seat."""
    session = _logind.session_of(peer.pid) or _logind.display_session(peer.uid)
    return bool(session) and _logind.is_active_local(session, peer.uid)


def user_has_session(uid: int) -> bool:
    return has_graphical_session(_logind.sessions(uid))
