"""Module PAM réel (C), chargé par une vraie pile PAM, face à un faux service.

Compile pam_sentinel.so et le programme de test, écrit une configuration PAM dans
un dossier temporaire (pam_start_confdir : /etc/pam.d n'est jamais touché), et
rejoue chaque réponse possible du service — dont le parcours complet avec caméra,
qu'un bug de comparaison de préfixe avait cassé (essai réel du 25/11).
"""

import json
import os
import shutil
import socket
import subprocess
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    not (shutil.which("cc") and Path("/usr/include/security/pam_modules.h").exists()),
    reason="compilateur C ou en-têtes PAM (libpam0g-dev) absents",
)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("pam")
    for f in ("pam_sentinel.c", "pam_harness.c", "Makefile"):
        shutil.copy(ROOT / "pam" / f, out / f)
    subprocess.run(["make", "-s", "-C", str(out), "all", "harness"], check=True)
    yield out
    # Des exécutables laissés dans /tmp déclenchent les alertes des détecteurs de
    # rootkits (chkrootkit : « possible Linux.Xor.DDoS ») : on les efface.
    shutil.rmtree(out, ignore_errors=True)


class FakeService:
    """Faux sentineld : renvoie les lignes prévues, enregistre les requêtes reçues."""

    def __init__(self, path: Path, replies: list[dict]):
        self.requests: list[dict] = []
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(str(path))
        self.sock.listen(4)
        self.replies = replies
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                self.requests.append(json.loads(conn.makefile().readline()))
                for r in self.replies:
                    conn.sendall((json.dumps(r, ensure_ascii=False) + "\n").encode())

    def close(self):
        self.sock.close()


def run(built: Path, tmp: Path, replies, *, user="alice", service="sudo", env=None, notify=False,
        then="pam_deny.so"):
    sock = tmp / "s.sock"
    conf = tmp / "conf"
    conf.mkdir(exist_ok=True)
    opt = "notify" if notify else ""
    (conf / service).write_text(
        f"auth sufficient {built}/pam_sentinel.so socket={sock} timeout=3 {opt}\n"
        f"auth required {then}\n")
    fake = FakeService(sock, replies) if replies is not None else None
    try:
        p = subprocess.run([str(built / "pam-harness"), str(conf), service, user],
                           capture_output=True, text=True, env={**os.environ, **(env or {})}, timeout=15)
    finally:
        if fake:
            fake.close()
        sock.unlink(missing_ok=True)
    return p.stdout, (fake.requests if fake else [])


def test_parcours_complet_avec_camera(built, tmp_path):
    out, reqs = run(built, tmp_path, [{"event": "camera"},
                                      {"ok": True, "status": "success", "message": "succès"}])
    assert "regardez la caméra" in out
    assert "visage reconnu" in out
    assert "résultat PAM : Success" in out
    assert reqs == [{"op": "authenticate", "user": "alice", "service": "sudo", "progress": True}]


def test_usurpation_refusee_avec_message(built, tmp_path):
    out, _ = run(built, tmp_path, [{"event": "camera"},
                                   {"ok": False, "status": "spoof", "message": "usurpation suspectée"}])
    assert "regardez la caméra" in out and "usurpation suspectée" in out
    assert "Success" not in out


def test_succes_sans_message_camera(built, tmp_path):
    out, _ = run(built, tmp_path, [{"ok": True, "status": "success", "message": "succès"}])
    assert "résultat PAM : Success" in out and "regardez" not in out


@pytest.mark.parametrize("status", ["not_enrolled", "disabled"])
def test_silencieux_si_visage_absent_ou_desactive(built, tmp_path, status):
    out, _ = run(built, tmp_path, [{"ok": False, "status": status, "message": "x"}])
    assert "[message]" not in out and "Success" not in out


def test_mot_de_passe_requis_affiche(built, tmp_path):
    out, _ = run(built, tmp_path, [{"ok": False, "status": "password_required",
                                    "message": "mot de passe requis"}])
    assert "mot de passe requis" in out and "regardez" not in out


def test_service_absent(built, tmp_path):
    out, _ = run(built, tmp_path, None)
    assert "Success" not in out and "[message]" not in out


def test_service_muet_delai_respecte(built, tmp_path):
    """Le service accepte mais ne répond jamais : le module abandonne après le délai."""
    import time
    t = time.monotonic()
    out, _ = run(built, tmp_path, [])
    assert "Success" not in out and time.monotonic() - t < 8


def test_connexion_distante_aucune_requete(built, tmp_path):
    out, reqs = run(built, tmp_path, [{"ok": True}], env={"PAM_RHOST": "203.0.113.7"})
    assert reqs == [] and "Success" not in out


def test_nom_piege_aucune_requete(built, tmp_path):
    out, reqs = run(built, tmp_path, [{"ok": True}], user='a", "op": "remove')
    assert reqs == [] and "Success" not in out


def test_reponse_falsifiee_sans_ok_refusee(built, tmp_path):
    """Seule une réponse commençant exactement par {"ok": true compte comme un succès."""
    out, _ = run(built, tmp_path, [{"status": "success", "ok": True}])
    assert "Success" not in out


def test_notification_mot_de_passe(built, tmp_path):
    out, reqs = run(built, tmp_path, [{"ok": True}], notify=True)
    assert reqs == [{"op": "password_ok", "user": "alice"}]


# --- Disposition « trousseau » de l'écran de connexion, avec la vraie libpam ----------

PAM_EXEC = Path("/usr/lib/x86_64-linux-gnu/security/pam_exec.so")


