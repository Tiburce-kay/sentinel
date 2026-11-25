from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"


@pytest.fixture
def models_dir() -> Path:
    if not (MODELS / "face_detection_yunet_2023mar.onnx").exists():
        pytest.skip("modèles absents : lancer scripts/fetch_models.sh")
    return MODELS


def unit(seed: int, dim: int = 128) -> np.ndarray:
    v = np.random.default_rng(seed).normal(size=dim).astype(np.float32)
    return v / np.linalg.norm(v)
