"""Logique de décision, testée avec des briques simulées (sans caméra ni visage)."""

import numpy as np

from sentinel.auth import Engine, Status
from sentinel.config import Config
from sentinel.vision import Face, Quality
from tests.conftest import unit

ME = unit(1)
OTHER = unit(2)


def face():
    raw = np.zeros(15, np.float32)
    raw[:4] = [100, 100, 150, 150]
    raw[14] = 0.99
    return Face(raw)


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
        v = s.who + np.random.default_rng(self.i).normal(scale=0.03, size=128).astype(np.float32)
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


def test_bon_visage_vivant_accepte():
    r = engine().authenticate([Scene(ME)] * 5, T)
    assert r.status is Status.SUCCESS and r.frames == 3


def test_autre_personne_refusee():
    r = engine().authenticate([Scene(OTHER)] * 20, T)
    assert r.status is Status.NO_MATCH


def test_photo_du_bon_visage_refusee():
    r = engine().authenticate([Scene(ME, live=-3.0)] * 20, T)
    assert r.status is Status.SPOOF


def test_succes_isoles_non_cumulables():
    """Alterner vrai visage et photo ne doit jamais atteindre 3 images consécutives."""
    seq = [Scene(ME), Scene(ME), Scene(ME, live=-3.0)] * 10
    assert engine().authenticate(seq, T).status is not Status.SUCCESS


def test_aucun_visage():
    assert engine().authenticate([Scene(None)] * 10, T).status is Status.NO_FACE


def test_non_enrole():
    r = engine().authenticate([Scene(ME)] * 5, np.zeros((0, 128), np.float32))
    assert r.status is Status.NOT_ENROLLED


def test_vivant_desactive_accepte_la_photo():
    r = engine(liveness__enabled=False).authenticate([Scene(ME, live=-3.0)] * 5, T)
    assert r.ok


def test_seuil_strict_refuse():
    r = engine(recognition__threshold=0.9999).authenticate([Scene(ME)] * 10, T)
    assert r.status is Status.NO_MATCH


def test_enrolement_collecte_des_empreintes_variees():
    e = engine(enroll__samples=4)
    rng = np.random.default_rng(0)

    def frames():
        for _ in range(200):
            v = ME + rng.normal(scale=0.04, size=128).astype(np.float32)
            yield Scene(v / np.linalg.norm(v))

    out = e.enroll(frames())
    assert out.shape == (4, 128)
    assert all(abs(np.linalg.norm(v) - 1) < 1e-5 for v in out)


def test_enrolement_refuse_plusieurs_visages_et_photo():
    msgs = []
    out = engine().enroll([Scene(ME, faces=2)] * 5 + [Scene(ME, live=-3.0)] * 5, msgs.append)
    assert len(out) == 0
    assert any("plusieurs visages" in m for m in msgs)
    assert any("non vivant" in m for m in msgs)


def test_enrolement_refuse_un_changement_de_personne():
    msgs = []
    seq = [Scene(ME)] + [Scene(OTHER)] * 5
    out = engine().enroll(seq, msgs.append)
    assert len(out) == 1
    assert any("visage différent" in m for m in msgs)
