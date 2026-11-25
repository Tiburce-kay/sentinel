"""Service sentineld de bout en bout : vrai socket Unix, caméra et modèles simulés."""

import threading

import pytest

import sentinel.daemon as d
from sentinel.auth import Engine
from sentinel.client import Client
from sentinel.config import Config
from sentinel.enrollment import STEPS
from sentinel.vision import Quality
from tests.test_enrollment import ME, OTHER, POSES, Detector, Liveness, Recognizer, Shot

PASSWORD = "bon-mot-de-passe"


class FakeCamera:
    drops_frames = False  # caméra simulée : aucune image sautée

    def __init__(self, scenes):
        self.scenes = scenes
        self.info = type("Info", (), {"identity": "04f2:b5ab||cam"})()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def frames(self, timeout):
        return iter(list(self.scenes))


def enrollment_scenes():
    from tests.test_enrollment import user_frames
    return list(user_frames())


@pytest.fixture
def service(tmp_path, monkeypatch):
    cfg = Config(data_dir=tmp_path / "store", strict_permissions=False)
    cfg.auth.require_password_after_boot = False
    engine = Engine(cfg, Detector(), Recognizer(), Liveness(), quality=lambda fr, f: Quality(True))
    state = d.State(cfg, engine)
    state.store.init()
    state.scenes = enrollment_scenes()
    state.open_camera = lambda: FakeCamera(state.scenes)
    monkeypatch.setattr(d, "in_active_local_session", lambda peer: state.local)
    monkeypatch.setattr(d, "user_has_session", lambda uid: True)
    import sentinel.pamauth
    monkeypatch.setattr(sentinel.pamauth, "check_password", lambda user, pw: pw == PASSWORD)
    state.local = True
    sock = tmp_path / "s.sock"
    srv = d.make_server(state, sock)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    state.client = lambda: Client(sock, timeout=10)
    yield state
    srv.shutdown()
    srv.server_close()


def enroll(state, password=PASSWORD, uses=None):
    with state.client() as c:
        msgs = list(c.stream("enroll_start", preview=False))
        assert msgs[-1]["event"] == "finished" and msgs[-1]["ok"], msgs[-1]
        assert set(msgs[-1]["stats"]["poses"]) == {"gauche", "droite", "haut", "bas"}
        r = c.call("enroll_commit", password=password, uses=uses or {})
        return msgs, r


def test_hello(service):
    with service.client() as c:
        r = c.call("hello")
    assert r["ok"] and r["protocol"] == d.PROTOCOL


def test_parcours_complet(service):
    with service.client() as c:
        assert c.call("authenticate", service="sudo")["status"] == "not_enrolled"
    msgs, r = enroll(service)
    assert r["ok"]
    service.scenes = [Shot(ME, 1.0)] * 20
    with service.client() as c:
        st = c.call("status")
        assert st["enrolled"] and st["uses"]["sudo"]
        r = c.call("authenticate", service="sudo")
    assert r["ok"] and r["status"] == "success"


def test_verification_avant_enregistrement(service):
    with service.client() as c:
        list(c.stream("enroll_start", preview=False))
        service.scenes = [Shot(ME, 1.0)] * 20
        assert c.call("enroll_verify")["ok"]
        service.scenes = [Shot(OTHER, 1.0)] * 20
        assert not c.call("enroll_verify")["ok"]


def test_mauvais_mot_de_passe_n_enregistre_rien(service):
    _, r = enroll(service, password="faux")
    assert r["status"] == "bad_password"
    with service.client() as c:
        assert not c.call("status")["enrolled"]


def test_limitation_des_essais_de_mot_de_passe(service):
    with service.client() as c:
        list(c.stream("enroll_start", preview=False))
        for _ in range(3):
            assert c.call("enroll_commit", password="faux")["status"] == "bad_password"
        assert c.call("enroll_commit", password=PASSWORD)["status"] == "rate_limited"


def test_photo_refusee_puis_verrouillage(service):
    enroll(service)
    service.scenes = [Shot(ME, -6.0)] * 20
    with service.client() as c:
        for _ in range(service.cfg.auth.max_failures):
            assert c.call("authenticate", service="sudo")["status"] == "spoof"
        service.scenes = [Shot(ME, 1.0)] * 20
        assert c.call("authenticate", service="sudo")["status"] == "locked"
        # Seul root (PAM, après un mot de passe correct) peut lever le verrou.
        assert c.call("password_ok", user=c.call("hello")["user"])["status"] == "forbidden"


def test_service_inconnu_ou_desactive(service):
    enroll(service, uses={"sudo": False})
    service.scenes = [Shot(ME, 1.0)] * 20
    with service.client() as c:
        assert c.call("authenticate", service="ssh")["status"] == "disabled"
        assert c.call("authenticate", service="sudo")["status"] == "disabled"
        assert c.call("authenticate", service="gdm-password")["ok"]  # écran de verrouillage


def test_autre_utilisateur_interdit(service):
    with service.client() as c:
        assert c.call("authenticate", user="root", service="sudo")["status"] == "forbidden"
        assert c.call("status", user="root")["status"] == "forbidden"


def test_session_distante_interdite(service):
    service.local = False
    with service.client() as c:
        assert c.call("authenticate", service="sudo")["status"] == "forbidden"
        assert list(c.stream("enroll_start"))[-1]["status"] == "forbidden"


def test_mot_de_passe_exige_apres_demarrage(service):
    enroll(service)
    service.cfg.auth.require_password_after_boot = True
    service.last_password.clear()
    with service.client() as c:
        assert c.call("authenticate", service="sudo")["status"] == "password_required"


def test_requetes_invalides(service):
    with service.client() as c:
        c.sock.sendall(b"pas du json\n")
        assert c._recv()["status"] == "error"
        assert c.call("__class__")["status"] == "error"
        assert c.call("inexistante")["status"] == "error"
        c.sock.sendall(b"x" * (d.MAX_LINE + 10) + b"\n")
        assert "trop longue" in c._recv()["message"]


def test_suppression_avec_mot_de_passe(service):
    enroll(service)
    with service.client() as c:
        assert c.call("remove", password="faux")["status"] == "bad_password"
        assert c.call("remove", password=PASSWORD)["removed"]
        assert not c.call("status")["enrolled"]
