"""Assistant graphique d'enregistrement du visage (GTK4 + libadwaita).

L'assistant ne touche jamais à la caméra ni aux gabarits : il dialogue avec le
service sentineld, qui fait tout le travail et lui renvoie seulement un aperçu
réduit de la caméra, pour que l'utilisateur se voie pendant l'enregistrement.

Lancement : python3 -m sentinel.gui [--socket CHEMIN] [--demo] [--snapshot DOSSIER]
"""

from __future__ import annotations

import argparse
import base64
import math
import sys
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, GdkPixbuf, GLib, Graphene, Gsk, Gtk  # noqa: E402

APP_ID = "io.github.tiburcekay.Sentinel"
STEPS = ("face", "gauche", "droite", "haut", "bas")
# L'aperçu est affiché en miroir, comme un selfie : la gauche de la personne est
# donc à gauche de l'écran.
ARROWS = {"face": "", "gauche": "←", "droite": "→", "haut": "↑", "bas": "↓"}

CSS = """
.arrow { font-size: 40px; font-weight: 800; color: @accent_color; min-height: 52px; }
.instruction { font-size: 20px; font-weight: 700; }
.hint { color: @warning_color; min-height: 22px; }
/* Pendant la capture, l'écran devient une source de lumière blanche pour le visage. */
.lit, .lit headerbar { background-color: #ffffff; color: #1d1d1f; }
.lit .hint { color: #b35c00; }
.lit .dim-label { color: alpha(#1d1d1f, 0.6); }
.promise { font-size: 15px; }
"""


# --- Accès au service ----------------------------------------------------------

class ServiceBackend:
    """Une seule connexion pour tout le parcours : l'enrôlement en attente y est lié."""

    def __init__(self, socket_path: Path):
        from .client import Client

        self.client = Client(socket_path, timeout=120)

    def hello(self) -> dict:
        return self.client.call("hello")

    def status(self) -> dict:
        return self.client.call("status")

    def enroll(self):
        return self.client.stream("enroll_start", preview=True)

    def verify(self) -> dict:
        return self.client.call("enroll_verify")

    def commit(self, password: str, uses: dict) -> dict:
        return self.client.call("enroll_commit", password=password, uses=uses)

    def remove(self, password: str) -> dict:
        return self.client.call("remove", password=password)


