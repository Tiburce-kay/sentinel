// Sentinel — intégration à l'écran de connexion et à l'écran de verrouillage.
//
// 1. Pendant que Sentinel vérifie le visage, le champ du mot de passe est masqué :
//    il n'apparaît que si le visage échoue, ou si l'utilisateur le demande.
// 2. Un bouton clé « Utiliser le mot de passe » est affiché sous le message de
//    Sentinel ; un clic demande au service d'arrêter la vérification.
//
// Le champ réapparaît par le mécanisme normal de GNOME : dès que PAM demande le mot
// de passe, GNOME appelle setQuestion(), qui l'affiche. Par sécurité, il réapparaît
// aussi après 15 s sans question. Toute erreur de cette extension est interceptée :
// elle ne peut pas empêcher de se connecter.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import {Extension, InjectionManager} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as AuthPrompt from 'resource:///org/gnome/shell/gdm/authPrompt.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const SOCKET_PATH = '/run/sentinel/sentinel.sock';
const CAMERA_MESSAGE = 'Sentinel : regardez la caméra';
const SAFETY_DELAY_S = 15;

function sentinelInstalled() {
    return GLib.file_test(SOCKET_PATH, GLib.FileTest.EXISTS);
}

function requestSkip(userName) {
    // Le service identifie l'appelant par le noyau (SO_PEERCRED). À l'écran de
    // connexion, l'appelant est GDM : on précise l'utilisateur concerné. Demander à
    // passer au mot de passe ne donne aucun accès : c'est toujours sans danger.
    const request = {op: 'skip'};
    if (userName)
        request.user = userName;
    const client = new Gio.SocketClient();
    client.connect_async(Gio.UnixSocketAddress.new(SOCKET_PATH), null, (c, res) => {
        try {
            const conn = c.connect_finish(res);
            const bytes = new GLib.Bytes(new TextEncoder().encode(`${JSON.stringify(request)}\n`));
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

function cancelSafety(prompt) {
    if (prompt._sentinelSafety) {
        GLib.source_remove(prompt._sentinelSafety);
        prompt._sentinelSafety = 0;
    }
}

function showEntry(prompt) {
    cancelSafety(prompt);
    if (prompt._sentinelDestroyed)
        return;
    prompt._sentinelKeyButton?.hide();
    prompt._entry?.show();
}

function watchDestroy(prompt) {
    // L'invite disparaît (session ouverte, écran déverrouillé) : plus rien à faire,
    // et surtout ne plus toucher à ses éléments, déjà détruits.
    if (prompt._sentinelWatched)
        return;
    prompt._sentinelWatched = true;
    prompt.connect('destroy', () => {
        cancelSafety(prompt);
        prompt._sentinelDestroyed = true;
        prompt._sentinelKeyButton = null;
    });
}

function keyButton(prompt) {
    if (prompt._sentinelKeyButton)
        return prompt._sentinelKeyButton;
    const content = new St.BoxLayout({style: 'spacing: 8px;'});
    content.add_child(new St.Icon({icon_name: 'dialog-password-symbolic', icon_size: 16}));
    content.add_child(new St.Label({text: 'Utiliser le mot de passe', y_align: Clutter.ActorAlign.CENTER}));
    const button = new St.Button({
        style_class: 'button',
        can_focus: true,
        accessible_name: 'Utiliser le mot de passe',
        child: content,
        x_align: Clutter.ActorAlign.CENTER,
    });
    button.connect('clicked', () => {
        button.hide();
        requestSkip(prompt._sentinelUser);
    });
    prompt._inputWell.add_child(button);
    prompt._sentinelKeyButton = button;
    return button;
}

export default class SentinelExtension extends Extension {
    enable() {
        // Une ligne au journal : savoir si l'extension est active, notamment à l'écran
        // de connexion, où rien d'autre n'est visible.
        console.log(`Sentinel : extension active (mode ${Main.sessionMode.currentMode})`);
        this._injections = new InjectionManager();
        const proto = AuthPrompt.AuthPrompt.prototype;

        // Début d'une vérification : on retient l'utilisateur et on masque le champ.
        this._injections.overrideMethod(proto, 'begin', original => function (params, ...rest) {
            const result = original.call(this, params, ...rest);
            try {
                this._sentinelUser = params?.userName ?? null;
                watchDestroy(this);
                if (sentinelInstalled()) {
                    this._entry.hide();
                    cancelSafety(this);
                    this._sentinelSafety = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, SAFETY_DELAY_S, () => {
                        this._sentinelSafety = 0;
                        if (this._sentinelDestroyed)
                            return GLib.SOURCE_REMOVE;
                        try {
                            showEntry(this);
                        } catch (e) {
                            logError(e, 'Sentinel');
                        }
                        return GLib.SOURCE_REMOVE;
                    });
                }
            } catch (e) {
                logError(e, 'Sentinel');
                this._entry?.show();
            }
            return result;
        });

        // Le mot de passe est demandé : on rend la main à GNOME (champ visible).
        this._injections.overrideMethod(proto, 'setQuestion', original => function (...args) {
            try {
                showEntry(this);
            } catch (e) {
                logError(e, 'Sentinel');
            }
            return original.call(this, ...args);
        });

        // Message de Sentinel : bouton clé visible tant que la caméra regarde.
        this._injections.overrideMethod(proto, 'setMessage', original => function (message, type, ...rest) {
            const result = original.call(this, message, type, ...rest);
            try {
                const waitingForFace = typeof message === 'string' && message.startsWith(CAMERA_MESSAGE);
                if (waitingForFace) {
                    this._entry.hide();
                    keyButton(this).show();
                } else {
                    this._sentinelKeyButton?.hide();
                }
            } catch (e) {
                logError(e, 'Sentinel');
                this._entry?.show();
            }
            return result;
        });

        // Vérification terminée (succès) : plus besoin du filet de sécurité.
        this._injections.overrideMethod(proto, 'finish', original => function (...args) {
            try {
                cancelSafety(this);
            } catch (e) {
                logError(e, 'Sentinel');
            }
            return original.call(this, ...args);
        });

        // Annulation / remise à zéro : état normal de GNOME.
        this._injections.overrideMethod(proto, 'reset', original => function (...args) {
            try {
                showEntry(this);
            } catch (e) {
                logError(e, 'Sentinel');
            }
            return original.call(this, ...args);
        });
    }

    disable() {
        this._injections?.clear();
        this._injections = null;
    }
}
