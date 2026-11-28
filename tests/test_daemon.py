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
    state.open_camera = lambda *a: FakeCamera(state.scenes)
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


def test_photo_refusee_puis_verrouillage(service, caplog):
    import logging
    caplog.set_level(logging.WARNING, logger="sentineld")
    enroll(service)
    service.scenes = [Shot(ME, -6.0)] * 20
    with service.client() as c:
        for _ in range(service.cfg.auth.max_failures):
            assert c.call("authenticate", service="sudo")["status"] == "spoof"
        service.scenes = [Shot(ME, 1.0)] * 20
        assert c.call("authenticate", service="sudo")["status"] == "locked"
        # Chaque tentative refusée est journalisée, puis le verrouillage.
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert sum("tentative refusée" in w and "motif=SPOOF" in w for w in warnings) == service.cfg.auth.max_failures
        assert any("visage verrouillé" in w for w in warnings)
        assert any("tentative sur visage verrouillé" in w and "service=sudo" in w for w in warnings)
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


def test_deux_comptes_chacun_son_visage(service, monkeypatch):
    """PC partagé : le module PAM (root) demande le visage du compte qui lance sudo.
    Le frère non enregistré passe au mot de passe ; une fois enregistré, seul SON
    visage ouvre SON compte, et le visage d'alice n'ouvre pas le sien (ni l'inverse)."""
    import numpy as np
    from sentinel.store import Enrollment
    enroll(service)                                   # alice : visage ME
    as_root(service, monkeypatch)
    service.scenes = [Shot(ME, 1.0)] * 20
    with service.client() as c:
        assert c.call("authenticate", user="frere", service="sudo")["status"] == "not_enrolled"
    alice = service.store.load("alice")
    service.store.save(Enrollment(user="frere", embeddings=np.stack([OTHER]).astype(np.float32),
                                  camera=alice.camera, liveness_threshold=alice.liveness_threshold,
                                  stats=alice.stats))
    with service.client() as c:
        assert c.call("authenticate", user="frere", service="sudo")["status"] == "no_match"  # visage d'alice
        assert c.call("authenticate", user="alice", service="sudo")["ok"]
        service.scenes = [Shot(OTHER, 1.0)] * 20
        assert c.call("authenticate", user="frere", service="sudo")["ok"]
        assert c.call("authenticate", user="alice", service="sudo")["status"] == "no_match"  # visage du frère
    # Les échecs sont comptés séparément pour chaque compte.
    assert service.store.failures("frere") == 0 and service.store.failures("alice") == 1


def test_plasma5_pause_apres_echec_puis_reprise(service):
    """Écran de Plasma 5 (service « kde ») : après un échec du visage, le mot de passe
    tapé ensuite doit être vérifié sans attendre la caméra ; le visage revient ensuite."""
    import time
    enroll(service)
    service.cfg.auth.pause_after_failure_s = 0.3
    with service.client() as c:
        service.scenes = [Shot(OTHER, 1.0)] * 20                      # échec du visage
        assert not c.call("authenticate", service="kde")["ok"]
        service.scenes = [Shot(ME, 1.0)] * 20
        r = c.call("authenticate", service="kde")                     # pause : pas de caméra
        assert r["status"] == "paused"
        assert c.call("authenticate", service="sudo")["ok"]           # autres services : non concernés
        time.sleep(0.35)
        assert c.call("authenticate", service="kde")["ok"]            # pause terminée
        assert c.call("authenticate", service="kde")["ok"]            # un succès ne met pas en pause


def test_seuil_personnel_recalibre_dans_les_conditions_reelles(service):
    """Retour de testeur (28/11) : enregistrement sous écran blanc -> seuil −1,26, mais
    au quotidien ses fenêtres de vivant sont plus basses. La vérification, faite sans
    écran blanc, abaisse le seuil, jamais sous le plancher validé."""
    with service.client() as c:
        list(c.stream("enroll_start", preview=False))
        service.scenes = [Shot(ME, -1.8)] * 20                       # visage réel, moins éclairé
        r = c.call("enroll_verify")
        assert r["ok"], r
        assert c.call("enroll_commit", password=PASSWORD, uses={})["ok"]
    t = service.store.load("alice").liveness_threshold
    assert t == service.cfg.liveness.floor                           # −1,8 − 0,5 borné à −2,0


def test_verification_dans_le_noir_refusee_sans_abaisser_sous_le_plancher(service):
    with service.client() as c:
        list(c.stream("enroll_start", preview=False))
        service.scenes = [Shot(ME, -4.0)] * 20                       # comme une photo
        r = c.call("enroll_verify")
        assert not r["ok"] and r["status"] == "spoof"


def test_client_parti_pendant_une_erreur_sans_erreur_interne(service, caplog, capfd):
    """Assistant fermé avant la réponse « mot de passe incorrect » : pas de trace
    d'« erreur interne » dans le journal (vu chez un testeur le 28/11)."""
    import logging
    import socket
    caplog.set_level(logging.ERROR, logger="sentineld")
    enroll(service)
    c = service.client()
    c.sock.sendall(b'{"op": "set_uses", "password": "faux", "uses": {}}\n')
    c.sock.shutdown(socket.SHUT_RDWR)
    c.sock.close()
    import time
    time.sleep(1.0)
    assert not [r for r in caplog.records if "erreur interne" in r.getMessage()]
    assert "BrokenPipeError" not in capfd.readouterr().err


def test_seuil_realiste_unitaire():
    from sentinel.config import Config
    from sentinel.daemon import realistic_threshold
    cfg = Config()
    assert realistic_threshold(-1.26, [0.0] * 5, cfg) == -1.26                    # trop peu d'images
    assert abs(realistic_threshold(-1.26, [-1.2] * 20, cfg) - -1.7) < 1e-9        # −1,2 − 0,5
    assert realistic_threshold(-1.26, [-3.0] * 20, cfg) == cfg.liveness.floor     # jamais sous −2
    assert realistic_threshold(-1.26, [2.0] * 20, cfg) == -1.26                   # jamais plus strict
