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
from .camera import Camera, CameraError, select_camera
from .config import Config, load_config
from .enrollment import EnrollmentSession
from .peer import Peer, in_active_local_session, peer_of, user_has_session
from .store import DEFAULT_USES, Enrollment, StoreError, TemplateStore, validate_username

PROTOCOL = 1
MAX_LINE = 64 * 1024
log = logging.getLogger("sentineld")

# Service PAM -> usage. Un service inconnu est toujours refusé.
SERVICES = {
    "sudo": "sudo", "sudo-i": "sudo", "polkit-1": "sudo",
    "gdm-password": "screen", "login": "screen", "lightdm": "screen",
    "sddm": "screen", "kde": "screen", "gnome-screensaver": "screen",
    "sentinel-test": "test",
}


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
        self.password_failures: dict[str, list[float]] = {}

    def get_engine(self) -> Engine:
        if self.engine is None:
            from .vision import Detector, Recognizer, TextureLiveness
            c = self.cfg
            live = TextureLiveness(c.models_dir, c.liveness) if c.liveness.enabled else None
            self.engine = Engine(c, Detector(c.models_dir), Recognizer(c.models_dir), live)
        return self.engine

    def open_camera(self) -> Camera:
        c = self.cfg.camera
        return Camera(select_camera(c.device, c.require_uvc), c.width, c.height, c.warmup_frames)

    # --- Politique de mot de passe ----------------------------------------------

    def password_required(self, user: str) -> bool:
        a = self.cfg.auth
        if not a.require_password_after_boot:
            return False
        last = self.last_password.get(user)
        return last is None or time.monotonic() - last > a.password_every_h * 3600

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
        self.wfile.write((json.dumps(msg) + "\n").encode())
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
        enr = st.store.load(user)
        self.send({"ok": True, "user": user, "enrolled": enr is not None,
                   "uses": enr.uses if enr else dict(DEFAULT_USES),
                   "locked": st.store.failures(user) >= st.cfg.auth.max_failures,
                   "password_required": st.password_required(user)})

    def op_password_ok(self, req: dict) -> None:
        if not self.peer.is_root:
            raise Refused("forbidden", "réservé à root")
        user = validate_username(req.get("user", ""))
        self.state.mark_password(user)
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
            use = "lockscreen" if user_has_session(pwd.getpwnam(user).pw_uid) else "login"
        enr = st.store.load(user)
        if enr is None:
            raise Refused("not_enrolled", Status.NOT_ENROLLED.value)
        if use != "test" and not enr.uses.get(use, False):
            raise Refused("disabled", "visage non activé pour cet usage")
        if use != "test" and st.password_required(user):
            raise Refused("password_required", "mot de passe requis (premier déverrouillage)")
        if st.store.failures(user) >= cfg.auth.max_failures:
            raise Refused("locked", Status.LOCKED.value)
        if not st.camera_lock.acquire(blocking=False):
            raise Refused("busy", "caméra déjà utilisée par Sentinel")
        try:
            with st.open_camera() as cam:
                if cfg.camera.pin_identity and enr.camera and cam.info.identity != enr.camera:
                    raise Refused("camera", "caméra différente de celle de l'enrôlement")
                res = st.get_engine().authenticate(cam.frames(cfg.auth.timeout_s), enr.embeddings,
                                                   enr.liveness_threshold)
        finally:
            st.camera_lock.release()
        if res.ok:
            st.store.set_failures(user, 0)
        elif res.status in (Status.SPOOF, Status.NO_MATCH):
            st.store.set_failures(user, st.store.failures(user) + 1)
        log.info("authentification user=%s service=%s résultat=%s durée=%.2fs",
                 user, service, res.status.name, res.elapsed_s)
        self.send({"ok": res.ok, "status": res.status.name.lower(), "message": res.status.value,
                   "elapsed": round(res.elapsed_s, 2)})

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
            with st.open_camera() as cam:
                camera_id = cam.info.identity
                for frame in cam.frames(cfg.enroll.timeout_s):
                    ev = session.feed(frame)
                    msg = {"event": "progress", "step": ev.step, "instruction": ev.instruction,
                           "progress": round(ev.progress, 3), "hint": ev.hint,
                           "box": list(ev.box) if ev.box else None,
                           "size": [frame.shape[1], frame.shape[0]]}
                    if req.get("preview", True):
                        msg["preview"] = _preview(frame)
                    self.send(msg)
                    if ev.done:
                        break
        finally:
            st.camera_lock.release()
        out = session.finish()
        self.pending = {"user": user, "outcome": out, "camera": camera_id} if out.ok else None
        # Uniquement des mesures numériques (relief, vivant) : utiles au réglage, sans biométrie.
        log.info("enrôlement user=%s résultat=%s mesures=%s", user, "ok" if out.ok else out.reason,
                 {k: round(v, 3) if isinstance(v, float) else v for k, v in out.stats.items()})
        self.send({"event": "finished", "ok": out.ok, "message": out.reason,
                   "stats": {k: round(v, 3) if isinstance(v, float) else v for k, v in out.stats.items()}})

    def op_enroll_verify(self, req: dict) -> None:
        """Vérifie, AVANT de l'enregistrer, que le nouveau gabarit reconnaît bien la personne."""
        if not self.pending:
            raise Refused("error", "aucun enrôlement en attente")
        st, cfg = self.state, self.state.cfg
        out = self.pending["outcome"]
        if not st.camera_lock.acquire(blocking=False):
            raise Refused("busy", "caméra déjà utilisée par Sentinel")
        try:
            with st.open_camera() as cam:
                res = st.get_engine().authenticate(cam.frames(cfg.auth.timeout_s), out.embeddings,
                                                   out.liveness_threshold)
        finally:
            st.camera_lock.release()
        self.send({"ok": res.ok, "status": res.status.name.lower(), "message": res.status.value,
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
        st.store.save(Enrollment(user=user, embeddings=out.embeddings, camera=self.pending["camera"],
                                 liveness_threshold=out.liveness_threshold, stats=out.stats,
                                 uses={k: bool(uses.get(k, v)) for k, v in DEFAULT_USES.items()}))
        self.pending = None
        log.info("enrôlement enregistré user=%s", user)
        self.send({"ok": True, "message": "Visage enregistré"})

    def op_set_uses(self, req: dict) -> None:
        user = self._user(req)
        self._require_local()
        st = self.state
        if not self.peer.is_root and not st.check_password(user, str(req.get("password", ""))):
            raise Refused("bad_password", "mot de passe incorrect")
        enr = st.store.load(user)
        if enr is None:
            raise Refused("not_enrolled", Status.NOT_ENROLLED.value)
        enr.uses = {k: bool((req.get("uses") or {}).get(k, v)) for k, v in enr.uses.items()}
        st.store.save(enr)
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
    path = args.socket or cfg.socket_path
    with make_server(state, path) as srv:
        log.info("prêt sur %s", path)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
