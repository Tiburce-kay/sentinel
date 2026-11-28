"""Mesure de recherche : un visage sombre devient-il « vivant » une fois éclairci, sans
qu'une photo ou une vidéo le deviennent aussi ?

Pour chaque image où un visage est détecté, calcule le score de vivant :
- tel quel ;
- après éclaircissement du visage (correction gamma vers une luminosité cible).
Aucune image n'est enregistrée : seulement des nombres, dans un CSV.

    .venv/bin/python scripts/mesure_lumiere.py ETIQUETTE [secondes]
    .venv/bin/python scripts/mesure_lumiere.py --rapport
ETIQUETTE : reel-clair, reel-sombre, photo, video…
"""
import csv
import sys
import time
from pathlib import Path

import numpy as np

from sentinel.camera import open_camera
from sentinel.config import Config
from sentinel.vision import Detector, TextureLiveness

OUT = Path("mesures-lumiere.csv")
TARGET = 110.0      # luminosité visée pour le visage (0-255)
K = 10              # fenêtre de décision


def face_brightness(frame, face) -> float:
    x, y, w, h = face.box
    roi = frame[max(y, 0):y + h, max(x, 0):x + w]
    return float(roi.mean()) if roi.size else 0.0


def brighten(frame, mean: float):
    """Correction gamma qui amène la luminosité du visage vers TARGET (n'assombrit jamais)."""
    if mean <= 1 or mean >= TARGET:
        return frame
    g = float(np.clip(np.log(TARGET / 255) / np.log(mean / 255), 0.35, 1.0))
    lut = (255 * (np.arange(256) / 255) ** g).clip(0, 255).astype(np.uint8)
    return lut[frame]


def record(label: str, seconds: float) -> None:
    cfg = Config()
    models = Path("models") if Path("models").exists() else cfg.models_dir
    det, live = Detector(models), TextureLiveness(models, cfg.liveness)
    rows = []
    print(f"Mesure « {label} » pendant {seconds:.0f} s : placez la présentation devant la caméra…")
    with open_camera(cfg.camera) as cam:
        for frame in cam.frames(seconds):
            faces = det.detect(frame)
            if not faces:
                continue
            face = max(faces, key=lambda f: f.area)
            b = face_brightness(frame, face)
            rows.append((label, round(b, 1), round(live.score(frame, face), 3),
                         round(live.score(brighten(frame, b), face), 3)))
    new = not OUT.exists()
    with OUT.open("a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["etiquette", "lumiere_visage", "vivant_brut", "vivant_eclairci"])
        w.writerows(rows)
    print(f"{len(rows)} images avec visage enregistrées (nombres seulement) dans {OUT}")


def windows(xs):
    return [float(np.mean(xs[i:i + K])) for i in range(len(xs) - K + 1)]


def report() -> None:
    data: dict[str, list] = {}
    with OUT.open() as fh:
        for r in csv.DictReader(fh):
            data.setdefault(r["etiquette"], []).append(r)
    print(f"{'étiquette':<14}{'images':>7}{'lumière':>9}   {'fenêtre brute (min/méd/max)':<30}{'fenêtre éclaircie (min/méd/max)'}")
    for label, rows in data.items():
        lum = np.median([float(r["lumiere_visage"]) for r in rows])
        out = [f"{label:<14}{len(rows):>7}{lum:>9.0f}   "]
        for key in ("vivant_brut", "vivant_eclairci"):
            ws = windows([float(r[key]) for r in rows])
            out.append(f"{min(ws):+.2f} / {np.median(ws):+.2f} / {max(ws):+.2f}".ljust(30) if ws else "—".ljust(30))
        print("".join(out))


if __name__ == "__main__":
    if sys.argv[1:2] == ["--rapport"]:
        report()
    else:
        record(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 15)
