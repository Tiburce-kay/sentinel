"""Client du service sentineld (utilisé par la CLI et l'interface graphique)."""

from __future__ import annotations

import json
import socket
from collections.abc import Iterator
from pathlib import Path


class ServiceError(Exception):
    pass


class Client:
    def __init__(self, path: Path, timeout: float = 90.0):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        try:
            self.sock.connect(str(path))
        except OSError as exc:
            raise ServiceError(f"service Sentinel injoignable ({path}) : {exc.strerror}") from None
        self._rfile = self.sock.makefile("rb")

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._rfile.close()
        self.sock.close()

    def _send(self, req: dict) -> None:
        self.sock.sendall((json.dumps(req) + "\n").encode())

    def _recv(self) -> dict:
        line = self._rfile.readline()
        if not line:
            raise ServiceError("connexion fermée par le service")
        return json.loads(line)

    def call(self, op: str, **params) -> dict:
        self._send({"op": op, **params})
        return self._recv()

    def stream(self, op: str, **params) -> Iterator[dict]:
        """Pour les opérations qui envoient plusieurs messages (enroll_start)."""
        self._send({"op": op, **params})
        while True:
            msg = self._recv()
            yield msg
            if msg.get("event") != "progress":
                return
