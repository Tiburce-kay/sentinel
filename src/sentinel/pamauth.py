"""Vérification d'un mot de passe par PAM (bibliothèque système, via ctypes).

Le service PAM « sentinel-confirm » n'utilise QUE pam_unix (voir packaging/) : la
confirmation d'un enregistrement de visage ne peut donc jamais être faite… par le
visage lui-même.

Limite : en Python, le mot de passe ne peut pas être effacé de la mémoire de façon
garantie (chaînes immuables). Il n'y transite que le temps de la vérification et
n'est jamais écrit ni journalisé.
"""

from __future__ import annotations

import ctypes
import ctypes.util

PAM_SUCCESS = 0
PAM_PROMPT_ECHO_OFF = 1
PAM_PROMPT_ECHO_ON = 2

SERVICE = "sentinel-confirm"


class _Message(ctypes.Structure):
    _fields_ = [("msg_style", ctypes.c_int), ("msg", ctypes.c_char_p)]


class _Response(ctypes.Structure):
    _fields_ = [("resp", ctypes.c_void_p), ("resp_retcode", ctypes.c_int)]


_CONV = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.POINTER(_Message)),
                         ctypes.POINTER(ctypes.POINTER(_Response)), ctypes.c_void_p)


class _Conv(ctypes.Structure):
    _fields_ = [("conv", _CONV), ("appdata_ptr", ctypes.c_void_p)]


_libpam = ctypes.CDLL(ctypes.util.find_library("pam") or "libpam.so.0")
_libc = ctypes.CDLL(ctypes.util.find_library("c"))
_libc.calloc.restype = ctypes.c_void_p
_libc.calloc.argtypes = [ctypes.c_size_t, ctypes.c_size_t]
_libc.strdup.restype = ctypes.c_void_p
_libc.strdup.argtypes = [ctypes.c_char_p]
_libpam.pam_start.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(_Conv),
                              ctypes.POINTER(ctypes.c_void_p)]
_libpam.pam_authenticate.argtypes = [ctypes.c_void_p, ctypes.c_int]
_libpam.pam_acct_mgmt.argtypes = [ctypes.c_void_p, ctypes.c_int]
_libpam.pam_end.argtypes = [ctypes.c_void_p, ctypes.c_int]


def check_password(user: str, password: str, service: str = SERVICE) -> bool:
    secret = password.encode()

    @_CONV
    def conv(n, messages, responses, _data):
        arr = _libc.calloc(n, ctypes.sizeof(_Response))
        if not arr:
            return 5  # PAM_BUF_ERR
        resp = ctypes.cast(arr, ctypes.POINTER(_Response))
        for i in range(n):
            if messages[i].contents.msg_style in (PAM_PROMPT_ECHO_OFF, PAM_PROMPT_ECHO_ON):
                resp[i].resp = _libc.strdup(secret)  # libpam libère cette copie
        responses[0] = resp
        return PAM_SUCCESS

    handle = ctypes.c_void_p()
    c = _Conv(conv, None)
    if _libpam.pam_start(service.encode(), user.encode(), ctypes.byref(c), ctypes.byref(handle)) != PAM_SUCCESS:
        return False
    rc = _libpam.pam_authenticate(handle, 0)
    if rc == PAM_SUCCESS:
        rc = _libpam.pam_acct_mgmt(handle, 0)
    _libpam.pam_end(handle, rc)
    return rc == PAM_SUCCESS
