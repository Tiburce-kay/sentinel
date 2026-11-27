"""Logique de décision, testée avec des briques simulées (sans caméra ni visage)."""

import numpy as np

from sentinel.auth import Engine, Status
from sentinel.config import Config
from sentinel.vision import Face, Quality
from tests.conftest import unit

ME = unit(1)
OTHER = unit(2)


def face():
    """Visage de face, avec des points de repère réalistes (tête synthétique)."""
    from tests.head import HEAD, project, raw_face
    return Face(raw_face(project(HEAD)))


class Scene:
    """Une « image » simulée : qui est devant la caméra, et est-ce vivant ?"""

    def __init__(self, who=None, live=2.0, faces=1):
        self.who, self.live, self.faces = who, live, faces


class FakeDetector:
    def detect(self, s):
        return [] if s.who is None else [face() for _ in range(s.faces)]


class FakeRecognizer:
    def __init__(self):
        self.i = 0

    def embed(self, s, f):
        # Petite variation d'une image à l'autre, comme en vrai.
        self.i += 1
        v = s.who + np.random.default_rng(1000 + self.i).normal(scale=0.03, size=128).astype(np.float32)
        return v / np.linalg.norm(v)


class FakeLiveness:
    def score(self, s, f):
        return s.live


def engine(**over):
    cfg = Config()
    for k, v in over.items():
        sect, attr = k.split("__")
        setattr(getattr(cfg, sect), attr, v)
    return Engine(cfg, FakeDetector(), FakeRecognizer(), FakeLiveness(),
                  quality=lambda fr, f: Quality(True))


T = np.stack([ME])


K = 10  # fenêtre par défaut


def test_bon_visage_vivant_accepte():
    r = engine().authenticate([Scene(ME)] * 15, T)
    assert r.status is Status.SUCCESS and r.frames == K


def test_autre_personne_refusee():
    r = engine().authenticate([Scene(OTHER)] * 20, T)
    assert r.status is Status.NO_MATCH


def test_photo_du_bon_visage_refusee():
    r = engine().authenticate([Scene(ME, live=-3.0)] * 20, T)
    assert r.status is Status.SPOOF


def test_vrai_visage_bruite_accepte():
    """Cas réel mesuré : le score d'un vrai visage oscille fortement (de -3 à +3)."""
    seq = [Scene(ME, live=v) for v in [2.5, -3.0, 0.5, 3.0, -2.0, 1.0, 0.0, -1.5, 2.0, 0.5] * 3]
    assert engine().authenticate(seq, T).ok


def test_photo_avec_images_isolees_vivantes_refusee():
    """Cas réel mesuré : une photo produit parfois une image isolée au-dessus de +1."""
    seq = [Scene(ME, live=v) for v in [-6.0, -7.0, 1.1, -5.0, -6.5, -4.0, -8.0, 0.4, -6.0, -5.5] * 5]
    r = engine().authenticate(seq, T)
    assert r.status is Status.SPOOF and r.window_liveness < -1.0


def test_une_image_non_reconnue_remet_la_fenetre_a_zero():
    seq = ([Scene(ME)] * 9 + [Scene(OTHER)]) * 3
    assert engine().authenticate(seq, T).status is not Status.SUCCESS


def test_delai_ecoule_avant_fenetre_complete():
    assert engine().authenticate([Scene(ME)] * (K - 1), T).status is Status.TIMEOUT


def test_aucun_visage():
    assert engine().authenticate([Scene(None)] * 10, T).status is Status.NO_FACE


def test_non_enrole():
    r = engine().authenticate([Scene(ME)] * 15, np.zeros((0, 128), np.float32))
    assert r.status is Status.NOT_ENROLLED


def test_vivant_desactive_accepte_la_photo():
    r = engine(liveness__enabled=False).authenticate([Scene(ME, live=-3.0)] * 15, T)
    assert r.ok


def test_seuil_strict_refuse():
    r = engine(recognition__threshold=0.9999).authenticate([Scene(ME)] * 10, T)
    assert r.status is Status.NO_MATCH


