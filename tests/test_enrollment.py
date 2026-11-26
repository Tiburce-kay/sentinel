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
    """Enrôlement en conditions idéales : le seuil n'est jamais durci au-delà du seuil validé."""
    _, out = run(session(), live=8.0)
    assert out.ok and out.liveness_threshold == -1.0


def test_encore_un_peu():
    sess = session()
    for _ in range(6):
        sess.feed(Shot(ME, 1.0))
    ev = sess.feed(Shot(ME, 1.0, yaw_deg=9))   # à mi-chemin de la pose « gauche »
    assert ev.step == "gauche" and ev.hint == "Encore un peu"


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


# --- Authentification et tête tournée (essai réel du 25/11) ----------------------

def auth_engine():
    return session().e


def test_tete_tournee_ignoree_et_pas_prise_pour_une_photo():
    """Cas réel : de profil, le modèle de vivant juge un vrai visage comme une photo."""
    T = np.stack([ME])
    turned = [Shot(ME, -5.0, yaw_deg=-30)] * 8 + [Shot(ME, -5.0, pitch_deg=25)] * 8
    r = auth_engine().authenticate(turned, T)
    assert r.status.name == "TURNED" and r.skipped_pose == 16


def test_retour_de_face_apres_rotation_accepte():
    T = np.stack([ME])
    seq = [Shot(ME, -5.0, yaw_deg=-30)] * 6 + [Shot(ME, 1.0)] * 15
    r = auth_engine().authenticate(seq, T)
    assert r.ok and r.skipped_pose == 6


def test_photo_de_face_toujours_refusee():
    T = np.stack([ME])
    r = auth_engine().authenticate([Shot(ME, -6.0)] * 20, T)
    assert r.status.name == "SPOOF"


def test_reference_de_l_utilisateur_utilisee():
    """Une personne dont la position naturelle est un peu tournée n'est pas pénalisée."""
    from sentinel.geometry import Pose, landmarks
    from tests.head import raw_face
    natural = Pose.of(landmarks(raw_face(project(rotate(HEAD, yaw_deg=12)))))
    T = np.stack([ME])
    seq = [Shot(ME, 1.0, yaw_deg=12)] * 15
    assert auth_engine().authenticate(seq, T, reference_pose=(natural.yaw, natural.pitch)).ok
    assert auth_engine().authenticate(seq, T, reference_pose=(natural.yaw - 0.3, natural.pitch)).status.name == "TURNED"


def test_autre_personne_tete_parfois_tournee_reste_un_echec():
    """Cas réel : un imposteur vu de face puis de profil doit être « non reconnu »
    (compté pour le verrouillage), pas « tête trop tournée »."""
    T = np.stack([ME])
    seq = [Shot(OTHER, 1.0)] * 5 + [Shot(OTHER, 1.0, yaw_deg=-30)] * 25
    assert auth_engine().authenticate(seq, T).status.name == "NO_MATCH"


# --- Défi adaptatif au risque (tourner la tête, direction tirée au sort) ------------

class FixedLiveness:
    def __init__(self, value):
        self.value = value

    def score(self, s, f):
        return s.live if s.live is not None else self.value


def challenge_engine(direction="gauche"):
    e = session().e
    e.liveness = FixedLiveness(0.0)
    e.choose = lambda options: direction
    return e


T1 = np.stack([ME])


def test_vivant_sans_ambiguite_aucun_defi():
    asked = []
    r = challenge_engine().authenticate([Shot(ME, 3.0)] * 15, T1, on_challenge=asked.append)
    assert r.ok and asked == [] and r.challenge == ""


def test_score_limite_defi_reussi_dans_le_bon_sens():
    asked = []
    seq = [Shot(ME, -0.5)] * 10 + [Shot(ME, -0.5, yaw_deg=25)] * 5
    r = challenge_engine("gauche").authenticate(seq, T1, on_challenge=asked.append)
    assert asked == ["Tournez la tête vers votre gauche"] and r.ok


def test_defi_mauvais_sens_refuse():
    seq = [Shot(ME, -0.5)] * 10 + [Shot(ME, -0.5, yaw_deg=25)] * 5  # tourne à gauche
    r = challenge_engine("droite").authenticate(seq, T1, on_challenge=lambda m: None)
    assert r.status.name == "CHALLENGE_FAILED"


def test_video_qui_ne_bouge_pas_refusee():
    seq = [Shot(ME, -0.5)] * 60
    r = challenge_engine().authenticate(seq, T1, on_challenge=lambda m: None)
    assert r.status.name == "CHALLENGE_FAILED"


def test_photo_inclinee_sans_relief_refusee():
    from tests.head import FLAT
    seq = [Shot(ME, -0.5, FLAT)] * 10 + [Shot(ME, -0.5, FLAT, yaw_deg=45)] * 40
    r = challenge_engine("gauche").authenticate(seq, T1, on_challenge=lambda m: None)
    assert r.status.name == "CHALLENGE_FAILED"


def test_autre_personne_pendant_le_defi_refusee():
    seq = [Shot(ME, -0.5)] * 10 + [Shot(OTHER, -0.5, yaw_deg=25)] * 30
    r = challenge_engine("gauche").authenticate(seq, T1, on_challenge=lambda m: None)
    assert r.status.name == "CHALLENGE_FAILED"


def test_sans_afficheur_de_consigne_pas_de_defi():
    """Un appelant qui ne peut pas afficher la consigne (vérification de l'assistant) :
    pas de défi, impossible à relever."""
    r = challenge_engine().authenticate([Shot(ME, -0.5)] * 15, T1)
    assert r.ok and r.challenge == ""
