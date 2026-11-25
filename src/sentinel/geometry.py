"""Géométrie du visage à partir des 5 points de YuNet : pose et relief.

Points (coordonnées image) : œil droit, œil gauche, bout du nez, coin droit de la
bouche, coin gauche de la bouche (« droit » = côté droit de la personne, donc à
gauche de l'image, la caméra ne faisant pas effet miroir).

Relief (parallaxe) : les yeux et les coins de la bouche sont presque dans un même
plan ; le bout du nez, lui, avance de plusieurs centimètres. Quand une vraie tête
tourne, le nez se déplace par rapport à ce plan. Une photo, elle, est entièrement
plate : quelle que soit la façon dont on l'incline, une transformation affine des
yeux et de la bouche prédit la position du nez presque parfaitement. Le résidu de
cette prédiction mesure donc le relief réel du visage.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Indices des points dans la ligne brute YuNet (après les 4 valeurs du cadre).
R_EYE, L_EYE, NOSE, R_MOUTH, L_MOUTH = range(5)


def landmarks(raw: np.ndarray) -> np.ndarray:
    """Les 5 points du visage, tableau (5, 2)."""
    return np.asarray(raw[4:14], dtype=np.float64).reshape(5, 2)


@dataclass(frozen=True)
class Pose:
    yaw: float    # > 0 : tête tournée vers la gauche de la personne
    pitch: float  # position verticale relative du nez, à comparer à une référence

    @staticmethod
    def of(pts: np.ndarray) -> "Pose":
        eyes = (pts[R_EYE] + pts[L_EYE]) / 2
        mouth = (pts[R_MOUTH] + pts[L_MOUTH]) / 2
        axis = pts[L_EYE] - pts[R_EYE]
        d = float(np.linalg.norm(axis))
        if d < 1e-6:
            return Pose(0.0, 0.0)
        u = axis / d                       # vers la gauche de la personne
        n = np.array([-u[1], u[0]])        # vers le bas du visage
        yaw = float(np.dot(pts[NOSE] - eyes, u) / d)
        vertical = float(np.dot(mouth - eyes, n))
        pitch = float(np.dot(pts[NOSE] - eyes, n) / vertical) if abs(vertical) > 1e-6 else 0.0
        return Pose(yaw, pitch)


def parallax(reference: np.ndarray, current: np.ndarray) -> float:
    """Écart entre la position réelle du nez et celle que prédirait un visage plat,
    en fraction de la distance entre les yeux. ~0 pour une photo, nettement positif
    pour une vraie tête qui a tourné."""
    plane = [R_EYE, L_EYE, R_MOUTH, L_MOUTH]
    src = np.hstack([reference[plane], np.ones((4, 1))])        # (4, 3)
    affine, *_ = np.linalg.lstsq(src, current[plane], rcond=None)  # (3, 2)
    predicted = np.append(reference[NOSE], 1.0) @ affine
    d = float(np.linalg.norm(current[L_EYE] - current[R_EYE]))
    if d < 1e-6:
        return 0.0
    return float(np.linalg.norm(current[NOSE] - predicted) / d)
