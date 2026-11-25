"""Stockage chiffré et scellé des gabarits de visage.

Ce qui est stocké : des empreintes numériques (vecteurs de 128 valeurs), jamais
d'image. Elles sont chiffrées en AES-256-GCM. Les données authentifiées associées
(AAD) contiennent le nom de l'utilisateur et l'empreinte du modèle : déplacer le
gabarit d'un compte vers un autre (attaque « Face Swap ») ou en fabriquer un sans
la clé (« Faceplant ») est détecté et refusé.

Limite connue : la clé est un fichier lisible par root uniquement. Un attaquant déjà
root peut donc la lire ; le scellement de la clé dans la puce TPM est prévu pour
relever ce niveau.
"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .models import recognizer_fingerprint

FORMAT = "sentinel-template"
VERSION = 1
USERNAME_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}\$?")
MAX_TEMPLATES = 64


class StoreError(Exception):
    pass


class TamperedError(StoreError):
    """Le gabarit a été modifié, déplacé ou fabriqué : il est refusé."""


def validate_username(user: str) -> str:
    if not USERNAME_RE.fullmatch(user):
        raise StoreError(f"nom d'utilisateur invalide : {user!r}")
    return user


@dataclass
class Enrollment:
    user: str
    embeddings: np.ndarray                 # (n, 128) float32, normalisées
    camera: str = ""                       # identité de la caméra d'enrôlement
    liveness_threshold: float | None = None  # seuil personnel issu de la calibration
    stats: dict = field(default_factory=dict)
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"), validate=True)


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def _read_nofollow(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as fh:
        return fh.read()


class TemplateStore:
    def __init__(self, data_dir: Path, strict_permissions: bool = True):
        self.root = Path(data_dir)
        self.strict = strict_permissions
        self.users_dir = self.root / "users"
        self.state_dir = self.root / "state"
        self.key_path = self.root / "master.key"

    # --- Initialisation et contrôles ------------------------------------------

    def init(self) -> None:
        for d in (self.root, self.users_dir, self.state_dir):
            d.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(d, 0o700)
        if not self.key_path.exists():
            fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(AESGCM.generate_key(bit_length=256))
                fh.flush()
                os.fsync(fh.fileno())
        self.check_permissions()

    def _check(self, path: Path, *, is_dir: bool) -> None:
        st = os.lstat(path)
        kind = "dossier" if is_dir else "fichier"
        if os.path.islink(path):
            raise StoreError(f"{path} est un lien symbolique : refusé")
        if self.strict and st.st_uid != 0:
            raise StoreError(f"{kind} {path} : doit appartenir à root")
        if st.st_uid != os.geteuid() and st.st_uid != 0:
            raise StoreError(f"{kind} {path} : propriétaire inattendu")
        if st.st_mode & 0o077:
            raise StoreError(f"{kind} {path} : accessible à d'autres utilisateurs (droits {oct(st.st_mode & 0o777)})")

    def check_permissions(self) -> None:
        if not self.root.exists():
            raise StoreError(f"{self.root} n'existe pas : lancer « sentinel init »")
        for d in (self.root, self.users_dir, self.state_dir):
            self._check(d, is_dir=True)
        self._check(self.key_path, is_dir=False)

    def _key(self) -> bytes:
        self._check(self.key_path, is_dir=False)
        key = _read_nofollow(self.key_path)
        if len(key) != 32:
            raise StoreError("clé de chiffrement invalide")
        return key

    def _path(self, user: str) -> Path:
        return self.users_dir / f"{validate_username(user)}.tpl"

    @staticmethod
    def _aad(user: str) -> bytes:
        return f"{FORMAT}-v{VERSION}|{user}|{recognizer_fingerprint()}".encode()

    # --- Gabarits -------------------------------------------------------------

    def save(self, enr: Enrollment) -> None:
        self.check_permissions()
        emb = np.asarray(enr.embeddings, dtype=np.float32)
        if emb.ndim != 2 or emb.shape[1] != 128 or not 0 < emb.shape[0] <= MAX_TEMPLATES:
            raise StoreError("gabarits invalides")
        payload = json.dumps({
            "embeddings": _b64(emb.tobytes()),
            "count": int(emb.shape[0]),
            "camera": enr.camera,
            "liveness_threshold": enr.liveness_threshold,
            "stats": enr.stats,
            "created": enr.created,
            "updated": time.time(),
        }).encode()
        nonce = secrets.token_bytes(12)
        ct = AESGCM(self._key()).encrypt(nonce, payload, self._aad(enr.user))
        doc = {"format": FORMAT, "version": VERSION, "user": enr.user,
               "nonce": _b64(nonce), "ciphertext": _b64(ct)}
        _write_atomic(self._path(enr.user), json.dumps(doc).encode())

    def load(self, user: str) -> Enrollment | None:
        path = self._path(user)
        if not path.exists():
            return None
        self._check(path, is_dir=False)
        try:
            doc = json.loads(_read_nofollow(path))
            if doc.get("format") != FORMAT or doc.get("version") != VERSION:
                raise TamperedError(f"{path} : format inconnu")
            if doc.get("user") != user:
                raise TamperedError(f"{path} : gabarit d'un autre utilisateur")
            payload = AESGCM(self._key()).decrypt(
                _unb64(doc["nonce"]), _unb64(doc["ciphertext"]), self._aad(user)
            )
        except InvalidTag:
            raise TamperedError(
                f"{path} : gabarit modifié, déplacé, ou lié à un autre modèle : refusé"
            ) from None
        except (ValueError, KeyError, TypeError) as exc:
            raise TamperedError(f"{path} : illisible ({exc})") from None
        data = json.loads(payload)
        emb = np.frombuffer(_unb64(data["embeddings"]), dtype=np.float32).reshape(data["count"], 128)
        return Enrollment(user=user, embeddings=emb.copy(), camera=data["camera"],
                          liveness_threshold=data.get("liveness_threshold"),
                          stats=data.get("stats", {}),
                          created=data["created"], updated=data["updated"])

    def remove(self, user: str) -> bool:
        path = self._path(user)
        state = self.state_dir / f"{validate_username(user)}.json"
        state.unlink(missing_ok=True)
        if path.exists():
            path.unlink()
            return True
        return False

    def users(self) -> list[str]:
        if not self.users_dir.exists():
            return []
        return sorted(p.stem for p in self.users_dir.glob("*.tpl") if USERNAME_RE.fullmatch(p.stem))

    # --- Compteur d'échecs (verrouillage après trop d'essais) -----------------

    def _state_path(self, user: str) -> Path:
        return self.state_dir / f"{validate_username(user)}.json"

    def failures(self, user: str) -> int:
        path = self._state_path(user)
        if not path.exists():
            return 0
        try:
            return int(json.loads(_read_nofollow(path)).get("failures", 0))
        except (ValueError, OSError):
            # État illisible : on considère le compte comme verrouillé par prudence.
            return 1 << 30

    def set_failures(self, user: str, count: int) -> None:
        _write_atomic(self._state_path(user), json.dumps({"failures": count, "at": time.time()}).encode())
