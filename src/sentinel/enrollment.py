"""Enrôlement guidé : face, gauche, droite, haut, bas.

Une même séance sert à trois choses :

1. **Reconnaissance** : des empreintes sous plusieurs angles, pour être reconnu même
   la tête un peu tournée.
2. **Calibration automatique** : le score de vivant de CE visage sur CETTE webcam est
   mesuré, et le seuil de l'utilisateur ajusté en conséquence — mais toujours entre un
   plancher et un plafond de sécurité qu'aucune adaptation ne peut franchir.
3. **Contrôle du relief** : en tournant la tête, un vrai visage révèle son relief
   (voir geometry.py) ; une photo, plate, ne peut pas être enrôlée.

La séance est pilotée image par image (méthode `feed`), ce qui permet à l'interface
graphique d'afficher la progression en direct, et de tout tester sans caméra.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .auth import Engine
from .geometry import Pose, landmarks, parallax
from .vision import Face, best_similarity

STEPS = ("face", "gauche", "droite", "haut", "bas")
REFERENCE_FRAMES = 5  # images de face pour fixer la position de référence
BACK_TO_FACE = "Revenez de face"
INSTRUCTIONS = {
    "face": "Regardez la caméra",
    "gauche": "Tournez lentement la tête vers votre gauche",
    "droite": "Tournez lentement la tête vers votre droite",
    "haut": "Levez légèrement la tête",
    "bas": "Baissez légèrement la tête",
}


@dataclass
class EnrollEvent:
    step: str
    instruction: str
    progress: float               # 0 à 1, sur l'ensemble de la séance
    hint: str = ""                # conseil ponctuel (éclairage, distance…)
    box: tuple[int, int, int, int] | None = None
    done: bool = False


@dataclass
class EnrollOutcome:
    ok: bool
    reason: str
    embeddings: np.ndarray
    liveness_threshold: float
    stats: dict = field(default_factory=dict)


class EnrollmentSession:
    def __init__(self, engine: Engine):
        self.e = engine
        c = engine.cfg
        self.ec, self.lc = c.enroll, c.liveness
        self.step_i = 0
        self.per_step: dict[str, list[np.ndarray]] = {s: [] for s in STEPS}
        self.reference_pts: np.ndarray | None = None
        self.reference_pose: Pose | None = None
        self.parallaxes: dict[str, list[float]] = {s: [] for s in STEPS[1:]}
        self.lives: list[float] = []   # séquence des scores de vivant (images valides)
        self.breaks: list[int] = []    # positions où la séquence a été interrompue
        self._reference_samples: list[np.ndarray] = []
        self.anchor: np.ndarray | None = None
        # Une pose ne compte qu'après un retour de face : chaque mouvement est voulu,
        # et une pose ne peut pas être validée par hasard en passant.
        self.armed = True
        self.pose_log: dict[str, list[float]] = {}

    @property
    def step(self) -> str:
        return STEPS[min(self.step_i, len(STEPS) - 1)]

    @property
    def done(self) -> bool:
        return self.step_i >= len(STEPS)

    def _progress(self) -> float:
        n = self.ec.per_pose
        got = sum(min(len(v), n) for v in self.per_step.values())
        return got / (n * len(STEPS))

    def _event(self, hint: str = "", box=None) -> EnrollEvent:
        instruction = INSTRUCTIONS[self.step] if self.armed or self.done else BACK_TO_FACE
        return EnrollEvent(self.step, instruction, self._progress(), hint, box, self.done)

    def _near_face(self, pose: Pose) -> bool:
        ref, ec = self.reference_pose, self.ec
        return abs(pose.yaw - ref.yaw) < ec.yaw_delta / 2 and abs(pose.pitch - ref.pitch) < ec.pitch_delta / 2

    def _pose_reached(self, pose: Pose) -> bool:
        ref, ec = self.reference_pose, self.ec
        if self.step == "face":
            return True
        dy, dp = pose.yaw - ref.yaw, pose.pitch - ref.pitch
        return {
            "gauche": dy > ec.yaw_delta,
            "droite": dy < -ec.yaw_delta,
            "haut": dp < -ec.pitch_delta,
            "bas": dp > ec.pitch_delta,
        }[self.step]

    def feed(self, frame: np.ndarray) -> EnrollEvent:
        if self.done:
            return self._event()
        faces = self.e.detector.detect(frame)
        if len(faces) != 1:
            self._break()
            return self._event("Aucun visage détecté" if not faces else "Une seule personne devant la caméra")
        face: Face = faces[0]
        q = self.e._quality(frame, face)
        # Les poses inclinées sont attendues : on ne bloque pas sur l'inclinaison.
        if not q.ok and q.reason != "tête trop inclinée":
            self._break()
            return self._event(q.reason.capitalize(), face.box)
        emb = self.e.recognizer.embed(frame, face)
        # Même personne du début à la fin : comparaison à la première empreinte vue
        # (dès les images de référence) et à toutes celles retenues.
        if self.anchor is None:
            self.anchor = emb
        everyone = [self.anchor] + [e for v in self.per_step.values() for e in v]
        if best_similarity(emb, np.stack(everyone)) < self.e.cfg.recognition.threshold:
            self._break()
            return self._event("Visage différent : une seule personne pendant l'enregistrement", face.box)
        if self.e.liveness is not None and self.lc.enabled:
            self.lives.append(self.e.liveness.score(frame, face))
        pts = landmarks(face.raw)
        pose = Pose.of(pts)
        if self.reference_pts is None:
            # Référence : position médiane sur plusieurs images de face.
            self._reference_samples.append(pts)
            if len(self._reference_samples) < REFERENCE_FRAMES:
                return self._event("", face.box)
            self.reference_pts = np.median(np.stack(self._reference_samples), axis=0)
            self.reference_pose = Pose.of(self.reference_pts)
        if not self.armed:
            if self._near_face(pose):
                self.armed = True
            return self._event("", face.box)
        if not self._pose_reached(pose):
            return self._event("", face.box)
        if self.step != "face":
            self.parallaxes[self.step].append(parallax(self.reference_pts, pts))
        kept = self.per_step[self.step]
        if not kept or best_similarity(emb, np.stack(kept)) < 0.97:
            kept.append(emb)
        if len(kept) >= self.ec.per_pose:
            if self.step != "face":
                ref = self.reference_pose
                self.pose_log[self.step] = [round(pose.yaw - ref.yaw, 3), round(pose.pitch - ref.pitch, 3)]
            self.step_i += 1
            self.armed = self.step_i <= 1  # après « face », il faut revenir de face entre deux poses
        return self._event("", face.box)

    def _break(self) -> None:
        if self.lives and (not self.breaks or self.breaks[-1] != len(self.lives)):
            self.breaks.append(len(self.lives))

    # --- Bilan ------------------------------------------------------------------

    def _window_means(self, k: int) -> list[float]:
        means, start = [], 0
        for end in self.breaks + [len(self.lives)]:
            seg = self.lives[start:end]
            means += [float(np.mean(seg[i:i + k])) for i in range(len(seg) - k + 1)]
            start = end
        return means

    def finish(self) -> EnrollOutcome:
        lc, k = self.lc, self.e.cfg.auth.window_frames
        emb = [e for s in STEPS for e in self.per_step[s]]
        empty = np.zeros((0, 128), np.float32)
        if not self.done:
            return EnrollOutcome(False, f"Enregistrement incomplet (étape « {self.step} »)", empty, lc.threshold)
        side = [p for s in STEPS[1:] for p in self.parallaxes[s]]
        relief = float(np.median(side)) if side else 0.0
        stats = {"relief": relief, "frames": len(self.lives), "poses": self.pose_log}
        if relief < self.ec.min_parallax:
            return EnrollOutcome(False, "Relief du visage insuffisant : photo ou écran suspecté",
                                 empty, lc.threshold, stats)
        threshold = lc.threshold
        if lc.enabled and self.e.liveness is not None:
            means = self._window_means(k)
            if not means:
                return EnrollOutcome(False, "Pas assez d'images consécutives : recommencez sans bouger trop vite",
                                     empty, lc.threshold, stats)
            p10, med = float(np.percentile(means, 10)), float(np.median(means))
            stats.update({"liveness_p10": p10, "liveness_median": med})
            if med < lc.floor:
                return EnrollOutcome(False, "Visage jugé non vivant, ou éclairage insuffisant",
                                     empty, lc.threshold, stats)
            # Seuil personnel : sous les fenêtres les plus basses de l'utilisateur,
            # mais jamais sous le plancher ni au-dessus du plafond.
            threshold = float(np.clip(p10 - lc.calibration_margin, lc.floor, lc.ceiling))
        stats["liveness_threshold"] = threshold
        return EnrollOutcome(True, "Visage enregistré", np.stack(emb).astype(np.float32), threshold, stats)
