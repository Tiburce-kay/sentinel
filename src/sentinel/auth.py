"""Moteur de décision : enrôlement et authentification.

Les briques (détecteur, reconnaissance, détection du vivant) sont injectées, ce qui
permet de tester toute la logique de décision sans caméra ni visage réel.
"""

from __future__ import annotations

import enum
import time
from collections.abc import Callable, Iterable
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
    SPOOF = "usurpation suspectée (photo, écran ou vidéo)"
    LOCKED = "trop d'échecs : mot de passe requis"
    NOT_ENROLLED = "aucun visage enregistré"


@dataclass
class AuthResult:
    status: Status
    frames: int = 0
    best_similarity: float = -1.0
    liveness_scores: list[float] = field(default_factory=list)
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

    def authenticate(self, frames: Iterable[np.ndarray], templates: np.ndarray) -> AuthResult:
        """Accepte si `required_frames` images CONSÉCUTIVES montrent le bon visage,
        chacune jugée vivante. Une seule image douteuse remet le compteur à zéro :
        un attaquant ne peut pas accumuler des succès isolés."""
        rc, lc = self.cfg.recognition, self.cfg.liveness
        start = time.monotonic()
        result = AuthResult(Status.NO_FACE)
        if templates.size == 0:
            result.status = Status.NOT_ENROLLED
            return result
        streak = 0
        matched_but_spoof = 0
        for frame in frames:
            result.frames += 1
            face = largest(self.detector.detect(frame))
            if face is None or not self._quality(frame, face).ok:
                streak = 0
                continue
            sim = best_similarity(self.recognizer.embed(frame, face), templates)
            result.best_similarity = max(result.best_similarity, sim)
            if sim < rc.threshold:
                streak = 0
                if result.status is Status.NO_FACE:
                    result.status = Status.NO_MATCH
                continue
            if lc.enabled and self.liveness is not None:
                live = self.liveness.score(frame, face)
                result.liveness_scores.append(live)
                if live < lc.threshold:
                    streak = 0
                    matched_but_spoof += 1
                    result.status = Status.SPOOF
                    continue
            streak += 1
            if streak >= rc.required_frames:
                result.status = Status.SUCCESS
                break
        if result.status is not Status.SUCCESS and matched_but_spoof == 0 and result.best_similarity >= rc.threshold:
            # Le bon visage a été vu, mais pas assez longtemps de suite.
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
            if lc.enabled and self.liveness is not None and self.liveness.score(frame, face) < lc.threshold:
                say("visage jugé non vivant : améliorez l'éclairage, sans contre-jour")
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
            say(f"capture {len(kept)}/{ec.samples}")
            if len(kept) >= ec.samples:
                break
        return np.stack(kept) if kept else np.zeros((0, 128), np.float32)
