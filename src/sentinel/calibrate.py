"""Calibration : mesure des scores sur le matériel réel, sans conserver d'image.

Chaque séance enregistre, image par image, uniquement des nombres (similarité, score
de vivant, qualité) avec une étiquette décrivant ce qui était devant la caméra :

    reel           l'utilisateur enrôlé, vivant (présentation authentique)
    autre          une autre personne vivante (imposteur pour la reconnaissance)
    tout autre nom une attaque par présentation : photo-ecran, photo-papier, video…

Le rapport calcule les taux d'erreur de la norme ISO/IEC 30107-3 :
    APCER  part des attaques acceptées       (sécurité)
    BPCER  part des présentations authentiques refusées (confort)
à l'échelle de l'image, puis de la règle de décision réelle (k images consécutives).
"""

from __future__ import annotations

import csv
import re
import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .auth import Engine
from .vision import best_similarity, largest

LABEL_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
FIELDS = ["label", "session", "frame", "face", "quality", "similarity", "liveness"]


@dataclass
class Row:
    label: str
    session: str
    frame: int
    face: bool
    quality: str          # "ok" ou la raison du refus
    similarity: float     # nan si pas de visage exploitable
    liveness: float


def record(engine: Engine, frames: Iterable[np.ndarray], templates: np.ndarray, label: str) -> list[Row]:
    if not LABEL_RE.fullmatch(label):
        raise ValueError(f"étiquette invalide : {label!r}")
    session = time.strftime("%Y%m%d-%H%M%S")
    rows: list[Row] = []
    for i, frame in enumerate(frames):
        face = largest(engine.detector.detect(frame))
        if face is None:
            rows.append(Row(label, session, i, False, "aucun visage", float("nan"), float("nan")))
            continue
        q = engine._quality(frame, face)
        sim = best_similarity(engine.recognizer.embed(frame, face), templates) if templates.size else float("nan")
        live = engine.liveness.score(frame, face) if engine.liveness is not None else float("nan")
        rows.append(Row(label, session, i, True, "ok" if q.ok else q.reason, sim, live))
    return rows


def append(path: Path, rows: list[Row]) -> None:
    new = not path.exists()
    with path.open("a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(FIELDS)
        for r in rows:
            w.writerow([r.label, r.session, r.frame, int(r.face), r.quality, f"{r.similarity:.5f}", f"{r.liveness:.5f}"])


def load(path: Path) -> list[Row]:
    with path.open(newline="") as fh:
        return [Row(d["label"], d["session"], int(d["frame"]), d["face"] == "1", d["quality"],
                    float(d["similarity"]), float(d["liveness"])) for d in csv.DictReader(fh)]


# --- Analyse -------------------------------------------------------------------

def _sequences(rows: list[Row]) -> list[list[Row]]:
    """Regroupe par séance, dans l'ordre des images."""
    by = defaultdict(list)
    for r in rows:
        by[(r.label, r.session)].append(r)
    return [sorted(v, key=lambda r: r.frame) for v in by.values()]


def window_accept_rate(rows: list[Row], sim_t: float, live_t: float, k: int) -> float:
    """Part des fenêtres de k images consécutives qui seraient ACCEPTÉES par la règle
    de décision (chaque image : visage de qualité, similarité ≥ sim_t, vivant ≥ live_t)."""
    windows = accepted = 0
    for seq in _sequences(rows):
        good = [r.face and r.quality == "ok" and r.similarity >= sim_t and r.liveness >= live_t for r in seq]
        for i in range(len(good) - k + 1):
            windows += 1
            accepted += all(good[i:i + k])
    return accepted / windows if windows else float("nan")


def _pct(values: np.ndarray) -> str:
    if values.size == 0:
        return "—"
    p = np.percentile(values, [0, 5, 50, 95, 100])
    return "  ".join(f"{x:6.2f}" for x in p)


def report(rows: list[Row], sim_t: float, live_t: float, k: int) -> str:
    out: list[str] = []
    labels = sorted({r.label for r in rows})
    out.append("Distribution par étiquette (min  p5  médiane  p95  max) :")
    for lab in labels:
        rs = [r for r in rows if r.label == lab and r.face and r.quality == "ok"]
        n_all = sum(1 for r in rows if r.label == lab)
        live = np.array([r.liveness for r in rs if np.isfinite(r.liveness)])
        sim = np.array([r.similarity for r in rs if np.isfinite(r.similarity)])
        out.append(f"\n  {lab}  ({len(rs)} images exploitables / {n_all})")
        out.append(f"    vivant     {_pct(live)}")
        out.append(f"    similarité {_pct(sim)}")

    bona = [r for r in rows if r.label == "reel" and r.face and r.quality == "ok"]
    attacks = [r for r in rows if r.label not in ("reel", "autre") and r.face and r.quality == "ok"]
    if bona and attacks:
        out.append("\nTaux d'erreur à l'échelle de l'image, selon le seuil de vivant :")
        out.append("    seuil   BPCER (vrai refusé)   APCER (attaque acceptée)")
        bl = np.array([r.liveness for r in bona])
        al = np.array([r.liveness for r in attacks])
        for t in np.arange(-1.0, 3.01, 0.25):
            mark = "  ← actuel" if abs(t - live_t) < 1e-9 else ""
            out.append(f"    {t:5.2f}   {np.mean(bl < t):18.1%}   {np.mean(al >= t):22.1%}{mark}")

    out.append(f"\nRègle de décision réelle ({k} images consécutives, similarité ≥ {sim_t}, vivant ≥ {live_t}) :")
    for lab in labels:
        rate = window_accept_rate([r for r in rows if r.label == lab], sim_t, live_t, k)
        kind = "acceptées (souhaité ~100 %)" if lab == "reel" else "acceptées (doit être 0 %)"
        out.append(f"    {lab:<14} {rate:7.1%} des fenêtres {kind}")
    return "\n".join(out)
