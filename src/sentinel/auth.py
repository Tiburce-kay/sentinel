"""Moteur de décision : enrôlement et authentification.

Les briques (détecteur, reconnaissance, détection du vivant) sont injectées, ce qui
permet de tester toute la logique de décision sans caméra ni visage réel.
"""

from __future__ import annotations

import enum
import math
import time
from collections import deque
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from .config import Config
from .geometry import Pose, landmarks
from .vision import Face, Quality, assess, best_similarity, largest


class DetectorLike(Protocol):
    def detect(self, frame: np.ndarray) -> list[Face]: ...


class RecognizerLike(Protocol):
    def embed(self, frame: np.ndarray, face: Face) -> np.ndarray: ...


class LivenessLike(Protocol):
    def score(self, frame: np.ndarray, face: Face) -> float: ...


class Status(enum.Enum):
    SUCCESS = "succès"
    NO_MATCH = "visage non reconnu"
    NO_FACE = "aucun visage détecté"
    TIMEOUT = "délai écoulé avant confirmation (restez face à la caméra)"
    TURNED = "tête trop tournée : regardez l'écran, de face"
    SPOOF = "usurpation suspectée (photo, écran ou vidéo)"
    LOCKED = "trop d'échecs : mot de passe requis"
    NOT_ENROLLED = "aucun visage enregistré"


@dataclass
class AuthResult:
    status: Status
    frames: int = 0
    best_similarity: float = -1.0
    liveness_scores: list[float] = field(default_factory=list)
    window_liveness: float = -math.inf   # meilleure moyenne de vivant sur une fenêtre complète
    liveness_threshold: float = math.nan  # seuil effectivement appliqué
    skipped_pose: int = 0                 # images ignorées : tête trop tournée
    camera_s: float = 0.0                 # temps passé à attendre la caméra
    compute_s: float = 0.0                # temps passé à analyser
    elapsed_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status is Status.SUCCESS


@dataclass
class Engine:
    cfg: Config
    detector: DetectorLike
    recognizer: RecognizerLike
    liveness: LivenessLike | None = None
    quality: Callable[[np.ndarray, Face], Quality] | None = None

    def _quality(self, frame: np.ndarray, face: Face) -> Quality:
        if self.quality is not None:
            return self.quality(frame, face)
        return assess(frame, face, self.cfg.quality)

    # --- Authentification -----------------------------------------------------

    def _turned(self, face: Face, ref: Pose | None) -> bool:
        """Tête nettement tournée par rapport à la position de face de l'utilisateur."""
        p = Pose.of(landmarks(face.raw))
        a = self.cfg.auth
        if ref is not None:
            return abs(p.yaw - ref.yaw) > a.max_yaw or abs(p.pitch - ref.pitch) > a.max_pitch
        # Ancien enregistrement, sans position de référence : position de face
        # moyenne (tangage ~0,55), avec une tolérance doublée sur le tangage.
        return abs(p.yaw) > a.max_yaw or abs(p.pitch - self.NEUTRAL_PITCH) > 2 * a.max_pitch

    NEUTRAL_PITCH = 0.55

    def _analyze(self, frame: np.ndarray, face: Face, templates: np.ndarray, pool) -> tuple[float, float]:
        """Similarité et score de vivant. Les deux modèles tournent en parallèle :
        OpenCV et ONNX Runtime libèrent le verrou de Python pendant le calcul."""
        fut = pool.submit(self.liveness.score, frame, face) if pool is not None else None
        sim = best_similarity(self.recognizer.embed(frame, face), templates)
        live = fut.result() if fut is not None else float("nan")
        return sim, live

    def authenticate(self, frames: Iterable[np.ndarray], templates: np.ndarray,
                     liveness_threshold: float | None = None,
                     reference_pose: tuple[float, float] | None = None) -> AuthResult:
        """Accepte quand `window_frames` images CONSÉCUTIVES montrent chacune un visage
        de qualité, reconnu comme le bon, ET que la moyenne de leurs scores de vivant
        dépasse le seuil.

        Pourquoi une moyenne : sur une webcam ordinaire, le score de vivant d'un vrai
        visage varie beaucoup d'une image à l'autre, et une photo produit parfois une
        image isolée « vivante ». En moyenne sur la fenêtre, les deux populations sont
        nettement séparées (voir docs/CALIBRATION.md). L'identité, elle, est exigée
        sur chaque image : une image non reconnue remet la fenêtre à zéro.

        Tête nettement tournée : l'image est ignorée (fenêtre remise à zéro), car le
        modèle de vivant, entraîné surtout sur des visages de face, y juge à tort un
        vrai visage comme une photo. Un attaquant n'y gagne rien : une image ignorée
        ne compte jamais pour accepter."""
        rc, lc, k = self.cfg.recognition, self.cfg.liveness, self.cfg.auth.window_frames
        # Seuil personnel (calibration à l'enrôlement), toujours ramené entre les bornes.
        live_t = lc.threshold if liveness_threshold is None else float(np.clip(liveness_threshold, lc.floor, lc.ceiling))
        result_threshold = live_t
        use_live = lc.enabled and self.liveness is not None
        start = time.monotonic()
        result = AuthResult(Status.NO_FACE)
        if templates.size == 0:
            result.status = Status.NOT_ENROLLED
            return result
        window: deque[float] = deque(maxlen=k)
        seen_match = full_window_spoof = False
        pool = ThreadPoolExecutor(max_workers=1) if use_live else None
        ref_pose = Pose(*reference_pose) if reference_pose else None
        t_last = time.monotonic()
        try:
            for frame in frames:
                t_frame = time.monotonic()
                result.camera_s += t_frame - t_last
                t_last = t_frame
                result.frames += 1
                face = largest(self.detector.detect(frame))
                if face is None or not self._quality(frame, face).ok:
                    window.clear()
                    result.compute_s += time.monotonic() - t_frame
                    t_last = time.monotonic()
                    continue
                if self._turned(face, ref_pose):
                    result.skipped_pose += 1
                    window.clear()
                    result.compute_s += time.monotonic() - t_frame
                    t_last = time.monotonic()
                    continue
                sim, live = self._analyze(frame, face, templates, pool)
                result.compute_s += time.monotonic() - t_frame
                t_last = time.monotonic()
                result.best_similarity = max(result.best_similarity, sim)
                if sim < rc.threshold:
                    window.clear()
                    continue
                seen_match = True
                if use_live:
                    result.liveness_scores.append(live)
                window.append(live)
                if len(window) < k:
                    continue
                mean = float(np.mean(window)) if use_live else float("inf")
                result.window_liveness = max(result.window_liveness, mean)
                if mean >= live_t:
                    result.status = Status.SUCCESS
                    break
                full_window_spoof = True
        finally:
            if pool is not None:
                pool.shutdown(wait=True)
        if result.status is not Status.SUCCESS:
            partial = result.liveness_scores
            if full_window_spoof or (len(partial) >= 3 and float(np.mean(partial)) < live_t):
                # Pas de fenêtre complète, mais les images reconnues étaient nettement
                # « non vivantes » : on le dit, plutôt que d'annoncer un simple délai.
                result.status = Status.SPOOF
            elif seen_match:
                result.status = Status.TIMEOUT
            elif result.skipped_pose:
                result.status = Status.TURNED
            elif result.best_similarity > -1.0:
                result.status = Status.NO_MATCH
        result.liveness_threshold = result_threshold
        result.elapsed_s = time.monotonic() - start
        return result
