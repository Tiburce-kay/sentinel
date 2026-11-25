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


def window_means(rows: list[Row], sim_t: float, k: int, attacker: bool = False) -> tuple[int, list[float]]:
    """Fenêtres de k images consécutives : renvoie le nombre total de fenêtres et la
    moyenne de vivant de celles où chaque image est un visage reconnu (similarité ≥
    sim_t) et de qualité. Avec attacker=True, on suppose que l'attaquant parvient à
    satisfaire le contrôle de qualité : c'est le cas le plus défavorable."""
    total, means = 0, []
    for seq in _sequences(rows):
        usable = [r.face and (r.quality == "ok" or (attacker and r.quality != "aucun visage"))
                  and r.similarity >= sim_t for r in seq]
        for i in range(len(seq) - k + 1):
            total += 1
            if all(usable[i:i + k]):
                means.append(float(np.mean([r.liveness for r in seq[i:i + k]])))
    return total, means


def window_accept_rate(rows: list[Row], sim_t: float, live_t: float, k: int, attacker: bool = False) -> float:
    """Part des fenêtres de k images consécutives ACCEPTÉES par la règle de décision."""
    total, means = window_means(rows, sim_t, k, attacker)
    return sum(m >= live_t for m in means) / total if total else float("nan")


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
        out.append("\nPour information, image par image (la décision, elle, porte sur la moyenne) :")
        out.append("    seuil   BPCER (vrai refusé)   APCER (attaque acceptée)")
        bl = np.array([r.liveness for r in bona])
        al = np.array([r.liveness for r in attacks])
        for t in np.arange(-4.0, 3.01, 0.5):
            mark = "  ← seuil actuel" if abs(t - live_t) < 1e-9 else ""
            out.append(f"    {t:5.2f}   {np.mean(bl < t):18.1%}   {np.mean(al >= t):22.1%}{mark}")

    out.append(f"\nRègle de décision réelle ({k} images consécutives reconnues (similarité ≥ {sim_t}),"
               f" moyenne de vivant ≥ {live_t}) :")
    for lab in labels:
        attack = lab not in ("reel", "autre")
        kind = "acceptées (souhaité ~100 %)" if lab == "reel" else "acceptées (doit être 0 %)"
        for ses in sorted({r.session for r in rows if r.label == lab}):
            sub = [r for r in rows if r.label == lab and r.session == ses]
            usable = sum(r.face and r.quality == "ok" for r in sub)
            rate = window_accept_rate(sub, sim_t, live_t, k, attacker=attack)
            line = f"    {lab:<12} {ses}  {rate:6.1%} des fenêtres {kind}"
            if attack:
                _, means = window_means(sub, sim_t, k, attacker=True)
                if means:
                    line += f" ; pire {max(means):+.2f}, marge {live_t - max(means):+.2f}"
            elif usable < len(sub) / 2:
                line += f"  [seulement {usable}/{len(sub)} images exploitables : éclairage ?]"
            out.append(line)
    out.append("    (attaques : contrôle de qualité supposé réussi, cas le plus défavorable)")
    return "\n".join(out)
