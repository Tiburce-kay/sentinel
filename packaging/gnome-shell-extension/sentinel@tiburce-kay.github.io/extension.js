// Sentinel — bouton « passer au mot de passe » sur l'écran de verrouillage.
//
// Pendant que Sentinel vérifie le visage, GNOME affiche le message envoyé par le
// module PAM (« Sentinel : regardez la caméra… »). Cette extension ajoute juste en
// dessous un bouton clé : un clic demande au service sentineld d'arrêter la
// vérification, et GNOME affiche aussitôt le champ du mot de passe.
//
// L'écran de connexion (GDM) n'accepte pas d'extension : ce bouton n'y apparaît pas.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import {Extension, InjectionManager} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as AuthPrompt from 'resource:///org/gnome/shell/gdm/authPrompt.js';

const SOCKET_PATH = '/run/sentinel/sentinel.sock';
const CAMERA_MESSAGE = 'Sentinel : regardez la caméra';

function requestSkip() {
    // Le service identifie l'appelant par le noyau (SO_PEERCRED) : aucune donnée à
    // fournir, la demande vaut pour l'utilisateur de la session.
    const client = new Gio.SocketClient();
    client.connect_async(Gio.UnixSocketAddress.new(SOCKET_PATH), null, (c, res) => {
        try {
            const conn = c.connect_finish(res);
            const bytes = new GLib.Bytes(new TextEncoder().encode('{"op": "skip"}\n'));
            conn.get_output_stream().write_bytes_async(bytes, GLib.PRIORITY_DEFAULT, null, (s, r) => {
                try {
                    s.write_bytes_finish(r);
                } catch (e) {
                    logError(e, 'Sentinel : envoi de la demande impossible');
                }
                conn.close_async(GLib.PRIORITY_DEFAULT, null, null);
            });
        } catch (e) {
            logError(e, 'Sentinel : service injoignable');
        }
    });
}

export default class SentinelExtension extends Extension {
    enable() {
        this._injections = new InjectionManager();
        this._injections.overrideMethod(AuthPrompt.AuthPrompt.prototype, 'setMessage', original => {
            return function (message, type, ...rest) {
                original.call(this, message, type, ...rest);
                const waitingForFace = typeof message === 'string' && message.startsWith(CAMERA_MESSAGE);
                if (waitingForFace && !this._sentinelKeyButton) {
                    const content = new St.BoxLayout({style: 'spacing: 8px;'});
                    content.add_child(new St.Icon({icon_name: 'dialog-password-symbolic', icon_size: 16}));
                    content.add_child(new St.Label({
                        text: 'Utiliser le mot de passe',
                        y_align: Clutter.ActorAlign.CENTER,
                    }));
                    this._sentinelKeyButton = new St.Button({
                        style_class: 'button',
                        can_focus: true,
                        accessible_name: 'Utiliser le mot de passe',
                        child: content,
                        x_align: Clutter.ActorAlign.CENTER,
                    });
                    this._sentinelKeyButton.connect('clicked', () => {
                        this._sentinelKeyButton.hide();
                        requestSkip();
                    });
                    this._inputWell.add_child(this._sentinelKeyButton);
                }
                this._sentinelKeyButton?.[waitingForFace ? 'show' : 'hide']();
            };
        });
    }

    disable() {
        // Appelé aussi au verrouillage/déverrouillage selon les modes de session :
        // on retire l'injection ; les boutons déjà créés disparaissent avec leur invite.
        this._injections?.clear();
        this._injections = null;
    }
}
