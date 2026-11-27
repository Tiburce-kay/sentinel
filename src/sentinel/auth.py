"""Moteur de décision : enrôlement et authentification.

Les briques (détecteur, reconnaissance, détection du vivant) sont injectées, ce qui
permet de tester toute la logique de décision sans caméra ni visage réel.
"""

from __future__ import annotations

import enum
import math
import secrets
import time
from collections import deque
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from .config import Config
from .geometry import Pose, landmarks, parallax
from .vision import Face, Quality, assess, best_similarity, largest


class DetectorLike(Protocol):
    def detect(self, frame: np.ndarray) -> list[Face]: ...


class RecognizerLike(Protocol):
    def embed(self, frame: np.ndarray, face: Face) -> np.ndarray: ...


class LivenessLike(Protocol):
    def score(self, frame: np.ndarray, face: Face) -> float: ...


def _brightness(frame) -> float | None:
    """Luminosité moyenne d'une image (None pour un objet qui n'est pas une image)."""
    if not isinstance(frame, np.ndarray) or frame.size == 0:
        return None
    return float(frame[::8, ::8].mean())


class Status(enum.Enum):
    SUCCESS = "succès"
    NO_MATCH = "visage non reconnu"
    NO_FACE = "aucun visage détecté"
    TIMEOUT = "délai écoulé avant confirmation (restez face à la caméra)"
    TURNED = "tête trop tournée : regardez l'écran, de face"
    TOO_DARK = "trop sombre : utilisez votre mot de passe"
    CHALLENGE_FAILED = "défi non réussi : utilisez votre mot de passe"
    SKIPPED = "passage au mot de passe"
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
    challenge: str = ""                   # direction demandée, si un défi a eu lieu
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
    # Tirage de la direction du défi (aléatoire cryptographique ; remplaçable en test).
    choose: Callable[[tuple], str] = field(default=secrets.choice)

    def _quality(self, frame: np.ndarray, face: Face) -> Quality:
        if self.quality is not None:
            return self.quality(frame, face)
        return assess(frame, face, self.cfg.quality)

    # --- Authentification -----------------------------------------------------

    def _challenge(self, frames, templates: np.ndarray, ref_pts: np.ndarray, result: AuthResult,
                   should_abort, on_challenge) -> Status:
        """Défi adaptatif au risque : « tournez la tête vers votre gauche/droite », tiré
        au sort. Une vidéo préenregistrée ne peut pas deviner la direction ; une photo
        qu'on incline n'a pas de relief (parallaxe du nez, voir geometry.py) ; et le
        visage doit rester reconnu pendant le geste."""
        a, ec = self.cfg.auth, self.cfg.enroll
        direction = self.choose(("gauche", "droite"))
        result.challenge = direction
        if on_challenge is not None:
            on_challenge(f"Tournez la tête vers votre {direction}")
        sign = 1.0 if direction == "gauche" else -1.0
        ref = Pose.of(ref_pts)
        deadline = time.monotonic() + a.challenge_timeout_s
        good = wrong = 0
        for frame in frames:
            if should_abort is not None and should_abort():
                return Status.SKIPPED
            if time.monotonic() > deadline:
                break
            face = largest(self.detector.detect(frame))
            if face is None:
                continue
            pts = landmarks(face.raw)
            dy = (Pose.of(pts).yaw - ref.yaw) * sign
            # Mauvais sens : seulement s'il est net (1,5 fois l'angle demandé) et confirmé
            # sur deux images d'affilée. Un petit mouvement réflexe au moment où la
            # consigne s'affiche ne fait pas échouer (essai réel du 26/11), mais une
            # vidéo qui tourne de l'autre côté est toujours arrêtée.
            if dy < -1.5 * ec.yaw_delta:
                wrong += 1
                if wrong >= 2:
                    return Status.CHALLENGE_FAILED
                continue
            wrong = 0
            if dy < ec.yaw_delta:
                continue
            if best_similarity(self.recognizer.embed(frame, face), templates) < self.cfg.recognition.threshold:
                continue
            if parallax(ref_pts, pts) < ec.min_parallax:
                continue  # aucun relief : support plat
            good += 1
            if good >= 2:
                return Status.SUCCESS
        return Status.CHALLENGE_FAILED

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
                     reference_pose: tuple[float, float] | None = None,
                     should_abort: Callable[[], bool] | None = None,
                     on_challenge: Callable[[str], None] | None = None) -> AuthResult:
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
            a = self.cfg.auth
            frames = iter(frames)  # la phase de défi reprend le même flux d'images
            last_face_t = start  # dernier instant où un visage était visible
            challenge_ref = None   # points du visage de face, si un défi est nécessaire
            gray_pts = None        # dernière fenêtre en zone grise sous le seuil
            last_pts = None
            dark_since: float | None = None
            for frame in frames:
                t_frame = time.monotonic()
                result.camera_s += t_frame - t_last
                t_last = t_frame
                result.frames += 1
                # Abandons anticipés : le mot de passe doit arriver vite quand le visage
                # ne peut visiblement pas réussir (demande de l'utilisateur, noir, absence).
                if should_abort is not None and should_abort():
                    result.status = Status.SKIPPED
                    break
                if t_frame - start > a.timeout_s:
                    break
                face = largest(self.detector.detect(frame))
                quality = self._quality(frame, face) if face is not None else None
                dark = (quality is not None and quality.reason == "image trop sombre") or (
                    face is None and _brightness(frame) is not None and _brightness(frame) < self.cfg.quality.min_brightness)
                dark_since = (dark_since or t_frame) if dark else None
                if dark_since is not None and t_frame - dark_since >= a.give_up_dark_s:
                    result.status = Status.TOO_DARK
                    break
                if face is not None:
                    last_face_t = t_frame
                elif t_frame - last_face_t >= a.give_up_no_face_s:
                    # Personne devant la caméra depuis give_up_no_face_s (dès le début,
                    # ou après le départ d'un visage) : inutile d'attendre la fin du délai.
                    # Le statut final reste établi plus bas sur ce qui a été vu (un
                    # imposteur parti tôt compte toujours comme un échec).
                    result.status = Status.NO_FACE
                    break
                if face is None or not quality.ok:
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
                last_pts = landmarks(face.raw)
                if len(window) < k:
                    continue
                mean = float(np.mean(window)) if use_live else float("inf")
                result.window_liveness = max(result.window_liveness, mean)
                can_challenge = use_live and lc.challenge and on_challenge is not None
                if mean >= live_t:
                    if can_challenge and mean < live_t + lc.challenge_margin:
                        # Réussite « limite » : on demande un geste imprévisible avant
                        # d'accepter (voir _challenge).
                        challenge_ref = last_pts
                    else:
                        result.status = Status.SUCCESS
                    break
                if can_challenge and mean >= max(live_t - lc.challenge_low_margin, lc.challenge_floor):
                    # Zone grise SOUS le seuil (éclairage difficile, caméra froide…) : plutôt
                    # que de conclure à une usurpation, on laisse d'abord le temps à une
                    # fenêtre de passer franchement, puis on propose le défi.
                    gray_pts = last_pts
                    if t_frame - start >= a.challenge_after_s:
                        challenge_ref = gray_pts
                        break
                    continue
                full_window_spoof = True
            if challenge_ref is None and gray_pts is not None and result.status is not Status.SKIPPED \
                    and result.status is not Status.TOO_DARK and result.status is not Status.NO_FACE:
                challenge_ref = gray_pts
            if challenge_ref is not None:
                result.status = self._challenge(frames, templates, challenge_ref, result,
                                                should_abort, on_challenge)
        finally:
            if pool is not None:
                pool.shutdown(wait=True)
        if result.status not in (Status.SUCCESS, Status.SKIPPED, Status.TOO_DARK, Status.CHALLENGE_FAILED):
            partial = result.liveness_scores
            if full_window_spoof or (len(partial) >= 3 and float(np.mean(partial)) < live_t):
                # Pas de fenêtre complète, mais les images reconnues étaient nettement
                # « non vivantes » : on le dit, plutôt que d'annoncer un simple délai.
                result.status = Status.SPOOF
            elif seen_match:
                result.status = Status.TIMEOUT
            elif result.best_similarity > -1.0:
                # Un visage de face a été analysé et n'est pas le bon : c'est un échec
                # (compté pour le verrouillage), même si d'autres images étaient tournées.
                result.status = Status.NO_MATCH
            elif result.skipped_pose:
                result.status = Status.TURNED
        result.liveness_threshold = result_threshold
        result.elapsed_s = time.monotonic() - start
        return result
