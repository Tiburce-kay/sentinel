"""Briques de vision : détection, reconnaissance, détection du vivant, qualité.

Toutes les images manipulées ici restent en mémoire : Sentinel n'écrit jamais
d'image de visage sur le disque.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .config import LivenessConfig, QualityConfig
from .models import load_verified


@dataclass(frozen=True)
class Face:
    """Un visage détecté par YuNet (coordonnées en pixels, image BGR)."""

    raw: np.ndarray  # ligne brute YuNet (15 valeurs), utilisée pour l'alignement

    @property
    def box(self) -> tuple[int, int, int, int]:
        x, y, w, h = self.raw[:4]
        return int(x), int(y), int(w), int(h)

    @property
    def score(self) -> float:
        return float(self.raw[14])

    @property
    def eyes(self) -> tuple[tuple[float, float], tuple[float, float]]:
        # YuNet : œil droit de la personne (à gauche de l'image), puis œil gauche.
        return (float(self.raw[4]), float(self.raw[5])), (float(self.raw[6]), float(self.raw[7]))

    @property
    def roll_deg(self) -> float:
        (xr, yr), (xl, yl) = self.eyes
        return math.degrees(math.atan2(yl - yr, xl - xr))

    @property
    def area(self) -> int:
        _, _, w, h = self.box
        return max(w, 0) * max(h, 0)


def _buffer(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.uint8)


class Detector:
    """Détection sur une image réduite (320 px de large : 6 fois plus rapide sur un
    processeur modeste), coordonnées ramenées ensuite à la taille d'origine."""

    def __init__(self, models_dir: Path, score_threshold: float = 0.8, max_width: int = 320):
        self._max_width = max_width
        self._net = cv2.FaceDetectorYN.create(
            "onnx", _buffer(load_verified(models_dir, "detector")),
            np.array([], np.uint8), (320, 240), score_threshold, 0.3, 50,
        )
        self._size: tuple[int, int] | None = None

    def detect(self, frame_bgr: np.ndarray) -> list[Face]:
        h, w = frame_bgr.shape[:2]
        scale = min(1.0, self._max_width / w)
        img = frame_bgr
        if scale < 1.0:
            img = cv2.resize(frame_bgr, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
        ih, iw = img.shape[:2]
        if self._size != (iw, ih):
            self._net.setInputSize((iw, ih))
            self._size = (iw, ih)
        _, faces = self._net.detect(img)
        if faces is None:
            return []
        out = []
        for row in faces:
            row = row.copy()
            row[:14] /= scale
            out.append(Face(row))
        return out


def largest(faces: list[Face]) -> Face | None:
    return max(faces, key=lambda f: f.area) if faces else None


class Recognizer:
    """Calcule l'empreinte d'un visage (vecteur de 128 valeurs, norme 1) avec SFace."""

    DIM = 128

    def __init__(self, models_dir: Path):
        self._net = cv2.FaceRecognizerSF.create(
            "onnx", _buffer(load_verified(models_dir, "recognizer")), np.array([], np.uint8)
        )

    def embed(self, frame_bgr: np.ndarray, face: Face) -> np.ndarray:
        aligned = self._net.alignCrop(frame_bgr, face.raw)
        feat = self._net.feature(aligned).reshape(-1).astype(np.float32)
        return normalize(feat)


def normalize(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    if not math.isfinite(n) or n == 0.0:
        raise ValueError("empreinte de visage invalide")
    return (v / n).astype(np.float32)


def best_similarity(embedding: np.ndarray, templates: np.ndarray) -> float:
    """Plus forte similarité cosinus entre une empreinte et les gabarits enrôlés."""
    if templates.size == 0:
        return -1.0
    return float(np.max(templates @ embedding))


# --- Détection du vivant par la texture (MiniFASNet) -------------------------

def square_crop(img: np.ndarray, box: tuple[int, int, int, int], expansion: float) -> np.ndarray:
    """Découpe carrée centrée sur le visage, agrandie, bords complétés par réflexion.

    Reproduit exactement le prétraitement utilisé à l'entraînement du modèle."""
    x, y, w, h = box
    if w <= 0 or h <= 0:
        raise ValueError("cadre de visage invalide")
    H, W = img.shape[:2]
    size = int(max(w, h) * expansion)
    cx, cy = x + w / 2, y + h / 2
    x0, y0 = int(cx - size / 2), int(cy - size / 2)
    x1, y1 = x0 + size, y0 + size
    inner = img[max(0, y0):min(H, y1), max(0, x0):min(W, x1)]
    if inner.size == 0:
        raise ValueError("visage hors de l'image")
    out = cv2.copyMakeBorder(
        inner, max(0, -y0), max(0, y1 - H), max(0, -x0), max(0, x1 - W), cv2.BORDER_REFLECT_101
    )
    if out.shape[:2] != (size, size):
        raise ValueError("découpe du visage incohérente")
    return out


def letterbox_chw(img: np.ndarray, size: int) -> np.ndarray:
    h, w = img.shape[:2]
    ratio = size / max(h, w)
    nh, nw = int(h * ratio), int(w * ratio)
    interp = cv2.INTER_LANCZOS4 if ratio > 1.0 else cv2.INTER_AREA
    img = cv2.resize(img, (nw, nh), interpolation=interp)
    dh, dw = size - nh, size - nw
    img = cv2.copyMakeBorder(img, dh // 2, dh - dh // 2, dw // 2, dw - dw // 2, cv2.BORDER_REFLECT_101)
    return img.transpose(2, 0, 1).astype(np.float32) / 255.0


class TextureLiveness:
    """Distingue une vraie peau d'une photo imprimée ou d'un écran.

    Renvoie l'écart logit(réel) - logit(usurpation) : positif = plutôt vivant."""

    INPUT = 128

    def __init__(self, models_dir: Path, cfg: LivenessConfig):
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        opts.intra_op_num_threads = 2
        self._sess = ort.InferenceSession(
            load_verified(models_dir, "liveness"), opts, providers=["CPUExecutionProvider"]
        )
        self._input = self._sess.get_inputs()[0].name
        self._cfg = cfg

    def score(self, frame_bgr: np.ndarray, face: Face) -> float:
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        crop = square_crop(rgb, face.box, self._cfg.bbox_expansion)
        batch = letterbox_chw(crop, self.INPUT)[None]
        logits = self._sess.run(None, {self._input: batch})[0][0]
        return float(logits[0] - logits[1])


# --- Qualité d'image ---------------------------------------------------------

@dataclass(frozen=True)
class Quality:
    ok: bool
    reason: str = ""


def assess(frame_bgr: np.ndarray, face: Face, cfg: QualityConfig) -> Quality:
    x, y, w, h = face.box
    if w < cfg.min_face_px:
        return Quality(False, "visage trop loin ou trop petit")
    H, W = frame_bgr.shape[:2]
    roi = frame_bgr[max(0, y):min(H, y + h), max(0, x):min(W, x + w)]
    if roi.size == 0:
        return Quality(False, "visage hors de l'image")
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    brightness = float(gray.mean())
    if brightness < cfg.min_brightness:
        return Quality(False, "image trop sombre")
    if brightness > cfg.max_brightness:
        return Quality(False, "image surexposée")
    if float(cv2.Laplacian(gray, cv2.CV_64F).var()) < cfg.min_sharpness:
        return Quality(False, "image floue")
    if abs(face.roll_deg) > cfg.max_roll_deg:
        return Quality(False, "tête trop inclinée")
    return Quality(True)
