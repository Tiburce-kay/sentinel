"""Chargement vérifié des modèles d'IA.

Un modèle remplacé par un attaquant pourrait accepter n'importe quel visage. Chaque
modèle est donc lu en mémoire, son empreinte SHA-256 comparée au manifeste, puis ce
sont exactement ces octets vérifiés qui sont chargés : aucun intervalle entre la
vérification et l'utilisation pendant lequel le fichier pourrait être échangé.
"""

from __future__ import annotations

import hashlib
import hmac
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 (Ubuntu 22.04, Mint 21…)
    import tomli as tomllib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path


class ModelError(Exception):
    pass


@dataclass(frozen=True)
class ModelSpec:
    role: str
    file: str
    sha256: str
    url: str
    license: str
    source: str


def manifest() -> dict[str, ModelSpec]:
    text = resources.files("sentinel").joinpath("models.toml").read_text("utf-8")
    data = tomllib.loads(text)
    return {role: ModelSpec(role=role, **spec) for role, spec in data.items()}


def load_verified(models_dir: Path, role: str) -> bytes:
    specs = manifest()
    if role not in specs:
        raise ModelError(f"modèle inconnu : {role}")
    spec = specs[role]
    path = models_dir / spec.file
    try:
        blob = path.read_bytes()
    except FileNotFoundError:
        raise ModelError(
            f"modèle absent : {path} (lancer scripts/fetch_models.sh)"
        ) from None
    digest = hashlib.sha256(blob).hexdigest()
    if not hmac.compare_digest(digest, spec.sha256):
        raise ModelError(f"empreinte invalide pour {path} : modèle refusé")
    return blob


def recognizer_fingerprint() -> str:
    """Empreinte du modèle de reconnaissance. Les gabarits y sont liés : une empreinte
    de visage calculée par un autre modèle n'est pas comparable."""
    return manifest()["recognizer"].sha256
