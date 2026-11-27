"""Joue le rôle de l'écran de verrouillage de Plasma 5.24 face au vrai kcheckpass.

Reproduit le protocole binaire de kscreenlocker 5.24.4 (greeter/authenticator.cpp) :
lance « kcheckpass -m classic -S <fd> », attend « prêt », envoie SIGUSR1, répond une
seule fois au mot de passe demandé (comme l'écran, qui l'efface ensuite), affiche les
messages PAM, puis rend le résultat. Utilisé par scripts/test-kcheckpass-docker.sh.

    python3 kcheckpass_driver.py CHEMIN_KCHECKPASS MOT_DE_PASSE [MOT_DE_PASSE…]

Chaque mot de passe est une tentative (comme un appui sur Entrée). Code de sortie :
0 si la dernière tentative est acceptée, 1 sinon.
"""
import os
import signal
import socket
import struct
import sys
import time

(GET_BINARY, GET_NORMAL, GET_HIDDEN, PUT_INFO, PUT_ERROR, SUCCEEDED, FAILED, AUTH_ERROR,
 AUTH_ABORT, READY) = range(10)
IS_PASSWORD = 2


def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError("kcheckpass a fermé la connexion")
        buf += chunk
    return buf


def recv_int(sock):
    return struct.unpack("i", recv_exact(sock, 4))[0]


def recv_arr(sock):
    n = recv_int(sock)
    return recv_exact(sock, n) if n else b""


def send_str(sock, s):
    data = s.encode() + b"\0"
    sock.sendall(struct.pack("I", len(data)) + data)


def main():
    kcp, passwords = sys.argv[1], sys.argv[2:]
    parent, child = socket.socketpair()
    os.set_inheritable(child.fileno(), True)
    pid = os.fork()
    if pid == 0:
        parent.close()
        os.execv(kcp, ["kcheckpass", "-m", "classic", "-S", str(child.fileno())])
    child.close()
    result, ready = None, False
    for attempt, password in enumerate(passwords, 1):
        pending, started, t0 = password, False, time.monotonic()
        if ready:                            # kcheckpass attend déjà : Entrée
            os.kill(pid, signal.SIGUSR1)
            started, ready = True, False
        while True:
            what = recv_int(parent)
            if what == READY:
                if not started:
                    os.kill(pid, signal.SIGUSR1)
                    started, t0 = True, time.monotonic()
                else:                        # tentative terminée, prêt pour la suivante
                    ready = True
                    break
            elif what in (GET_HIDDEN, GET_NORMAL):
                recv_arr(parent)             # invite
                if pending is None:          # l'écran ne répond qu'une fois par tentative
                    parent.sendall(struct.pack("I", 0))
                else:
                    send_str(parent, pending)
                    parent.sendall(struct.pack("i", IS_PASSWORD))
                    pending = None
            elif what in (PUT_INFO, PUT_ERROR):
                print(f"  [message] {recv_arr(parent).decode(errors='replace').rstrip(chr(0))}")
            elif what == GET_BINARY:
                recv_arr(parent)
                parent.sendall(struct.pack("I", 0))
            elif what in (SUCCEEDED, FAILED, AUTH_ERROR, AUTH_ABORT):
                result = {SUCCEEDED: "accepté", FAILED: "refusé", AUTH_ERROR: "erreur",
                          AUTH_ABORT: "abandon"}[what]
                print(f"essai {attempt} ({'champ vide' if password == '' else 'mot de passe tapé'}) : "
                      f"{result} en {time.monotonic() - t0:.1f} s", flush=True)
            else:
                raise RuntimeError(f"requête inconnue : {what}")
    os.kill(pid, signal.SIGUSR2)
    os.waitpid(pid, 0)
    return 0 if result == "accepté" else 1


if __name__ == "__main__":
    sys.exit(main())
