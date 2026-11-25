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

    def _analyze(self, frame: np.ndarray, face: Face, templates: np.ndarray, pool) -> tuple[float, float]:
        """Similarité et score de vivant. Les deux modèles tournent en parallèle :
        OpenCV et ONNX Runtime libèrent le verrou de Python pendant le calcul."""
        fut = pool.submit(self.liveness.score, frame, face) if pool is not None else None
        sim = best_similarity(self.recognizer.embed(frame, face), templates)
        live = fut.result() if fut is not None else float("nan")
        return sim, live

    def authenticate(self, frames: Iterable[np.ndarray], templates: np.ndarray) -> AuthResult:
        """Accepte quand `window_frames` images CONSÉCUTIVES montrent chacune un visage
        de qualité, reconnu comme le bon, ET que la moyenne de leurs scores de vivant
        dépasse le seuil.

        Pourquoi une moyenne : sur une webcam ordinaire, le score de vivant d'un vrai
        visage varie beaucoup d'une image à l'autre, et une photo produit parfois une
        image isolée « vivante ». En moyenne sur la fenêtre, les deux populations sont
        nettement séparées (voir docs/CALIBRATION.md). L'identité, elle, est exigée
        sur chaque image : une image non reconnue remet la fenêtre à zéro."""
        rc, lc, k = self.cfg.recognition, self.cfg.liveness, self.cfg.auth.window_frames
        use_live = lc.enabled and self.liveness is not None
        start = time.monotonic()
        result = AuthResult(Status.NO_FACE)
        if templates.size == 0:
            result.status = Status.NOT_ENROLLED
            return result
        window: deque[float] = deque(maxlen=k)
        seen_match = full_window_spoof = False
        pool = ThreadPoolExecutor(max_workers=1) if use_live else None
        try:
            for frame in frames:
                result.frames += 1
                face = largest(self.detector.detect(frame))
                if face is None or not self._quality(frame, face).ok:
                    window.clear()
                    continue
                sim, live = self._analyze(frame, face, templates, pool)
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
                if mean >= lc.threshold:
                    result.status = Status.SUCCESS
                    break
                full_window_spoof = True
        finally:
            if pool is not None:
                pool.shutdown(wait=True)
        if result.status is not Status.SUCCESS:
            if full_window_spoof:
                result.status = Status.SPOOF
            elif seen_match:
                result.status = Status.TIMEOUT
            elif result.best_similarity > -1.0:
                result.status = Status.NO_MATCH
        result.elapsed_s = time.monotonic() - start
        return result

    # --- Enrôlement -----------------------------------------------------------

    def enroll(self, frames: Iterable[np.ndarray], on_feedback: Callable[[str], None] = lambda m: None) -> np.ndarray:
        """Collecte des empreintes de bonne qualité, d'un visage vivant et unique.

        Les images trop proches d'une empreinte déjà retenue sont ignorées pour
        obtenir des gabarits variés (légers changements d'angle et d'expression)."""
        ec, lc = self.cfg.enroll, self.cfg.liveness
        kept: list[np.ndarray] = []
        lives: list[float] = []
        last_msg = ""

        def say(msg: str) -> None:
            nonlocal last_msg
            if msg != last_msg:
                on_feedback(msg)
                last_msg = msg

        for frame in frames:
            faces = self.detector.detect(frame)
            if len(faces) != 1:
                say("aucun visage détecté" if not faces else "plusieurs visages : une seule personne devant la caméra")
                continue
            face = faces[0]
            q = self._quality(frame, face)
            if not q.ok:
                say(q.reason)
                continue
            emb = self.recognizer.embed(frame, face)
            if kept:
                ref = np.stack(kept)
                # Cohérence : toutes les images doivent montrer la même personne.
                if best_similarity(emb, ref) < self.cfg.recognition.threshold:
                    say("visage différent des précédents : une seule personne")
                    continue
                if best_similarity(emb, ref) > 0.92:
                    say("bougez légèrement la tête")
                    continue
            kept.append(emb)
            if lc.enabled and self.liveness is not None:
                lives.append(self.liveness.score(frame, face))
            say(f"capture {len(kept)}/{ec.samples}")
            if len(kept) >= ec.samples:
                break
        if lives and float(np.mean(lives)) < lc.threshold:
            # Comme pour l'authentification, le vivant se juge sur l'ensemble.
            say("visage jugé non vivant (photo, écran ?) : rien n'est enregistré")
            return np.zeros((0, 128), np.float32)
        return np.stack(kept) if kept else np.zeros((0, 128), np.float32)