class DemoBackend:
    """Parcours simulé, sans service ni caméra : démonstration et captures d'écran."""

    def __init__(self, delay: float = 0.08):
        self.delay = delay
        pix = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 320, 240)
        pix.fill(0x6B7F99FF)
        ok, data = pix.save_to_bufferv("jpeg", [], [])
        self.jpeg = base64.b64encode(data).decode()

    def hello(self) -> dict:
        return {"ok": True, "user": "demo"}

    def status(self) -> dict:
        return {"ok": True, "enrolled": False, "uses": {"sudo": True, "lockscreen": True, "login": False}}

    def enroll(self):
        n = 40
        for i in range(n + 1):
            step = STEPS[min(i * len(STEPS) // n, len(STEPS) - 1)]
            time.sleep(self.delay)
            yield {"event": "progress", "step": step, "progress": i / n, "hint": "",
                   "instruction": {"face": "Regardez la caméra",
                                   "gauche": "Tournez lentement la tête vers votre gauche",
                                   "droite": "Tournez lentement la tête vers votre droite",
                                   "haut": "Levez légèrement la tête",
                                   "bas": "Baissez légèrement la tête"}[step],
                   "preview": {"jpeg": self.jpeg}}
        yield {"event": "finished", "ok": True, "message": "Visage enregistré",
               "stats": {"relief": 0.142, "liveness_threshold": -1.64}}

    def verify(self) -> dict:
        time.sleep(self.delay * 10)
        return {"ok": True, "status": "success", "elapsed": 1.24}

    def commit(self, password: str, uses: dict) -> dict:
        return {"ok": bool(password), "status": "bad_password", "message": "mot de passe incorrect"}

    def remove(self, password: str) -> dict:
        return {"ok": True, "removed": True}


def in_thread(work, done) -> None:
    """Exécute `work` hors de l'interface, puis `done(résultat ou exception)` dans l'interface."""
    def run():
        try:
            result = work()
        except Exception as exc:  # noqa: BLE001
            result = exc
        GLib.idle_add(done, result)
    threading.Thread(target=run, daemon=True).start()


# --- Composants ----------------------------------------------------------------

class CameraRing(Gtk.Widget):
    """Aperçu de la caméra dans un disque, entouré d'un anneau de 5 segments (un par pose)."""

    def __init__(self, size: int):
        super().__init__(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.size = size
        self.progress = 0.0
        self.texture: Gdk.Texture | None = None

    def do_measure(self, orientation, for_size):
        return self.size, self.size, -1, -1

    def set_progress(self, value: float) -> None:
        self.progress = value
        self.queue_draw()

    def set_frame(self, pix: GdkPixbuf.Pixbuf) -> None:
        fmt = Gdk.MemoryFormat.R8G8B8A8 if pix.get_has_alpha() else Gdk.MemoryFormat.R8G8B8
        self.texture = Gdk.MemoryTexture.new(pix.get_width(), pix.get_height(), fmt,
                                             pix.read_pixel_bytes(), pix.get_rowstride())
        self.queue_draw()

    def do_snapshot(self, snap: Gtk.Snapshot) -> None:
        w, h = self.get_width(), self.get_height()
        fg = self.get_color()
        cx, cy = w / 2, h / 2
        ring_r = min(w, h) / 2 - 7
        disc_r = ring_r - 16
        # Disque : aperçu de la caméra, recadré pour le remplir (comme « cover »).
        disc = Graphene.Rect().init(cx - disc_r, cy - disc_r, 2 * disc_r, 2 * disc_r)
        clip = Gsk.RoundedRect()
        clip.init_from_rect(disc, disc_r)
        snap.push_rounded_clip(clip)
        tint = Gdk.RGBA()
        tint.red, tint.green, tint.blue, tint.alpha = fg.red, fg.green, fg.blue, 0.08
        snap.append_color(tint, disc)
        if self.texture is not None:
            tw, th = self.texture.get_width(), self.texture.get_height()
            scale = 2 * disc_r / min(tw, th)
            snap.append_texture(self.texture, Graphene.Rect().init(
                cx - tw * scale / 2, cy - th * scale / 2, tw * scale, th * scale))
        snap.pop()
        # Anneau : un segment par pose, rempli au fil de l'enregistrement.
        cr = snap.append_cairo(Graphene.Rect().init(0, 0, w, h))
        filled = self.progress * len(STEPS)
        span, gap = 2 * math.pi / len(STEPS), math.radians(7)
        cr.set_line_width(9)
        cr.set_line_cap(1)  # bouts arrondis
        for i in range(len(STEPS)):
            a0 = -math.pi / 2 + i * span + gap / 2
            a1 = a0 + span - gap
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.15)
            cr.arc(cx, cy, ring_r, a0, a1)
            cr.stroke()
            part = min(max(filled - i, 0.0), 1.0)
            if part > 0:
                cr.set_source_rgba(0.21, 0.52, 0.89, 1.0)
                cr.arc(cx, cy, ring_r, a0, a0 + (a1 - a0) * part)
                cr.stroke()


def check_row(text: str) -> Gtk.Box:
    box = Gtk.Box(spacing=10, halign=Gtk.Align.START)
    icon = Gtk.Image.new_from_icon_name("object-select-symbolic")
    icon.add_css_class("success")
    label = Gtk.Label(label=text, xalign=0)
    label.add_css_class("promise")
    box.append(icon)
    box.append(label)
    return box


# --- Fenêtre ---------------------------------------------------------------------

class Wizard(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, backend_factory, snapshot_dir: Path | None = None):
        super().__init__(application=app, title="Sentinel", default_width=520, default_height=640)
        self.backend_factory = backend_factory
        self.backend = None
        self.snapshot_dir = snapshot_dir
        self.cancelled = False

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT, vexpand=True)
        self.view = Adw.ToolbarView()
        self.view.add_top_bar(Adw.HeaderBar())
        self.view.set_content(self.stack)
        self.set_content(self.view)
        for name, builder in (("welcome", self._welcome), ("capture", self._capture),
                              ("verify", self._verify), ("confirm", self._confirm),
                              ("done", self._done), ("error", self._error)):
            self.stack.add_named(builder(), name)
        self._connect()

    # -- Pages --------------------------------------------------------------------

    def _page(self, child: Gtk.Widget) -> Gtk.Widget:
        clamp = Adw.Clamp(maximum_size=440, child=child, margin_top=12, margin_bottom=24,
                          margin_start=18, margin_end=18)
        return Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER)

    def _status_page(self, icon: str, title: str, description: str, child: Gtk.Widget) -> Adw.StatusPage:
        """Page standard libadwaita : défile et se centre d'elle-même, sans rien couper."""
        clamp = Adw.Clamp(maximum_size=420, child=child)
        return Adw.StatusPage(icon_name=icon, title=title, description=description, child=clamp)

    def _welcome(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        for text in ("Tout reste sur cet ordinateur, sans Internet",
                     "Aucune photo de vous n'est enregistrée",
                     "Votre mot de passe reste toujours utilisable"):
            box.append(check_row(text))
        self.welcome_state = Gtk.Label(wrap=True, margin_top=10, justify=Gtk.Justification.CENTER)
        self.welcome_state.add_css_class("dim-label")
        box.append(self.welcome_state)
        self.start_button = Gtk.Button(label="Commencer", halign=Gtk.Align.CENTER, margin_top=6,
                                       sensitive=False)
        self.start_button.add_css_class("suggested-action")
        self.start_button.add_css_class("pill")
        self.start_button.connect("clicked", lambda *_: self._start_capture())
        box.append(self.start_button)
        self.remove_button = Gtk.Button(label="Supprimer mon visage", halign=Gtk.Align.CENTER, visible=False)
        self.remove_button.add_css_class("destructive-action")
        self.remove_button.add_css_class("flat")
        self.remove_button.connect("clicked", lambda *_: self._ask_remove())
        box.append(self.remove_button)
        page = self._status_page("camera-web-symbolic", "Déverrouillez votre PC avec votre visage",
                                 "Sentinel reconnaît votre visage et vérifie qu'il s'agit bien de vous, "
                                 "en vrai, et non d'une photo ou d'une vidéo.", box)
        page.add_css_class("compact")
        return page

    def _capture(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, valign=Gtk.Align.CENTER)
        self.ring = CameraRing(290)
        self.ring.set_margin_top(18)
        self.ring.set_halign(Gtk.Align.CENTER)
        box.append(self.ring)
        self.arrow = Gtk.Label()
        self.arrow.add_css_class("arrow")
        box.append(self.arrow)
        self.instruction = Gtk.Label(label="Préparation de la caméra…", wrap=True, justify=Gtk.Justification.CENTER)
        self.instruction.add_css_class("instruction")
        box.append(self.instruction)
        self.hint = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self.hint.add_css_class("hint")
        box.append(self.hint)
        cancel = Gtk.Button(label="Annuler", halign=Gtk.Align.CENTER, margin_top=8)
        cancel.add_css_class("flat")
        cancel.connect("clicked", lambda *_: self._cancel())
        box.append(cancel)
        return self._page(box)

    def _verify(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.verify_spinner = Adw.Spinner(halign=Gtk.Align.CENTER, width_request=32, height_request=32)
        box.append(self.verify_spinner)
        buttons = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        self.verify_retry = Gtk.Button(label="Réessayer", visible=False)
        self.verify_retry.add_css_class("pill")
        self.verify_retry.connect("clicked", lambda *_: self._start_verify())
        self.verify_next = Gtk.Button(label="Continuer", visible=False)
        self.verify_next.add_css_class("suggested-action")
        self.verify_next.add_css_class("pill")
        self.verify_next.connect("clicked", lambda *_: self._show("confirm"))
        buttons.append(self.verify_retry)
        buttons.append(self.verify_next)
        box.append(buttons)
        self.verify_status = self._status_page("face-smile-symbolic", "Vérification",
                                               "Regardez la caméra…", box)
        return self.verify_status

    def _confirm(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        uses = Adw.PreferencesGroup(title="Utiliser mon visage pour")
        self.use_sudo = Adw.SwitchRow(title="Commandes administrateur",
                                      subtitle="sudo, dans le terminal", active=True)
        self.use_lock = Adw.SwitchRow(title="Écran de verrouillage",
                                      subtitle="Déverrouiller la session déjà ouverte", active=True)
        self.use_login = Adw.SwitchRow(title="Connexion au démarrage", sensitive=False,
                                       subtitle="Au démarrage, votre mot de passe reste demandé : "
                                                "il déverrouille aussi votre trousseau de mots de passe.")
        for row in (self.use_sudo, self.use_lock, self.use_login):
            uses.add(row)
        box.append(uses)
        confirm = Adw.PreferencesGroup(title="Confirmation",
                                       description="Saisissez votre mot de passe pour autoriser "
                                                   "l'enregistrement de votre visage.")
        self.password = Adw.PasswordEntryRow(title="Mot de passe")
        self.password.connect("entry-activated", lambda *_: self._commit())
        confirm.add(self.password)
        box.append(confirm)
        self.confirm_error = Gtk.Label(wrap=True)
        self.confirm_error.add_css_class("error")
        box.append(self.confirm_error)
        self.finish_button = Gtk.Button(label="Terminer", halign=Gtk.Align.CENTER)
        self.finish_button.add_css_class("suggested-action")
        self.finish_button.add_css_class("pill")
        self.finish_button.connect("clicked", lambda *_: self._commit())
        box.append(self.finish_button)
        return self._page(box)

    def _done(self) -> Gtk.Widget:
        close = Gtk.Button(label="Fermer", halign=Gtk.Align.CENTER)
        close.add_css_class("pill")
        close.connect("clicked", lambda *_: self.close())
        self.done_status = self._status_page(
            "object-select-symbolic", "Visage enregistré",
            "Seules des empreintes chiffrées sont conservées, jamais d'image. "
            "Vous pouvez relancer cet assistant pour modifier vos choix.", close)
        return self.done_status

    def _error(self) -> Gtk.Widget:
        retry = Gtk.Button(label="Recommencer", halign=Gtk.Align.CENTER)
        retry.add_css_class("pill")
        retry.connect("clicked", lambda *_: self._connect())
        self.error_status = self._status_page("dialog-warning-symbolic", "Un problème est survenu", "", retry)
        return self.error_status

    # -- Déroulement ------------------------------------------------------------

    def _show(self, name: str) -> None:
        self.stack.set_visible_child_name(name)
        # Capture et vérification : plein écran blanc, pour éclairer le visage.
        lit = name in ("capture", "verify")
        if lit:
            self.view.add_css_class("lit")
            self.fullscreen()
        else:
            self.view.remove_css_class("lit")
            self.unfullscreen()
        if self.snapshot_dir:
            # Capture de la page en cours, au moment où elle est la plus parlante.
            GLib.timeout_add(1800 if name == "capture" else 600, self._snapshot, name)

    def _fail(self, message: str) -> None:
        self.error_status.set_description(message)
        self._show("error")

    def _connect(self) -> None:
        self._show("welcome")
        self.welcome_state.set_label("Connexion au service Sentinel…")

        def work():
            if self.backend is not None and hasattr(self.backend, "client"):
                self.backend.client.close()
            self.backend = self.backend_factory()
            return self.backend.hello(), self.backend.status()

        def done(result):
            if isinstance(result, Exception):
                self.welcome_state.set_label(f"Service Sentinel indisponible : {result}")
                return
            hello, status = result
            if not status.get("ok"):
                self.welcome_state.set_label(status.get("message", "Accès refusé"))
                return
            enrolled = status.get("enrolled")
            self.welcome_state.set_label(
                f"Compte : {hello.get('user')}. "
                + ("Un visage est déjà enregistré : vous pouvez le remplacer." if enrolled
                   else "Durée : environ 30 secondes."))
            self.start_button.set_label("Remplacer mon visage" if enrolled else "Commencer")
            self.start_button.set_sensitive(True)
            self.remove_button.set_visible(bool(enrolled))
            uses = status.get("uses", {})
            self.use_sudo.set_active(uses.get("sudo", True))
            self.use_lock.set_active(uses.get("lockscreen", True))
        in_thread(work, done)

    def _start_capture(self) -> None:
        self.cancelled = False
        self.ring.set_progress(0)
        self.arrow.set_label("")
        self.hint.set_label("")
        self.instruction.set_label("Préparation de la caméra…")
        self._show("capture")

        def work():
            for msg in self.backend.enroll():
                if self.cancelled:
                    return None
                GLib.idle_add(self._on_progress, msg)
                if msg.get("event") != "progress":
                    return msg
            return None

        def done(result):
            if self.cancelled:
                return
            if isinstance(result, Exception):
                self._fail(f"La connexion au service a été interrompue ({result}).")
            elif result is None or not result.get("ok"):
                msg = (result or {}).get("message", "Enregistrement interrompu")
                self._fail(f"{msg}. Rien n'a été enregistré.")
            else:
                self._start_verify()
        in_thread(work, done)

    def _on_progress(self, msg: dict) -> bool:
        if msg.get("event") != "progress":
            return False
        self.ring.set_progress(msg.get("progress", 0.0))
        self.arrow.set_label(ARROWS.get(msg.get("step", ""), ""))
        self.instruction.set_label(msg.get("instruction", ""))
        self.hint.set_label(msg.get("hint", ""))
        jpeg = (msg.get("preview") or {}).get("jpeg")
        if jpeg:
            loader = GdkPixbuf.PixbufLoader.new_with_type("jpeg")
            loader.write(base64.b64decode(jpeg))
            loader.close()
            self.ring.set_frame(loader.get_pixbuf().flip(True))  # miroir, comme un selfie
        return False

    def _start_verify(self) -> None:
        self.verify_status.set_icon_name("face-smile-symbolic")
        self.verify_status.set_title("Vérification")
        self.verify_status.set_description("Regardez la caméra…")
        self.verify_spinner.set_visible(True)
        self.verify_retry.set_visible(False)
        self.verify_next.set_visible(False)
        self._show("verify")

        def done(result):
            self.verify_spinner.set_visible(False)
            if isinstance(result, Exception):
                self._fail(str(result))
            elif result.get("ok"):
                self.verify_status.set_icon_name("object-select-symbolic")
                self.verify_status.set_title("Reconnu")
                self.verify_status.set_description(f"Votre visage a été reconnu en {result.get('elapsed', 0):.1f} s.".replace(".", ",", 1))
                self.verify_next.set_visible(True)
                if self.snapshot_dir:
                    self._show("verify")
            else:
                self.verify_status.set_icon_name("dialog-warning-symbolic")
                self.verify_status.set_title("Non reconnu")
                self.verify_status.set_description(result.get("message", ""))
                self.verify_retry.set_visible(True)
        in_thread(self.backend.verify, done)

    def _commit(self) -> None:
        password = self.password.get_text()
        uses = {"sudo": self.use_sudo.get_active(), "lockscreen": self.use_lock.get_active(), "login": False}
        self.finish_button.set_sensitive(False)
        self.confirm_error.set_label("")

        def done(result):
            self.finish_button.set_sensitive(True)
            self.password.set_text("")
            if isinstance(result, Exception):
                self._fail(str(result))
            elif result.get("ok"):
                self._show("done")
            else:
                self.confirm_error.set_label(result.get("message", "Échec"))
        in_thread(lambda: self.backend.commit(password, uses), done)

    def _cancel(self) -> None:
        self.cancelled = True
        if hasattr(self.backend, "client"):
            self.backend.client.close()  # le service arrête l'enrôlement et libère la caméra
            self.backend = None
        self._connect()

    def _ask_remove(self) -> None:
        dialog = Adw.AlertDialog(heading="Supprimer votre visage ?",
                                 body="Sentinel ne pourra plus vous reconnaître. "
                                      "Confirmez avec votre mot de passe.")
        entry = Gtk.PasswordEntry(show_peek_icon=True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Annuler")
        dialog.add_response("remove", "Supprimer")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(_d, response):
            if response != "remove":
                return

            def done(result):
                if isinstance(result, Exception) or not result.get("ok"):
                    msg = str(result) if isinstance(result, Exception) else result.get("message", "")
                    self.welcome_state.set_label(f"Suppression refusée : {msg}")
                else:
                    self._connect()
            in_thread(lambda: self.backend.remove(entry.get_text()), done)
        dialog.connect("response", on_response)
        dialog.present(self)

    # -- Captures d'écran (documentation, tests) ------------------------------------

    def _snapshot(self, name: str) -> bool:
        w, h = self.get_width(), self.get_height()
        paintable = Gtk.WidgetPaintable.new(self)
        snap = Gtk.Snapshot()
        paintable.snapshot(snap, w, h)
        node = snap.to_node()
        if node is not None:
            texture = self.get_renderer().render_texture(node, None)
            path = self.snapshot_dir / f"{len(list(self.snapshot_dir.glob('*.png'))):02d}-{name}.png"
            texture.save_to_png(str(path))
        return False


class App(Adw.Application):
    def __init__(self, backend_factory, snapshot_dir: Path | None, demo: bool):
        super().__init__(application_id=APP_ID)
        self.backend_factory, self.snapshot_dir, self.demo = backend_factory, snapshot_dir, demo

    def do_activate(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        win = Wizard(self, self.backend_factory, self.snapshot_dir)
        win.present()
        if self.demo and self.snapshot_dir:
            self._autoplay(win)

    def _autoplay(self, win: Wizard) -> None:
        """Démonstration automatique : parcourt toutes les pages, puis quitte."""
        steps = [
            (1500, lambda: win._start_capture()),       # l'accueil est capturé avant
            (5500, lambda: None),                       # enregistrement puis vérification
            (900, lambda: win._show("confirm")),
            (900, lambda: (win.password.set_text("x"), win._commit())),
            (1500, lambda: self.quit()),
        ]
        t = 0
        for delay, action in steps:
            t += delay
            GLib.timeout_add(t, lambda a=action: (a(), False)[1])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sentinel-setup")
    ap.add_argument("--socket", type=Path, default=Path("/run/sentinel/sentinel.sock"))
    ap.add_argument("--demo", action="store_true", help="parcours simulé, sans service ni caméra")
    ap.add_argument("--snapshot", type=Path, help="enregistre une capture de chaque page (avec --demo)")
    args = ap.parse_args(argv)
    if args.snapshot:
        args.snapshot.mkdir(parents=True, exist_ok=True)
    factory = DemoBackend if args.demo else (lambda: ServiceBackend(args.socket))
    return App(factory, args.snapshot, args.demo).run([sys.argv[0]])


if __name__ == "__main__":
    raise SystemExit(main())
