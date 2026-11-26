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
# Les captures d'écran passent par les nœuds de rendu GSK, que les anciennes versions
# de PyGObject (Ubuntu 22.04, Debian 12) ne savent pas manipuler : elles y plantent.
SNAPSHOTS_SUPPORTED = (Gtk.get_major_version(), Gtk.get_minor_version()) >= (4, 10)
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

    def set_uses(self, password: str, uses: dict) -> dict:
        return self.client.call("set_uses", password=password, uses=uses)


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

    def set_uses(self, password: str, uses: dict) -> dict:
        return {"ok": bool(password), "uses": uses, "message": "mot de passe incorrect"}


EXTENSION_UUID = "sentinel@tiburce-kay.github.io"


def enable_lockscreen_button(on: bool) -> None:
    """Active (ou retire) l'extension du bouton clé pour l'utilisateur courant. Passer
    par les réglages GNOME fonctionne même si GNOME Shell ne l'a pas encore chargée :
    elle le sera à la prochaine ouverture de session."""
    from gi.repository import Gio
    source = Gio.SettingsSchemaSource.get_default()
    if source is None or source.lookup("org.gnome.shell", True) is None:
        return  # pas GNOME Shell : rien à faire
    settings = Gio.Settings.new("org.gnome.shell")
    enabled = list(settings.get_strv("enabled-extensions"))
    if on and EXTENSION_UUID not in enabled:
        settings.set_strv("enabled-extensions", enabled + [EXTENSION_UUID])
    elif not on and EXTENSION_UUID in enabled:
        settings.set_strv("enabled-extensions", [u for u in enabled if u != EXTENSION_UUID])


def in_thread(work, done) -> None:
    """Exécute `work` hors de l'interface, puis `done(résultat ou exception)` dans l'interface."""
    def run():
        try:
            result = work()
        except Exception as exc:  # noqa: BLE001
            result = exc
        GLib.idle_add(done, result)
    threading.Thread(target=run, daemon=True).start()


# --- Compatibilité : libadwaita 1.1+ et GTK 4.6+ ---------------------------------
# Ubuntu 22.04 / Mint 21 : libadwaita 1.1, GTK 4.6 ; Debian 12 : 1.2 et 4.8. Les
# composants récents sont utilisés quand ils existent, sinon un équivalent ancien.

def fg_color(widget: Gtk.Widget) -> Gdk.RGBA:
    if hasattr(widget, "get_color"):  # GTK 4.10+
        return widget.get_color()
    return widget.get_style_context().get_color()


def load_css(provider: Gtk.CssProvider, css: str) -> None:
    if hasattr(provider, "load_from_string"):  # GTK 4.12+
        provider.load_from_string(css)
    else:
        provider.load_from_data(css.encode())


def window_layout(window: Adw.ApplicationWindow, content: Gtk.Widget) -> Gtk.Widget:
    """Barre de titre + contenu. Renvoie le conteneur (pour l'éclairage par l'écran)."""
    if hasattr(Adw, "ToolbarView"):  # libadwaita 1.4+
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(content)
    else:
        view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        view.append(Adw.HeaderBar())
        view.append(content)
    window.set_content(view)
    return view


class SwitchRow:
    """Ligne avec interrupteur : Adw.SwitchRow (1.4+) ou Adw.ActionRow + Gtk.Switch."""

    def __init__(self, title: str, subtitle: str, active: bool = False, sensitive: bool = True):
        if hasattr(Adw, "SwitchRow"):
            self.row = Adw.SwitchRow(title=title, subtitle=subtitle, active=active, sensitive=sensitive)
            self._switch = None
        else:
            self._switch = Gtk.Switch(active=active, valign=Gtk.Align.CENTER)
            self.row = Adw.ActionRow(title=title, subtitle=subtitle, sensitive=sensitive,
                                     activatable_widget=self._switch)
            self.row.add_suffix(self._switch)

    def get_active(self) -> bool:
        return (self._switch or self.row).get_active()

    def set_active(self, value: bool) -> None:
        (self._switch or self.row).set_active(value)


class PasswordRow:
    """Saisie de mot de passe : Adw.PasswordEntryRow (1.2+) ou Gtk.PasswordEntry dans une ligne."""

    def __init__(self, title: str, on_activate):
        if hasattr(Adw, "PasswordEntryRow"):
            self.row = Adw.PasswordEntryRow(title=title)
            self.row.connect("entry-activated", lambda *_: on_activate())
            self._entry = self.row
        else:
            self._entry = Gtk.PasswordEntry(show_peek_icon=True, hexpand=True, valign=Gtk.Align.CENTER)
            self._entry.connect("activate", lambda *_: on_activate())
            self.row = Adw.ActionRow(title=title)
            self.row.add_suffix(self._entry)

    def get_text(self) -> str:
        return self._entry.get_text()

    def set_text(self, text: str) -> None:
        self._entry.set_text(text)


