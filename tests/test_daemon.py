"""Service sentineld de bout en bout : vrai socket Unix, caméra et modèles simulés."""

import os
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


class FakePeer(d.Peer):
    @property
    def username(self) -> str:
        return "alice"


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
    # Appelant simulé : un utilisateur ordinaire, quel que soit l'utilisateur qui lance
    # les tests (en conteneur, c'est root, à qui le service accorde tout).
    monkeypatch.setattr(d, "peer_of", lambda sock: FakePeer(os.getpid(), 4242, 4242))
    monkeypatch.setattr(d, "uid_of", lambda user: 4242)
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


# --- Visage au démarrage, trousseau, bouton clé -----------------------------------

def as_root(service_state, monkeypatch):
    monkeypatch.setattr(d, "peer_of", lambda sock: FakePeer(os.getpid(), 0, 0))


def test_demarrage_par_le_visage_rend_le_mot_de_passe_au_seul_module_root(service, monkeypatch):
    enroll(service, uses={"login": True})
    assert service.store.load_secret("alice") == PASSWORD
    service.cfg.auth.require_password_after_boot = True
    service.last_password.clear()
    monkeypatch.setattr(d, "user_has_session", lambda uid: False)  # pas de session : connexion
    service.scenes = [Shot(ME, 1.0)] * 20
    with service.client() as c:  # appelant ordinaire : jamais de mot de passe
        r = c.call("authenticate", service="gdm-password")
        assert r["ok"] and "authtok" not in r
    as_root(service, monkeypatch)
    with service.client() as c:
        r = c.call("authenticate", user="alice", service="gdm-password")
        assert r["ok"] and r["authtok"] == PASSWORD
        r = c.call("authenticate", user="alice", service="sudo")
        assert r["ok"] and "authtok" not in r  # uniquement pour ouvrir une session


def test_sans_option_demarrage_le_mot_de_passe_reste_exige_et_rien_n_est_stocke(service, monkeypatch):
    enroll(service)
    assert service.store.load_secret("alice") is None
    service.cfg.auth.require_password_after_boot = True
    service.last_password.clear()
    with service.client() as c:
        assert c.call("authenticate", service="sudo")["status"] == "password_required"


def test_photo_au_demarrage_ne_rend_rien(service, monkeypatch):
    enroll(service, uses={"login": True})
    monkeypatch.setattr(d, "user_has_session", lambda uid: False)
    as_root(service, monkeypatch)
    service.scenes = [Shot(ME, -6.0)] * 20
    with service.client() as c:
        r = c.call("authenticate", user="alice", service="gdm-password")
    assert not r["ok"] and "authtok" not in r


def test_bouton_cle_interrompt_la_verification(service):
    enroll(service)
    service.scenes = [Shot(ME, 1.0)] * 20
    with service.client() as c:
        assert c.call("skip")["ok"]
        r = c.call("authenticate", service="sudo")
        assert r["status"] == "skipped"
        # La demande est consommée : la tentative suivante utilise de nouveau le visage.
        assert c.call("authenticate", service="sudo")["ok"]


def test_mot_de_passe_change_mis_a_jour_apres_verification(service, monkeypatch):
    import sentinel.pamauth
    enroll(service, uses={"login": True})
    as_root(service, monkeypatch)
    monkeypatch.setattr(sentinel.pamauth, "check_password", lambda u, p: p == "Nouveau-77")
    with service.client() as c:
        c.call("password_ok", user="alice", authtok="Faux")
        assert service.store.load_secret("alice") == PASSWORD  # refusé par PAM
        c.call("password_ok", user="alice", authtok="Nouveau-77")
    assert service.store.load_secret("alice") == "Nouveau-77"


def test_desactiver_le_demarrage_efface_le_mot_de_passe(service):
    enroll(service, uses={"login": True})
    with service.client() as c:
        r = c.call("set_uses", password=PASSWORD, uses={"login": False})
    assert r["ok"] and service.store.load_secret("alice") is None


def test_defi_transmis_au_client_et_echec_compte(service):
    from tests.test_enrollment import FixedLiveness
    enroll(service)
    service.engine.liveness = FixedLiveness(0.0)
    service.engine.choose = lambda options: "droite"
    service.scenes = [Shot(ME, -0.5)] * 60   # vivant « limite », ne bouge pas
    with service.client() as c:
        c._send({"op": "authenticate", "service": "sudo", "progress": True})
        lines = [c._recv() for _ in range(3)]
    assert lines[0] == {"event": "camera"}
    assert lines[1] == {"event": "challenge", "message": "Tournez la tête vers votre droite"}
    assert lines[2]["status"] == "challenge_failed"
    assert service.store.failures("alice") == 1


def test_ecran_de_connexion_peut_demander_le_mot_de_passe_pour_un_utilisateur(service):
    """GDM (autre compte) demande « passer au mot de passe » pour l'utilisateur choisi."""
    enroll(service)
    service.scenes = [Shot(ME, 1.0)] * 20
    with service.client() as c:
        assert c.call("skip", user="bob")["ok"]
        assert "bob" in service.skip_requests


def test_demande_de_mot_de_passe_refusee_a_distance(service):
    service.local = False
    with service.client() as c:
        assert c.call("skip", user="alice")["status"] == "forbidden"


def test_nouvelles_options_activables_sur_un_ancien_enrolement(service):
    enroll(service)
    enr = service.store.load("alice")
    enr.uses = {"sudo": True, "lockscreen": True}  # enrôlement d'avant l'option « login »
    service.store.save(enr)
    with service.client() as c:
        r = c.call("set_uses", password=PASSWORD, uses={"login": True})
    assert r["ok"] and r["uses"]["login"] is True


def test_controle_de_sante_reserve_a_root(service, monkeypatch):
    enroll(service)
    with service.client() as c:
        assert c.call("health")["status"] == "forbidden"
    monkeypatch.setattr(d, "peer_of", lambda sock: FakePeer(os.getpid(), 0, 0))
    with service.client() as c:
        r = c.call("health")
    assert r == {"ok": True, "users": 1, "key": "fichier"}


def test_gabarit_illisible_signale_sans_bloquer(service):
    enroll(service)
    path = service.store.users_dir / "alice.tpl"
    raw = bytearray(path.read_bytes())
    raw[-10] ^= 1
    path.write_bytes(bytes(raw))
    with service.client() as c:
        st = c.call("status")
    assert st["ok"] and not st["enrolled"] and st["damaged"]
    enroll(service)                      # le réenregistrement remplace le gabarit abîmé
    with service.client() as c:
        st = c.call("status")
    assert st["enrolled"] and not st["damaged"]
