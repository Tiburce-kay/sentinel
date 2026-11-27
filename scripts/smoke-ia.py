"""Fait réellement tourner les trois modèles d'IA du paquet installé (test des paquets).

Lancé avec le Python du service : /opt/sentinel/venv/bin/python /src/scripts/smoke-ia.py
Vérifie que les bibliothèques embarquées (OpenCV, onnxruntime, numpy) se chargent et
calculent sur cette distribution, sans webcam.
"""
import sys
from pathlib import Path

import numpy as np

from sentinel.config import LivenessConfig
from sentinel.vision import Detector, Face, Recognizer, TextureLiveness

models = Path("/usr/share/sentinel/models")
frame = np.random.default_rng(0).integers(0, 255, (480, 640, 3), dtype=np.uint8)
faces = Detector(models).detect(frame)
face = Face(np.array([220, 140, 200, 200, 270, 210, 370, 210, 320, 260, 280, 300, 360, 300, 0.99], dtype=np.float32))
emb = Recognizer(models).embed(frame, face)
live = TextureLiveness(models, LivenessConfig()).score(frame, face)
assert emb.shape == (128,) and np.isfinite(emb).all() and abs(float(np.linalg.norm(emb)) - 1) < 1e-3, emb
assert np.isfinite(live)
print(f"IA : détection ({len(faces)} visage sur bruit), empreinte 128-d, vivant {live:+.2f}")
sys.exit(0)