def keyring_stack(built: Path, tmp: Path, replies, password_ok: bool):
    """Pile identique à celle de gdm-password ; pam_exec remplace pam_gnome_keyring
    pour observer le mot de passe qu'il recevrait."""
    sock, conf, seen = tmp / "s.sock", tmp / "conf", tmp / "vu.txt"
    conf.mkdir(exist_ok=True)
    spy = tmp / "espion.sh"
    spy.write_text(f"#!/bin/sh\ncat > {seen}\n")
    spy.chmod(0o755)
    (conf / "gdm-password").write_text(
        f"auth [success=ok default=2] {built}/pam_sentinel.so socket={sock} timeout=3\n"
        f"auth optional pam_exec.so expose_authtok quiet {spy}\n"
        "auth [success=done default=die] pam_permit.so\n"
        f"auth required {'pam_permit.so' if password_ok else 'pam_deny.so'}\n")
    fake = FakeService(sock, replies) if replies is not None else None
    try:
        p = subprocess.run([str(built / "pam-harness"), str(conf), "gdm-password", "alice"],
                           capture_output=True, text=True, timeout=15)
    finally:
        if fake:
            fake.close()
        sock.unlink(missing_ok=True)
    return p.stdout, (seen.read_text().rstrip("\0") if seen.exists() else None)


needs_exec = pytest.mark.skipif(not PAM_EXEC.exists(), reason="pam_exec absent")


@needs_exec
def test_visage_au_demarrage_transmet_le_mot_de_passe_au_trousseau(built, tmp_path):
    secret = 'Mdp "complexe" \\ é 42'
    out, seen = keyring_stack(built, tmp_path, [{"event": "camera"},
                              {"ok": True, "status": "success", "authtok": secret}], password_ok=False)
    assert "Success" in out          # accepté sans passer par le mot de passe
    assert seen == secret            # le trousseau aurait reçu le bon mot de passe


@needs_exec
def test_echec_du_visage_ne_fait_pas_echouer_un_bon_mot_de_passe(built, tmp_path):
    out, seen = keyring_stack(built, tmp_path, [{"event": "camera"},
                              {"ok": False, "status": "spoof", "message": "usurpation"}], password_ok=True)
    assert "Success" in out and seen is None


@needs_exec
def test_echec_du_visage_et_mauvais_mot_de_passe(built, tmp_path):
    out, _ = keyring_stack(built, tmp_path, [{"ok": False, "status": "no_face", "message": "x"}],
                           password_ok=False)
    assert "Success" not in out


@needs_exec
def test_visage_non_enregistre_mene_au_mot_de_passe(built, tmp_path):
    out, seen = keyring_stack(built, tmp_path, [{"ok": False, "status": "not_enrolled"}], password_ok=True)
    assert "Success" in out and seen is None


def test_notify_transmet_le_mot_de_passe_echappe(built, tmp_path):
    """Aller-retour complet : le mot de passe reçu par une ligne (visage) est renvoyé
    intact par la ligne notify, même avec guillemets, barre oblique et caractère de contrôle."""
    secret = 'a"b\\c\x01d é'
    a, b, conf = tmp_path / "a.sock", tmp_path / "b.sock", tmp_path / "conf"
    conf.mkdir()
    (conf / "sudo").write_text(
        f"auth optional {built}/pam_sentinel.so socket={a}\n"
        f"auth optional {built}/pam_sentinel.so socket={b} notify\n")
    face = FakeService(a, [{"ok": True, "status": "success", "authtok": secret}])
    notify = FakeService(b, [{"ok": True}])
    try:
        subprocess.run([str(built / "pam-harness"), str(conf), "sudo", "alice"],
                       capture_output=True, timeout=15)
    finally:
        face.close()
        notify.close()
    assert notify.requests == [{"op": "password_ok", "user": "alice", "authtok": secret}]


def test_defi_affiche_puis_reussi(built, tmp_path):
    out, _ = run(built, tmp_path, [{"event": "camera"},
                                   {"event": "challenge", "message": "Tournez la tête vers votre gauche"},
                                   {"ok": True, "status": "success"}])
    assert "Tournez la tête vers votre gauche" in out
    assert "résultat PAM : Success" in out


# --- Secours : quoi qu'il arrive au visage, le mot de passe reste possible ----------

SECOURS = {
    "webcam cassée ou absente": [{"ok": False, "status": "camera", "message": "aucune webcam utilisable trouvée"}],
    "trop sombre": [{"event": "camera"}, {"ok": False, "status": "too_dark", "message": "trop sombre"}],
    "aucun visage": [{"event": "camera"}, {"ok": False, "status": "no_face", "message": "aucun visage"}],
    "verrouillé après 5 échecs": [{"ok": False, "status": "locked", "message": "verrouillé"}],
    "erreur interne du service": [{"ok": False, "status": "error", "message": "erreur interne"}],
    "réponse illisible": ["n'importe quoi"],
    "service arrêté": None,
}


@pytest.mark.parametrize("cas", SECOURS)
def test_sudo_secours_par_mot_de_passe(built, tmp_path, cas):
    """Pile de sudo réelle (« sufficient » puis mot de passe) : le visage échoue, le
    mot de passe (ici pam_permit, comme un bon mot de passe) prend le relais."""
    out, _ = run(built, tmp_path, SECOURS[cas], then="pam_permit.so")
    assert "résultat PAM : Success" in out


@needs_exec
@pytest.mark.parametrize("cas", SECOURS)
def test_ecran_de_connexion_secours_par_mot_de_passe(built, tmp_path, cas):
    out, seen = keyring_stack(built, tmp_path, SECOURS[cas], password_ok=True)
    assert "Success" in out and seen is None
