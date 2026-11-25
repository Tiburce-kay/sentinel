"""Configuration de Sentinel.

En production, la configuration est lue dans /etc/sentinel/config.toml, qui doit
appartenir à root et ne pas être modifiable par les autres : sinon n'importe quel
utilisateur pourrait baisser les seuils de sécurité. En développement, la variable
SENTINEL_CONFIG peut désigner un autre fichier.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("/etc/sentinel/config.toml")


class ConfigError(Exception):
    pass


@dataclass
class CameraConfig:
    # "auto" : première webcam USB (pilote uvcvideo). Sinon un chemin /dev/videoN.
    device: str = "auto"
    width: int = 640
    height: int = 480
    # Refuser les caméras virtuelles (v4l2loopback…) et non USB.
    require_uvc: bool = True
    # Exiger que la caméra soit celle utilisée lors de l'enrôlement.
    pin_identity: bool = True
    # Images ignorées à l'ouverture, le temps que l'exposition automatique se règle.
    warmup_frames: int = 5


@dataclass
class RecognitionConfig:
    # Similarité cosinus minimale (SFace). OpenCV propose 0,363 pour un usage
    # général ; Sentinel est plus strict par défaut.
    threshold: float = 0.50
    # Nombre d'images concordantes exigées pour accepter.
    required_frames: int = 3


@dataclass
class LivenessConfig:
    enabled: bool = True
    # Écart minimal logit(réel) - logit(usurpation) pour juger une image « vivante ».
    threshold: float = 0.5
    # Facteur d'agrandissement du cadre du visage (identique à l'entraînement du modèle).
    bbox_expansion: float = 1.5


@dataclass
class QualityConfig:
    min_face_px: int = 90           # largeur minimale du visage, en pixels
    min_sharpness: float = 40.0     # variance du laplacien
    min_brightness: float = 40.0
    max_brightness: float = 220.0
    max_roll_deg: float = 20.0      # inclinaison de la tête tolérée


@dataclass
class AuthConfig:
    timeout_s: float = 4.0
    # Après ce nombre d'échecs consécutifs, le visage est refusé jusqu'à la
    # prochaine authentification par mot de passe.
    max_failures: int = 5


@dataclass
class EnrollConfig:
    samples: int = 8
    timeout_s: float = 30.0


@dataclass
class Config:
    data_dir: Path = Path("/var/lib/sentinel")
    models_dir: Path = Path("/usr/share/sentinel/models")
    # Vérifie que les fichiers sensibles appartiennent à root et sont protégés.
    strict_permissions: bool = True
    camera: CameraConfig = field(default_factory=CameraConfig)
    recognition: RecognitionConfig = field(default_factory=RecognitionConfig)
    liveness: LivenessConfig = field(default_factory=LivenessConfig)
    quality: QualityConfig = field(default_factory=QualityConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    enroll: EnrollConfig = field(default_factory=EnrollConfig)


def _apply(obj, values: dict, where: str) -> None:
    known = {f.name: f for f in fields(obj)}
    for key, value in values.items():
        if key not in known:
            raise ConfigError(f"option inconnue : {where}{key}")
        current = getattr(obj, key)
        if is_dataclass(current):
            if not isinstance(value, dict):
                raise ConfigError(f"{where}{key} doit être une section")
            _apply(current, value, f"{where}{key}.")
        elif isinstance(current, Path):
            setattr(obj, key, Path(value))
        elif isinstance(current, bool):
            if not isinstance(value, bool):
                raise ConfigError(f"{where}{key} doit valoir true ou false")
            setattr(obj, key, value)
        elif isinstance(current, (int, float)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ConfigError(f"{where}{key} doit être un nombre")
            setattr(obj, key, type(current)(value))
        else:
            setattr(obj, key, value)


def check_secure_file(path: Path) -> None:
    """Refuse un fichier qui n'appartient pas à root ou que d'autres peuvent modifier."""
    st = path.stat()
    if st.st_uid != 0:
        raise ConfigError(f"{path} doit appartenir à root")
    if st.st_mode & 0o022:
        raise ConfigError(f"{path} ne doit être modifiable que par root")


def load_config(path: Path | None = None) -> Config:
    if path is None:
        env = os.environ.get("SENTINEL_CONFIG")
        path = Path(env) if env else DEFAULT_CONFIG_PATH
    cfg = Config()
    if path.exists():
        with path.open("rb") as fh:
            try:
                values = tomllib.load(fh)
            except tomllib.TOMLDecodeError as exc:
                raise ConfigError(f"{path} : {exc}") from exc
        _apply(cfg, values, "")
        if cfg.strict_permissions:
            check_secure_file(path)
    return cfg
