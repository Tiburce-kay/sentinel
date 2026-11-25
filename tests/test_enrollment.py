import numpy as np

from sentinel.auth import Engine
from sentinel.config import Config
from sentinel.enrollment import STEPS, EnrollmentSession
from sentinel.vision import Face, Quality
from tests.conftest import unit
from tests.head import FLAT, HEAD, project, raw_face, rotate

ME, OTHER = unit(1), unit(2)
POSES = {"face": {}, "gauche": {"yaw_deg": 25}, "droite": {"yaw_deg": -25},
         "haut": {"pitch_deg": 20}, "bas": {"pitch_deg": -20}}


class Shot:
    shape = (480, 640, 3)  # dimensions d'une image de webcam simulée

    def __init__(self, who, live, model=HEAD, n=1, **pose):
        self.who, self.live, self.model, self.n, self.pose = who, live, model, n, pose


class Detector:
    def detect(self, s):
        if s.who is None:
            return []
        return [Face(raw_face(project(rotate(s.model, **s.pose))))] * s.n


class Recognizer:
    def __init__(self):
        self.rng = np.random.default_rng(7)

    def embed(self, s, f):
        v = s.who + self.rng.normal(scale=0.05, size=128).astype(np.float32)
        return v / np.linalg.norm(v)


class Liveness:
    def __init__(self):
        self.rng = np.random.default_rng(3)

    def score(self, s, f):
        return float(s.live + self.rng.normal(scale=1.5))   # bruité, comme en vrai


def session(**over):
    cfg = Config()
    for k, v in over.items():
        sect, attr = k.split("__")
        setattr(getattr(cfg, sect), attr, v)
    return EnrollmentSession(Engine(cfg, Detector(), Recognizer(), Liveness(),
                                    quality=lambda fr, f: Quality(True)))


def user_frames(shots_per_pose=12, who=ME, live=1.0, model=HEAD, scale=1.0):
    """Comme un vrai utilisateur : chaque pose, puis retour de face."""
    for step in STEPS:
        for _ in range(shots_per_pose):
            yield Shot(who, live, model, **{k: scale * v for k, v in POSES[step].items()})
        for _ in range(3):
            yield Shot(who, live, model)


def run(sess, shots_per_pose=12, who=ME, live=1.0, model=HEAD):
    events = [sess.feed(f) for f in user_frames(shots_per_pose, who, live, model)]
    return events, sess.finish()


def test_enrolement_complet_d_un_vrai_visage():
    events, out = run(session())
    assert out.ok, out.reason
    assert out.embeddings.shape == (10, 128)
    assert events[-1].done and events[-1].progress == 1.0
    assert out.stats["relief"] > 0.10
    assert set(out.stats["poses"]) == {"gauche", "droite", "haut", "bas"}
    assert -2.0 <= out.liveness_threshold <= 1.0


def test_progression_et_consignes():
    sess = session()
    ev = sess.feed(Shot(ME, 1.0))
    assert ev.step == "face" and ev.instruction == "Regardez la caméra"
    for _ in range(5):
        ev = sess.feed(Shot(ME, 1.0))
    assert ev.step == "gauche" and 0 < ev.progress < 1
    assert ev.instruction.startswith("Tournez")


def test_retour_de_face_exige_entre_deux_poses():
    """Passer directement de gauche à droite ne valide pas « droite » : il faut revenir de face."""
    sess = session()
    for _ in range(6):
        sess.feed(Shot(ME, 1.0))
    for _ in range(4):
        ev = sess.feed(Shot(ME, 1.0, **POSES["gauche"]))
    assert ev.step == "droite" and ev.instruction == "Revenez de face"
    for _ in range(6):
        ev = sess.feed(Shot(ME, 1.0, **POSES["droite"]))
    assert ev.step == "droite" and ev.instruction == "Revenez de face"
    ev = sess.feed(Shot(ME, 1.0))
    assert ev.instruction.startswith("Tournez lentement la tête vers votre droite")
    for _ in range(3):
        ev = sess.feed(Shot(ME, 1.0, **POSES["droite"]))
    assert ev.step == "haut"


def test_reference_mediane_resiste_a_une_premiere_image_penchee():
    """Cas réel : tête légèrement baissée au début ; « haut » ne doit pas être validé d'office."""
    sess = session()
    sess.feed(Shot(ME, 1.0, pitch_deg=-8))
    for _ in range(5):
        sess.feed(Shot(ME, 1.0))
    for step in ("gauche", "droite"):
        for _ in range(4):
            sess.feed(Shot(ME, 1.0, **POSES[step]))
        sess.feed(Shot(ME, 1.0))
    for _ in range(5):
        ev = sess.feed(Shot(ME, 1.0))  # de face : ne vaut pas « haut »
    assert ev.step == "haut"


def test_photo_inclinee_refusee():
    """Une photo plate n'atteint pas les poses, ou n'a pas de relief : jamais enrôlée."""
    sess = session()
    for f in user_frames(model=FLAT, scale=1.6):
        sess.feed(f)
    assert not sess.finish().ok


def test_relief_insuffisant_refuse():
    _, out = run(session(enroll__min_parallax=0.9))
    assert not out.ok and "relief" in out.reason.lower()


def test_visage_non_vivant_refuse():
    _, out = run(session(), live=-6.0)
    assert not out.ok and "non vivant" in out.reason


def test_seuil_personnel_borne_par_le_plafond():
    _, out = run(session(), live=8.0)
    assert out.ok and out.liveness_threshold == 1.0


def test_seuil_personnel_borne_par_le_plancher():
    _, out = run(session(liveness__floor=-2.0), live=-1.0)
    assert out.ok and out.liveness_threshold >= -2.0


def test_changement_de_personne_signale():
    sess = session()
    sess.feed(Shot(ME, 1.0))
    ev = sess.feed(Shot(OTHER, 1.0))
    assert "Visage différent" in ev.hint


def test_plusieurs_visages_signales():
    ev = session().feed(Shot(ME, 1.0, n=2))
    assert "Une seule personne" in ev.hint