def confirm_dialog(parent: Gtk.Window, heading: str, body: str, extra: Gtk.Widget,
                   action: str, on_confirm) -> None:
    """Dialogue de confirmation destructive : Adw.AlertDialog (1.5+), Adw.MessageDialog
    (1.2+) ou Gtk.MessageDialog."""
    def respond(_d, response):
        if response in ("ok", Gtk.ResponseType.OK):
            on_confirm()

    if hasattr(Adw, "AlertDialog") or hasattr(Adw, "MessageDialog"):
        modern = hasattr(Adw, "AlertDialog")
        dialog = (Adw.AlertDialog(heading=heading, body=body) if modern
                  else Adw.MessageDialog(transient_for=parent, heading=heading, body=body))
        dialog.set_extra_child(extra)
        dialog.add_response("cancel", "Annuler")
        dialog.add_response("ok", action)
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", respond)
        dialog.present(parent) if modern else dialog.present()
        return
    dialog = Gtk.MessageDialog(transient_for=parent, modal=True, text=heading, secondary_text=body,
                               message_type=Gtk.MessageType.WARNING)
    dialog.add_button("Annuler", Gtk.ResponseType.CANCEL)
    dialog.add_button(action, Gtk.ResponseType.OK)
    dialog.get_message_area().append(extra)

    def done(d, response):
        d.destroy()
        respond(d, response)
    dialog.connect("response", done)
    dialog.present()


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
        fg = fg_color(self)
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
        self.settings_mode = False

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT, vexpand=True)
        self.view = window_layout(self, self.stack)
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
        self.settings_button = Gtk.Button(label="Modifier mes réglages", halign=Gtk.Align.CENTER, visible=False)
        self.settings_button.add_css_class("flat")
        self.settings_button.connect("clicked", lambda *_: self._open_settings())
        box.append(self.settings_button)
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
        # Adw.Spinner date de libadwaita 1.6 (Ubuntu 24.04 a la 1.5) : repli sur Gtk.Spinner.
        if hasattr(Adw, "Spinner"):
            self.verify_spinner = Adw.Spinner(halign=Gtk.Align.CENTER, width_request=32, height_request=32)
        else:
            self.verify_spinner = Gtk.Spinner(halign=Gtk.Align.CENTER, spinning=True,
                                              width_request=32, height_request=32)
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
        self.use_sudo = SwitchRow("Commandes administrateur", "sudo, dans le terminal", active=True)
        self.use_lock = SwitchRow("Écran de verrouillage", "Déverrouiller la session déjà ouverte", active=True)
        self.use_login = SwitchRow("Connexion au démarrage",
                                   "Le PC s'ouvre avec votre visage dès le démarrage. Votre mot de passe "
                                   "est alors conservé chiffré sur ce PC, uniquement pour ouvrir votre "
                                   "trousseau de mots de passe. Pratique, mais un PC volé s'ouvrirait "
                                   "devant votre visage.", active=False)
        for row in (self.use_sudo, self.use_lock, self.use_login):
            uses.add(row.row)
        box.append(uses)
        self.confirm_group = confirm = Adw.PreferencesGroup(
            title="Confirmation",
            description="Saisissez votre mot de passe pour autoriser l'enregistrement de votre visage.")
        self.password = PasswordRow("Mot de passe", self._commit)
        confirm.add(self.password.row)
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
            print(f"page: {name}", flush=True)  # trace vérifiée par les tests d'installation
            # Capture de la page en cours, au moment où elle est la plus parlante.
            if SNAPSHOTS_SUPPORTED:
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
            self.settings_button.set_visible(bool(enrolled))
            uses = status.get("uses", {})
            self.use_sudo.set_active(uses.get("sudo", True))
            self.use_lock.set_active(uses.get("lockscreen", True))
            self.use_login.set_active(uses.get("login", False))
        in_thread(work, done)

    def _start_capture(self) -> None:
        self.settings_mode = False
        self.confirm_group.set_description(
            "Saisissez votre mot de passe pour autoriser l'enregistrement de votre visage.")
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
        uses = {"sudo": self.use_sudo.get_active(), "lockscreen": self.use_lock.get_active(),
                "login": self.use_login.get_active()}
        self.finish_button.set_sensitive(False)
        self.confirm_error.set_label("")

        def done(result):
            self.finish_button.set_sensitive(True)
            self.password.set_text("")
            if isinstance(result, Exception):
                self._fail(str(result))
            elif result.get("ok"):
                if isinstance(self.backend, ServiceBackend):
                    try:
                        enable_lockscreen_button(uses["lockscreen"])
                    except Exception:  # noqa: BLE001 — confort, jamais bloquant
                        pass
                self._show("done")
            else:
                self.confirm_error.set_label(result.get("message", "Échec"))
        action = self.backend.set_uses if self.settings_mode else self.backend.commit
        in_thread(lambda: action(password, uses), done)

    def _open_settings(self) -> None:
        """Modifier les usages sans refaire l'enregistrement du visage."""
        self.settings_mode = True
        self.confirm_group.set_description("Saisissez votre mot de passe pour enregistrer ces réglages.")
        self._show("confirm")

    def _cancel(self) -> None:
        self.cancelled = True
        if hasattr(self.backend, "client"):
            self.backend.client.close()  # le service arrête l'enrôlement et libère la caméra
            self.backend = None
        self._connect()

    def _ask_remove(self) -> None:
        entry = Gtk.PasswordEntry(show_peek_icon=True)

        def confirmed():
            def done(result):
                if isinstance(result, Exception) or not result.get("ok"):
                    msg = str(result) if isinstance(result, Exception) else result.get("message", "")
                    self.welcome_state.set_label(f"Suppression refusée : {msg}")
                else:
                    self._connect()
            in_thread(lambda: self.backend.remove(entry.get_text()), done)

        confirm_dialog(self, "Supprimer votre visage ?",
                       "Sentinel ne pourra plus vous reconnaître. Confirmez avec votre mot de passe.",
                       entry, "Supprimer", confirmed)

    # -- Captures d'écran (documentation, tests) ------------------------------------

    def _snapshot(self, name: str) -> bool:
        w, h = self.get_width(), self.get_height()
        if self.get_renderer() is None or w == 0 or h == 0:
            GLib.timeout_add(100, self._snapshot, name)  # pas encore affichée : réessai rapide
            return False
        if self.stack.get_visible_child_name() != name:
            return False  # la page a déjà changé
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
        load_css(provider, CSS)
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
