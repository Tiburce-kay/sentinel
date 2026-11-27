"""Service Sentinel pour les tests en conteneur (jamais installé par le paquet).

Le vrai service, avec deux substitutions qu'un conteneur ne peut pas fournir :
- une « session graphique active » (logind absent du conteneur) ;
- une caméra qui filme une pièce vide (image grise, aucun visage).
Un visage fictif est enregistré pour l'utilisateur donné. Le moteur, les modèles, le
socket, les autorisations et la pause du visage sont les vrais.

    /opt/sentinel/venv/bin/python -I sentineld_essai.py UTILISATEUR
"""
import sys
import time

import numpy as np

import sentinel.config as config
import sentinel.daemon as d
from sentinel.store import Enrollment

user = sys.argv[1]
_load = config.load_config


def load_config(path=None):
    cfg = _load(path)
    cfg.auth.require_password_after_boot = False
    return cfg


class EmptyRoom:
    info = type("Info", (), {"identity": ""})()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def frames(self, timeout_s):
        end = time.monotonic() + timeout_s
        while time.monotonic() < end:
            time.sleep(1 / 15)
            yield np.full((480, 640, 3), 128, np.uint8)


d.load_config = load_config
d.in_active_local_session = lambda peer: True
d.user_has_session = lambda uid: True
d.State.open_camera = lambda self, prefer_identity=None: EmptyRoom()
_init = d.State.__init__


def __init__(self, cfg, engine=None):
    _init(self, cfg, engine)
    self.store.init()
    if self.store.load(user) is None:
        v = np.random.default_rng(0).normal(size=(5, 128)).astype(np.float32)
        self.store.save(Enrollment(user=user, embeddings=v / np.linalg.norm(v, axis=1, keepdims=True),
                                   uses={"sudo": True, "lockscreen": True, "login": False}))


d.State.__init__ = __init__
sys.exit(d.main([]))
