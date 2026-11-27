"""sentineld : le service d'authentification.

Seul ce service touche à la caméra, aux modèles et aux gabarits. Le module PAM et
l'interface graphique ne font que lui envoyer des requêtes, sur un socket Unix local.
En production, systemd l'exécute sans aucun accès réseau (voir packaging/).

Protocole : une requête JSON par ligne, une ou plusieurs réponses JSON par ligne.
L'identité de l'appelant est fournie par le noyau (SO_PEERCRED) : un client ne peut
pas mentir sur qui il est.

Règles d'autorisation :
    authenticate   root (PAM : sudo, GDM…) pour tout utilisateur ; sinon seulement pour
                   soi-même, depuis une session active devant la machine
    enroll_*       pour soi-même, depuis une session active devant la machine, avec
                   confirmation par mot de passe (root : sans mot de passe)
    password_ok    root uniquement (appelé par PAM après un mot de passe correct)
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import pwd
import socketserver
import threading
import time
from pathlib import Path

import numpy as np

from .auth import Engine, Status
from .camera import Camera, CameraError, FrameGrabber, open_camera
from .config import Config, load_config
from .enrollment import EnrollmentSession
from .peer import Peer, in_active_local_session, peer_of, user_has_session
from .store import DEFAULT_USES, Enrollment, StoreError, TemplateStore, validate_username

PROTOCOL = 1
MAX_LINE = 64 * 1024
log = logging.getLogger("sentineld")

# Service PAM -> usage. Un service inconnu est toujours refusé.
# Piles où le visage passe avant le mot de passe à chaque tentative (Plasma 5).
PAUSE_SERVICES = ("kde",)

SERVICES = {
    "sudo": "sudo", "sudo-i": "sudo", "polkit-1": "sudo",
    "gdm-password": "screen", "login": "screen", "lightdm": "screen",
    "sddm": "screen", "kde": "screen", "kde-fingerprint": "screen", "gnome-screensaver": "screen",
    "sentinel-test": "test",
}


def uid_of(user: str) -> int:
    try:
        return pwd.getpwnam(user).pw_uid
    except KeyError:
        raise Refused("error", f"utilisateur inconnu : {user}") from None


class Refused(Exception):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status, self.message = status, message


class State:
    """État partagé du service : moteur, caméra, politique de mot de passe."""

    def __init__(self, cfg: Config, engine: Engine | None = None):
        self.cfg = cfg
        self.store = TemplateStore(cfg.data_dir, cfg.strict_permissions)
        self.engine = engine
        self.camera_lock = threading.Lock()
        self.lock = threading.Lock()
        self.last_password: dict[str, float] = {}
        self.skip_requests: dict[str, float] = {}
        self.password_failures: dict[str, list[float]] = {}
        self.face_paused_until: dict[tuple[str, str], float] = {}

    def get_engine(self) -> Engine:
        if self.engine is None:
            from .vision import Detector, Recognizer, TextureLiveness
            c = self.cfg
            live = TextureLiveness(c.models_dir, c.liveness) if c.liveness.enabled else None
            self.engine = Engine(c, Detector(c.models_dir), Recognizer(c.models_dir), live)
        return self.engine

    def open_camera(self, prefer_identity: str | None = None) -> Camera:
        return open_camera(self.cfg.camera, prefer_identity)

    # --- Politique de mot de passe ----------------------------------------------

    def password_required(self, user: str, enr: Enrollment | None = None) -> bool:
        a = self.cfg.auth
        if not a.require_password_after_boot:
            return False
        # L'utilisateur a choisi le visage dès le démarrage (comme Windows Hello) :
        # pas de mot de passe obligatoire après le démarrage. Le verrouillage après
        # plusieurs échecs, lui, s'applique toujours.
        if enr is not None and enr.uses.get("login"):
            return False
        last = self.last_password.get(user)
        return last is None or time.monotonic() - last > a.password_every_h * 3600

    SKIP_VALIDITY_S = 10.0

    def request_skip(self, user: str) -> None:
        with self.lock:
            self.skip_requests[user] = time.monotonic()

    def skip_pending(self, user: str) -> bool:
        """Une demande « passer au mot de passe » récente (clic juste avant ou pendant
        la vérification) ?"""
        with self.lock:
            t = self.skip_requests.get(user)
        return t is not None and time.monotonic() - t < self.SKIP_VALIDITY_S

    def consume_skip(self, user: str) -> None:
        with self.lock:
            self.skip_requests.pop(user, None)

    def mark_password(self, user: str) -> None:
        with self.lock:
            self.last_password[user] = time.monotonic()
        self.store.set_failures(user, 0)

    def check_password(self, user: str, password: str) -> bool:
        """Mot de passe vérifié par PAM, avec limitation : 3 essais par minute."""
        from .pamauth import check_password

        now = time.monotonic()
        with self.lock:
            recent = [t for t in self.password_failures.get(user, []) if now - t < 60]
            self.password_failures[user] = recent
            if len(recent) >= 3:
                raise Refused("rate_limited", "Trop d'essais : patientez une minute")
        if check_password(user, password):
            self.mark_password(user)
            return True
        with self.lock:
            self.password_failures.setdefault(user, []).append(time.monotonic())
        return False


def _preview(frame: np.ndarray, width: int = 320) -> dict:
    import cv2

    h, w = frame.shape[:2]
    scale = width / w
    small = cv2.resize(frame, (width, round(h * scale)), interpolation=cv2.INTER_AREA)
    ok, jpg = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return {"jpeg": base64.b64encode(jpg.tobytes()).decode() if ok else "", "scale": scale}


class Handler(socketserver.StreamRequestHandler):
    state: State  # injecté par make_server

    # --- E/S -----------------------------------------------------------------

    def send(self, msg: dict) -> None:
        # Accents transmis tels quels (UTF-8) : le module PAM affiche les messages bruts.
        self.wfile.write((json.dumps(msg, ensure_ascii=False) + "\n").encode())
        self.wfile.flush()

    def handle(self) -> None:
        self.peer: Peer = peer_of(self.request)
        self.pending: dict | None = None
        while True:
            line = self.rfile.readline(MAX_LINE + 1)
            if not line:
                return
            if len(line) > MAX_LINE:
                self.send({"ok": False, "status": "error", "message": "requête trop longue"})
                return
            try:
                req = json.loads(line)
                if not isinstance(req, dict):
                    raise ValueError
                op = req.get("op")
                fn = getattr(self, f"op_{op}", None) if isinstance(op, str) and op.isidentifier() else None
                if fn is None:
                    raise Refused("error", f"opération inconnue : {op!r}")
                fn(req)
            except Refused as r:
                self.send({"ok": False, "status": r.status, "message": r.message})
            except (ValueError, StoreError) as exc:
                self.send({"ok": False, "status": "error", "message": str(exc) or "requête invalide"})
            except (BrokenPipeError, ConnectionResetError):
                return
            except CameraError as exc:
                self.send({"ok": False, "status": "camera", "message": str(exc)})
            except Exception:  # noqa: BLE001 — le service ne doit jamais tomber
                log.exception("erreur interne")
                self.send({"ok": False, "status": "error", "message": "erreur interne"})

    # --- Autorisations ---------------------------------------------------------

    def _user(self, req: dict, *, allow_other_if_root: bool = True) -> str:
        user = req.get("user") or self.peer.username
        validate_username(user)
        if self.peer.is_root and allow_other_if_root:
            return user
        if user != self.peer.username:
            raise Refused("forbidden", "opération autorisée pour votre propre compte uniquement")
        return user

    def _require_local(self) -> None:
        if not self.peer.is_root and not in_active_local_session(self.peer):
            raise Refused("forbidden", "réservé à une session active devant la machine")

    # --- Opérations ------------------------------------------------------------

    def op_hello(self, req: dict) -> None:
        self.send({"ok": True, "protocol": PROTOCOL, "user": self.peer.username})

    def op_status(self, req: dict) -> None:
        user = self._user(req)
        st = self.state
        try:
            enr = st.store.load(user)
            damaged = ""
        except StoreError as exc:
            # Gabarit illisible (clé perdue, fichier abîmé) : on le signale, et
            # l'assistant propose de réenregistrer le visage au lieu de bloquer.
            enr, damaged = None, str(exc)
        self.send({"ok": True, "user": user, "enrolled": enr is not None, "damaged": damaged,
                   "uses": enr.uses if enr else dict(DEFAULT_USES),
                   "locked": st.store.failures(user) >= st.cfg.auth.max_failures,
                   "password_required": st.password_required(user)})

    def op_password_ok(self, req: dict) -> None:
        if not self.peer.is_root:
            raise Refused("forbidden", "réservé à root")
        user = validate_username(req.get("user", ""))
        st = self.state
        st.mark_password(user)
        log.info("mot de passe validé user=%s (verrou levé)", user)
        authtok = req.get("authtok")
        if isinstance(authtok, str) and authtok:
            enr = st.store.load(user)
            # Mis à jour seulement si l'utilisateur a choisi le visage au démarrage, et
            # après une vérification indépendante (PAM) : un appelant root qui se
            # tromperait ne peut pas enregistrer un mauvais mot de passe.
            if enr is not None and enr.uses.get("login") and st.store.load_secret(user) != authtok:
                from .pamauth import check_password
                if check_password(user, authtok):
                    st.store.save_secret(user, authtok)
                    log.info("mot de passe du trousseau mis à jour user=%s", user)
        self.send({"ok": True})

    def op_authenticate(self, req: dict) -> None:
        user = self._user(req)
        if not self.peer.is_root:
            self._require_local()
        st, cfg = self.state, self.state.cfg
        service = str(req.get("service", ""))
        use = SERVICES.get(service)
        if use is None:
            raise Refused("disabled", f"service non pris en charge : {service!r}")
        if use == "screen":
            use = "lockscreen" if user_has_session(uid_of(user)) else "login"
        enr = st.store.load(user)
        if enr is None:
            raise Refused("not_enrolled", Status.NOT_ENROLLED.value)
        if use != "test" and not enr.uses.get(use, False):
            raise Refused("disabled", "visage non activé pour cet usage")
        if use != "test" and st.password_required(user, enr):
            raise Refused("password_required", "mot de passe requis (premier déverrouillage)")
        if service in PAUSE_SERVICES and st.face_paused_until.get((user, service), 0.0) > time.monotonic():
            # Échec récent du visage sur cet écran : le mot de passe tapé est vérifié tout
            # de suite, sans caméra (voir AuthConfig.pause_after_failure_s).
            raise Refused("paused", "visage en pause : utilisez votre mot de passe")
        if st.store.failures(user) >= cfg.auth.max_failures:
            log.warning("tentative sur visage verrouillé user=%s service=%s", user, service)
            raise Refused("locked", Status.LOCKED.value)
        if not st.camera_lock.acquire(blocking=False):
            raise Refused("busy", "caméra déjà utilisée par Sentinel")
        try:
            if req.get("progress"):
                # Prévient le client (module PAM) juste avant d'allumer la caméra :
                # « regardez la caméra » ne s'affiche que si elle sert vraiment.
                self.send({"event": "camera"})
            with st.open_camera(enr.camera or None) as cam:
                if cfg.camera.pin_identity and enr.camera and cam.info.identity != enr.camera:
                    raise Refused("camera", "caméra différente de celle de l'enrôlement")
                def challenge(message: str) -> None:
                    if req.get("progress"):
                        self.send({"event": "challenge", "message": message})
                # Caméra ouverte assez longtemps pour un éventuel défi.
                res = st.get_engine().authenticate(
                    cam.frames(cfg.auth.timeout_s + cfg.auth.challenge_timeout_s), enr.embeddings,
                    enr.liveness_threshold, enr.stats.get("reference_pose"),
                    should_abort=lambda: st.skip_pending(user),
                    on_challenge=challenge if req.get("progress") else None)
        finally:
            st.camera_lock.release()
            st.consume_skip(user)
        if service in PAUSE_SERVICES:
            if res.ok or res.status is Status.SKIPPED:
                st.face_paused_until.pop((user, service), None)
            else:
                st.face_paused_until[(user, service)] = time.monotonic() + cfg.auth.pause_after_failure_s
        if res.ok:
            st.store.set_failures(user, 0)
        elif res.status in (Status.SPOOF, Status.NO_MATCH, Status.CHALLENGE_FAILED):
            count = st.store.failures(user) + 1
            st.store.set_failures(user, count)
            # Niveau « avertissement » : journalctl -u sentineld -p warning les isole.
            log.warning("tentative refusée user=%s service=%s motif=%s échecs=%d/%d", user, service,
                        res.status.name, count, cfg.auth.max_failures)
            if count >= cfg.auth.max_failures:
                log.warning("visage verrouillé user=%s : mot de passe exigé jusqu'à la prochaine "
                            "saisie correcte", user)
        lv = res.liveness_scores
        # Mesures numériques uniquement (aucune image) : comprendre un refus a posteriori.
        log.info("authentification user=%s service=%s résultat=%s durée=%.2fs images=%d ignorées=%d "
                 "similarité=%.2f vivant[fenêtre max=%s, min=%s, médiane=%s, max=%s] seuil=%.2f défi=%s rejets=%s "
                 "lumière_visage=%s",
                 user, service, res.status.name, res.elapsed_s, res.frames, res.skipped_pose,
                 res.best_similarity,
                 f"{res.window_liveness:+.2f}" if res.window_liveness > float("-inf") else "-",
                 f"{min(lv):+.2f}" if lv else "-", f"{float(np.median(lv)):+.2f}" if lv else "-",
                 f"{max(lv):+.2f}" if lv else "-", res.liveness_threshold, res.challenge or "-",
                 ",".join(f"{k}:{v}" for k, v in sorted(res.rejected.items(), key=lambda kv: -kv[1])) or "-",
                 f"{float(np.median(res.face_brightness)):.0f}" if res.face_brightness else "-")
        reply = {"ok": res.ok, "status": res.status.name.lower(), "message": res.message,
                 "elapsed": round(res.elapsed_s, 2)}
        if res.ok and self.peer.is_root and use == "login" and enr.uses.get("login"):
            # Connexion par le visage : le mot de passe, s'il est conservé, est rendu au
            # module PAM pour ouvrir le trousseau. Jamais journalisé.
            secret = st.store.load_secret(user)
            if secret is not None:
                reply["authtok"] = secret
        self.send(reply)

    def op_health(self, req: dict) -> None:
        """Contrôle après installation (root) : la clé déchiffre-t-elle bien tous les
        gabarits ? L'installateur ne supprime l'ancienne clé en clair qu'après ce test."""
        if not self.peer.is_root:
            raise Refused("forbidden", "réservé à root")
        st = self.state
        users = st.store.users()
        for user in users:
            st.store.load(user)  # lève une erreur si la clé ne convient pas
        self.send({"ok": True, "users": len(users), "key": st.store.key_source})

    def op_skip(self, req: dict) -> None:
        """« Passer au mot de passe » (bouton clé). À l'écran de connexion, la demande
        vient de GDM (compte système) pour l'utilisateur choisi : autorisée pour tout
        compte depuis une session active devant la machine, car elle ne peut que faire
        demander un mot de passe, jamais donner un accès."""
        user = validate_username(req.get("user") or self.peer.username)
        self._require_local()
        self.state.request_skip(user)
        self.send({"ok": True})

    # --- Enrôlement (interface graphique) --------------------------------------

    def op_enroll_start(self, req: dict) -> None:
        user = self._user(req)
        self._require_local()
        st, cfg = self.state, self.state.cfg
        st.store.check_permissions()
        if not st.camera_lock.acquire(blocking=False):
            raise Refused("busy", "caméra déjà utilisée par Sentinel")
        try:
            session = EnrollmentSession(st.get_engine())
            with st.open_camera() as cam, FrameGrabber(cam, cfg.enroll.timeout_s) as grab:
                camera_id = cam.info.identity
                self._enroll_loop(session, grab, bool(req.get("preview", True)))
        finally:
            st.camera_lock.release()
        out = session.finish()
        self.pending = {"user": user, "outcome": out, "camera": camera_id} if out.ok else None
        # Uniquement des mesures numériques (relief, vivant) : utiles au réglage, sans biométrie.
        log.info("enrôlement user=%s résultat=%s mesures=%s", user, "ok" if out.ok else out.reason,
                 {k: round(v, 3) if isinstance(v, float) else v for k, v in out.stats.items()})
        self.send({"event": "finished", "ok": out.ok, "message": out.reason,
                   "stats": {k: round(v, 3) if isinstance(v, float) else v for k, v in out.stats.items()}})

    PREVIEW_FPS = 15

    def _enroll_loop(self, session: EnrollmentSession, grab: FrameGrabber, preview: bool) -> None:
        """Analyse dans un fil séparé ; ce fil-ci envoie l'aperçu à cadence régulière,
        avec la dernière consigne connue."""
        shared: dict = {"ev": None, "error": None, "stop": False}

        def analyze():
            seq = 0
            try:
                while not shared["stop"]:
                    seq, frame = grab.wait_new(seq)
                    if frame is None:
                        if grab.ended:
                            return
                        continue
                    ev = session.feed(frame)
                    shared["ev"] = ev
                    if ev.done:
                        return
            except BaseException as exc:  # noqa: BLE001 — renvoyée au fil principal
                shared["error"] = exc

        worker = threading.Thread(target=analyze, daemon=True)
        worker.start()
        try:
            while worker.is_alive():
                tick = time.monotonic()
                frame, ev = grab.latest(), shared["ev"]
                if frame is not None and ev is not None:
                    msg = {"event": "progress", "step": ev.step, "instruction": ev.instruction,
                           "progress": round(ev.progress, 3), "hint": ev.hint,
                           "box": list(ev.box) if ev.box else None,
                           "size": [frame.shape[1], frame.shape[0]]}
                    if preview:
                        msg["preview"] = _preview(frame)
                    self.send(msg)
                worker.join(max(0.0, 1 / self.PREVIEW_FPS - (time.monotonic() - tick)))
        finally:
            shared["stop"] = True
            worker.join(timeout=5)
        if shared["error"] is not None:
            raise shared["error"]

    def op_enroll_verify(self, req: dict) -> None:
        """Vérifie, AVANT de l'enregistrer, que le nouveau gabarit reconnaît bien la personne."""
        if not self.pending:
            raise Refused("error", "aucun enrôlement en attente")
        st, cfg = self.state, self.state.cfg
        out = self.pending["outcome"]
        if not st.camera_lock.acquire(blocking=False):
            raise Refused("busy", "caméra déjà utilisée par Sentinel")
        try:
            with st.open_camera(self.pending.get("camera")) as cam:
                res = st.get_engine().authenticate(cam.frames(cfg.auth.timeout_s), out.embeddings,
                                                   out.liveness_threshold, out.stats.get("reference_pose"))
        finally:
            st.camera_lock.release()
        self.send({"ok": res.ok, "status": res.status.name.lower(), "message": res.message,
                   "elapsed": round(res.elapsed_s, 2)})

    def op_enroll_commit(self, req: dict) -> None:
        if not self.pending:
            raise Refused("error", "aucun enrôlement en attente")
        user = self.pending["user"]
        st = self.state
        if not self.peer.is_root and not st.check_password(user, str(req.get("password", ""))):
            raise Refused("bad_password", "mot de passe incorrect")
        uses = req.get("uses") or {}
        out = self.pending["outcome"]
        enr = Enrollment(user=user, embeddings=out.embeddings, camera=self.pending["camera"],
                         liveness_threshold=out.liveness_threshold, stats=out.stats,
                         uses={k: bool(uses.get(k, v)) for k, v in DEFAULT_USES.items()})
        st.store.save(enr)
        self._sync_secret(enr, str(req.get("password", "")))
        self.pending = None
        log.info("enrôlement enregistré user=%s", user)
        self.send({"ok": True, "message": "Visage enregistré"})

    def _sync_secret(self, enr: Enrollment, password: str) -> None:
        """Mot de passe conservé seulement si « connexion au démarrage » est activé."""
        if enr.uses.get("login") and password:
            self.state.store.save_secret(enr.user, password)
        elif not enr.uses.get("login"):
            self.state.store.remove_secret(enr.user)

    def op_set_uses(self, req: dict) -> None:
        user = self._user(req)
        self._require_local()
        st = self.state
        if not self.peer.is_root and not st.check_password(user, str(req.get("password", ""))):
            raise Refused("bad_password", "mot de passe incorrect")
        enr = st.store.load(user)
        if enr is None:
            raise Refused("not_enrolled", Status.NOT_ENROLLED.value)
        # Toutes les options connues, y compris celles apparues après l'enrôlement.
        current = {**DEFAULT_USES, **enr.uses}
        enr.uses = {k: bool((req.get("uses") or {}).get(k, v)) for k, v in current.items()}
        st.store.save(enr)
        self._sync_secret(enr, str(req.get("password", "")))
        self.send({"ok": True, "uses": enr.uses})

    def op_remove(self, req: dict) -> None:
        user = self._user(req)
        self._require_local()
        st = self.state
        if not self.peer.is_root and not st.check_password(user, str(req.get("password", ""))):
            raise Refused("bad_password", "mot de passe incorrect")
        removed = st.store.remove(user)
        log.info("suppression user=%s", user)
        self.send({"ok": True, "removed": removed})


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def make_server(state: State, path: Path) -> Server:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_socket():
        path.unlink()
    handler = type("BoundHandler", (Handler,), {"state": state})
    old = os.umask(0o111)  # socket ouvert à tous : l'autorisation se fait par SO_PEERCRED
    try:
        return Server(str(path), handler)
    finally:
        os.umask(old)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sentineld", description="Service d'authentification Sentinel")
    ap.add_argument("--socket", type=Path, help="chemin du socket (défaut : configuration)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = load_config()
    state = State(cfg)
    if os.geteuid() == 0 or not cfg.strict_permissions:
        state.store.init()          # crée le stockage au premier lancement
    else:
        state.store.check_permissions()
    state.get_engine()  # modèles chargés une fois, au démarrage
    protection = os.environ.get("SENTINEL_KEY_PROTECTION", "")
    log.info("clé de chiffrement : %s%s", state.store.key_source,
             f" ({protection})" if state.store.key_source == "coffre" and protection else "")
    path = args.socket or cfg.socket_path
    import signal

    # Arrêt propre sur SIGTERM (systemd) comme sur Ctrl+C : le socket est retiré.
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    with make_server(state, path) as srv:
        log.info("prêt sur %s", path)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