def test_photo_sans_fenetre_complete_signalee_comme_usurpation():
    """Cas réel : photo tenue de loin, fenêtre jamais complète, scores très bas."""
    seq = ([Scene(ME, live=-7.0)] * 4 + [Scene(None)]) * 10
    assert engine().authenticate(seq, T).status is Status.SPOOF


def test_vrai_visage_interrompu_reste_un_delai():
    seq = ([Scene(ME, live=1.0)] * 4 + [Scene(None)]) * 10
    assert engine().authenticate(seq, T).status is Status.TIMEOUT


def test_seuil_personnel_applique_et_borne():
    seq = [Scene(ME, live=-1.5)] * 15
    e = engine(liveness__challenge=False)  # ce test porte sur le seuil, pas sur le défi
    assert not e.authenticate(seq, T).ok                      # seuil global -1,0
    assert e.authenticate(seq, T, liveness_threshold=-1.8).ok  # seuil personnel
    # Un seuil personnel sous le plancher (-2,0) est ramené au plancher.
    r = engine().authenticate([Scene(ME, live=-2.5)] * 15, T, liveness_threshold=-9.0)
    assert not r.ok and r.liveness_threshold == -2.0


def test_abandon_immediat_si_aucun_visage():
    r = engine(auth__give_up_no_face_s=0.0).authenticate([Scene(None)] * 50, T)
    assert r.status is Status.NO_FACE and r.frames == 1


def test_abandon_si_trop_sombre():
    dark = np.zeros((48, 64, 3), np.uint8)
    e = engine(auth__give_up_dark_s=0.0)
    e.detector = type("Aveugle", (), {"detect": lambda self, f: []})()  # dans le noir, aucun visage
    r = e.authenticate([dark] * 50, T)
    assert r.status is Status.TOO_DARK and r.frames == 1


def test_interruption_a_la_demande():
    r = engine().authenticate([Scene(ME)] * 50, T, should_abort=lambda: True)
    assert r.status is Status.SKIPPED and r.frames == 1


def test_abandon_quand_le_visage_s_en_va():
    """Quelqu'un passe devant la caméra puis s'en va : abandon sans attendre le délai."""
    r = engine(auth__give_up_no_face_s=0.0).authenticate([Scene(ME)] * 3 + [Scene(None)] * 50, T)
    assert r.frames == 4 and not r.ok
    assert r.status is Status.TIMEOUT  # le bon visage, mais trop bref : simple délai


def test_imposteur_parti_tot_reste_un_echec_compte():
    """Un imposteur qui se retire vite ne doit pas échapper au compteur d'échecs."""
    r = engine(auth__give_up_no_face_s=0.0).authenticate([Scene(OTHER)] * 3 + [Scene(None)] * 50, T)
    assert r.status is Status.NO_MATCH and r.frames == 4


def _rejecting(reason):
    from sentinel.vision import Quality
    e = engine()
    e.quality = lambda fr, f: Quality(False, reason)
    return e


def test_visage_vu_mais_image_inexploitable_le_dit_avec_un_conseil():
    """Essai réel du 27/11 : visage présent 4 s, aucune image analysée, message
    « aucun visage détecté » trompeur. Le motif est maintenant compté et expliqué."""
    r = _rejecting("visage trop loin ou trop petit").authenticate([Scene(ME)] * 30, T)
    assert r.status is Status.POOR_IMAGE and not r.ok
    assert r.rejected == {"visage trop loin ou trop petit": 30}
    assert r.message == "visage trop loin : rapprochez-vous de la caméra"


def test_motif_le_plus_frequent_retenu():
    from sentinel.vision import Quality
    e = engine()
    reasons = iter(["image floue"] * 5 + ["tête trop inclinée"] * 20)
    e.quality = lambda fr, f: Quality(False, next(reasons))
    r = e.authenticate([Scene(ME)] * 25, T)
    assert r.message == "tête trop inclinée : redressez la tête"


def test_image_inexploitable_ne_compte_pas_comme_echec():
    from sentinel.auth import Status as S
    r = _rejecting("image floue").authenticate([Scene(ME)] * 30, T)
    assert r.status not in (S.SPOOF, S.NO_MATCH, S.CHALLENGE_FAILED)
