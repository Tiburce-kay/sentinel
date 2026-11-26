"""Configuration de Sentinel.

En production, la configuration est lue dans /etc/sentinel/config.toml, qui doit
appartenir à root et ne pas être modifiable par les autres : sinon n'importe quel
utilisateur pourrait baisser les seuils de sécurité. En développement, la variable
SENTINEL_CONFIG peut désigner un autre fichier.
"""

from __future__ import annotations

import os
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 (Ubuntu 22.04, Mint 21…)
    import tomli as tomllib
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
    # Ouverture : images ignorées tant que l'exposition automatique se règle (luminosité
    # stable), au moins `warmup_frames`, au plus `warmup_max_s` secondes.
    warmup_frames: int = 5
    warmup_max_s: float = 1.0


@dataclass
class RecognitionConfig:
    # Similarité cosinus minimale (SFace). OpenCV propose 0,363 pour un usage
    # général ; Sentinel est plus strict par défaut.
    threshold: float = 0.50


@dataclass
class LivenessConfig:
    enabled: bool = True
    # Seuil sur la MOYENNE, sur la fenêtre d'images, de l'écart
    # logit(réel) - logit(usurpation). Image par image, le score est trop bruité
    # sur une webcam ordinaire ; la moyenne sépare nettement vrai visage et attaque.
    # Valeur calibrée sur le matériel réel : voir docs/CALIBRATION.md.
    threshold: float = -1.0
    # Bornes de la calibration automatique à l'enrôlement : le seuil personnel de
    # chaque utilisateur reste toujours entre ces deux valeurs. Le plancher protège
    # contre une adaptation qui rendrait le système plus facile à tromper (pire
    # attaque mesurée : -3,45 ; voir docs/CALIBRATION.md). Provisoire : à valider
    # sur plusieurs webcams et plusieurs personnes.
    # Défi adaptatif : si la moyenne de vivant dépasse le seuil de moins de cette marge,
    # Sentinel demande de tourner la tête (direction tirée au sort) avant d'accepter.
    challenge: bool = True
    challenge_margin: float = 1.0
    # Zone grise sous le seuil : défi plutôt que refus, jusqu'à 1,0 sous le seuil et
    # jamais sous -2,5 (pire fenêtre d'attaque mesurée : -3,45 ; voir CALIBRATION.md).
    challenge_low_margin: float = 1.0
    challenge_floor: float = -2.5
    floor: float = -2.0
    # Plafond = seuil validé : l'enrôlement se fait en conditions idéales (écran blanc,
    # face à la caméra), qui gonflent les scores. Un seuil personnel plus strict que
    # le seuil validé refuserait l'utilisateur au quotidien (mesuré le 25/11 : seuil
    # calculé +0,14, vérification en 3,5 s sur 4). La calibration ne fait donc
    # qu'assouplir, jusqu'au plancher, pour les webcams aux scores bas.
    ceiling: float = -1.0
    calibration_margin: float = 0.5
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
    # Comme Face ID : mot de passe exigé au premier déverrouillage après le démarrage
    # du service (donc du PC), puis à nouveau si le dernier date de plus de N heures.
    # Le mot de passe déverrouille aussi le trousseau GNOME.
    require_password_after_boot: bool = True
    password_every_h: float = 48.0
    # Nombre d'images consécutives exigées : visage de qualité et reconnu sur
    # CHACUNE, et moyenne du score de vivant au-dessus du seuil.
    window_frames: int = 10
    # Au-delà de ces écarts à la position de face (voir geometry.Pose), l'image est
    # ignorée : le modèle de vivant n'est pas fiable de profil (essai réel du 25/11).
    # Abandons anticipés (le mot de passe s'affiche aussitôt) : image trop sombre
    # pendant tant de secondes, ou aucun visage depuis le début de la tentative.
    challenge_timeout_s: float = 3.0
    challenge_after_s: float = 1.5   # délai avant de proposer le défi en zone grise
    give_up_dark_s: float = 1.0
    give_up_no_face_s: float = 1.5
    max_yaw: float = 0.12
    max_pitch: float = 0.08
    # Après ce nombre d'échecs consécutifs, le visage est refusé jusqu'à la
    # prochaine authentification par mot de passe.
    max_failures: int = 5


@dataclass
class PresenceConfig:
    duration_s: float = 0.8   # durée d'une vérification de présence (caméra allumée)


@dataclass
class EnrollConfig:
    per_pose: int = 2               # empreintes retenues par pose (5 poses)
    timeout_s: float = 60.0
    yaw_delta: float = 0.10         # rotation gauche/droite exigée (voir geometry.Pose)
    pitch_delta: float = 0.06       # rotation haut/bas exigée (0,08 : « haut » tout juste atteint)
    # Relief minimal (parallaxe du nez) : une photo inclinée reste plate. Provisoire.
    min_parallax: float = 0.05


@dataclass
class Config:
    data_dir: Path = Path("/var/lib/sentinel")
    models_dir: Path = Path("/usr/share/sentinel/models")
    socket_path: Path = Path("/run/sentinel/sentinel.sock")
    # Vérifie que les fichiers sensibles appartiennent à root et sont protégés.
    strict_permissions: bool = True
    camera: CameraConfig = field(default_factory=CameraConfig)
    recognition: RecognitionConfig = field(default_factory=RecognitionConfig)
    liveness: LivenessConfig = field(default_factory=LivenessConfig)
    quality: QualityConfig = field(default_factory=QualityConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    enroll: EnrollConfig = field(default_factory=EnrollConfig)
    presence: PresenceConfig = field(default_factory=PresenceConfig)


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
