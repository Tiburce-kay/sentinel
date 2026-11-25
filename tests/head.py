"""Tête synthétique en 3D, et photo plate, pour tester pose, relief et enrôlement."""

import math

import numpy as np

# Unité : distance entre les yeux. x vers la droite de l'image : l'œil droit de la
# personne (à gauche de l'image) est donc en x négatif. y vers le bas,
# z vers le fond (la caméra est en z négatif) : le nez avance, donc z < 0.
HEAD = np.array([
    [-0.5, 0.0, 0.0],     # œil droit
    [0.5, 0.0, 0.0],      # œil gauche
    [0.0, 0.55, -0.45],   # bout du nez
    [-0.35, 1.0, -0.05],  # coin droit de la bouche
    [0.35, 1.0, -0.05],   # coin gauche de la bouche
])
FLAT = HEAD * np.array([1, 1, 0])  # même dessin, sans relief : une photo


def rotate(pts, yaw_deg=0.0, pitch_deg=0.0):
    """yaw > 0 : la personne tourne vers SA gauche ; pitch > 0 : elle lève la tête."""
    a, b = math.radians(yaw_deg), math.radians(pitch_deg)
    ry = np.array([[math.cos(a), 0, -math.sin(a)], [0, 1, 0], [math.sin(a), 0, math.cos(a)]])
    rx = np.array([[1, 0, 0], [0, math.cos(b), math.sin(b)], [0, -math.sin(b), math.cos(b)]])
    return pts @ ry.T @ rx.T


def project(pts, scale=80.0, cx=320.0, cy=200.0, distance=8.0):
    z = pts[:, 2] + distance
    return np.stack([cx + scale * distance * pts[:, 0] / z, cy + scale * distance * pts[:, 1] / z], axis=1)


def raw_face(pts2d):
    raw = np.zeros(15, np.float32)
    mx, my = pts2d.mean(axis=0)
    raw[:4] = [mx - 70, my - 90, 140, 180]
    raw[4:14] = pts2d.reshape(-1)
    raw[14] = 0.99
    return raw
