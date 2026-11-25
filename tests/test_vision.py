import numpy as np
import pytest

from sentinel.config import LivenessConfig, QualityConfig
from sentinel.vision import (Detector, Face, Recognizer, TextureLiveness, assess,
                             letterbox_chw, normalize, square_crop)


def face_at(x, y, w, h, eyes=((0, 0), (10, 0))):
    raw = np.zeros(15, np.float32)
    raw[:4] = [x, y, w, h]
    raw[4:8] = [*eyes[0], *eyes[1]]
    return Face(raw)


def test_decoupe_carree_et_bords():
    img = np.arange(100 * 100 * 3, dtype=np.uint8).reshape(100, 100, 3)
    out = square_crop(img, (80, 80, 30, 20), 1.5)   # déborde de l'image
    assert out.shape == (45, 45, 3)


def test_decoupe_identique_a_la_reference():
    """Même résultat que le prétraitement d'entraînement du modèle (cadre au centre)."""
    img = np.random.default_rng(0).integers(0, 255, (200, 200, 3), dtype=np.uint8)
    out = square_crop(img, (50, 60, 80, 80), 1.5)
    assert np.array_equal(out, img[40:160, 30:150])


def test_letterbox():
    t = letterbox_chw(np.zeros((60, 120, 3), np.uint8), 128)
    assert t.shape == (3, 128, 128) and t.dtype == np.float32 and t.max() <= 1


def test_normalisation_refuse_vecteur_nul():
    with pytest.raises(ValueError):
        normalize(np.zeros(128, np.float32))


def test_qualite():
    cfg = QualityConfig()
    sharp = (np.indices((480, 640)).sum(0) % 2 * 255).astype(np.uint8)
    frame = np.dstack([sharp] * 3)
    frame[...] = np.clip(frame.astype(int) // 2 + 60, 0, 255).astype(np.uint8)
    assert assess(frame, face_at(100, 100, 200, 200), cfg).ok
    assert assess(frame, face_at(100, 100, 50, 50), cfg).reason == "visage trop loin ou trop petit"
    assert assess(np.zeros_like(frame), face_at(100, 100, 200, 200), cfg).reason == "image trop sombre"
    flat = np.full_like(frame, 120)
    assert assess(flat, face_at(100, 100, 200, 200), cfg).reason == "image floue"
    tilted = face_at(100, 100, 200, 200, eyes=((0, 0), (10, 10)))
    assert assess(frame, tilted, cfg).reason == "tête trop inclinée"


def test_modeles_reels_sur_image_vide(models_dir):
    blank = np.zeros((480, 640, 3), np.uint8)
    assert Detector(models_dir).detect(blank) == []
    f = face_at(200, 120, 200, 240, eyes=((260, 200), (340, 200)))
    noise = np.random.default_rng(0).integers(0, 255, (480, 640, 3), dtype=np.uint8)
    score = TextureLiveness(models_dir, LivenessConfig()).score(noise, f)
    assert np.isfinite(score)
    raw = f.raw.copy()
    raw[8:14] = [300, 240, 270, 290, 330, 290]  # nez et coins de la bouche
    e = Recognizer(models_dir).embed(noise, Face(raw))
    assert e.shape == (128,) and abs(np.linalg.norm(e) - 1) < 1e-5
