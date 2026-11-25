"""Accès à la webcam et vérification de son identité.

Une caméra virtuelle (v4l2loopback, OBS…) permettrait de « montrer » une vidéo de
la victime au système. Sentinel n'accepte donc que des caméras physiques USB
(pilote uvcvideo), et peut exiger que ce soit la même caméra qu'à l'enrôlement.

Limite connue : un périphérique USB malveillant qui recopie les identifiants de la
vraie caméra passe ce contrôle. La détection du vivant reste alors la défense.
"""

from __future__ import annotations

import os
import threading
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SYSFS_V4L = Path("/sys/class/video4linux")


class CameraError(Exception):
    pass


@dataclass(frozen=True)
class CameraInfo:
    node: str              # ex. video0
    name: str
    driver: str
    usb_id: str            # ex. 04f2:b5c0, vide si non USB
    serial: str
    bus_path: str          # emplacement physique (port USB)
    index: int             # 0 = flux vidéo principal ; 1 = métadonnées

    @property
    def device(self) -> str:
        return f"/dev/{self.node}"

    @property
    def is_uvc(self) -> bool:
        return self.driver == "uvcvideo" and bool(self.usb_id)

    @property
    def identity(self) -> str:
        """Identité stable de la caméra, enregistrée à l'enrôlement."""
        return f"{self.usb_id}|{self.serial}|{self.name}"


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace").strip()
    except OSError:
        return ""


def describe(node_dir: Path) -> CameraInfo:
    node = node_dir.name
    dev = node_dir / "device"
    driver = ""
    if (dev / "driver").exists():
        driver = os.path.basename(os.path.realpath(dev / "driver"))
    usb_id = serial = bus_path = ""
    # On remonte l'arborescence sysfs jusqu'au périphérique USB parent.
    cur = Path(os.path.realpath(dev)) if dev.exists() else None
    while cur is not None and cur != cur.parent:
        if (cur / "idVendor").exists() and (cur / "idProduct").exists():
            usb_id = f"{_read(cur / 'idVendor')}:{_read(cur / 'idProduct')}"
            serial = _read(cur / "serial")
            bus_path = cur.name
            break
        cur = cur.parent
    idx = _read(node_dir / "index")
    return CameraInfo(
        node=node,
        name=_read(node_dir / "name"),
        driver=driver,
        usb_id=usb_id,
        serial=serial,
        bus_path=bus_path,
        index=int(idx) if idx.isdigit() else 0,
    )


def list_cameras(sysfs: Path = SYSFS_V4L) -> list[CameraInfo]:
    if not sysfs.exists():
        return []
    nodes = sorted(
        (p for p in sysfs.iterdir() if re.fullmatch(r"video\d+", p.name)),
        key=lambda p: int(p.name[5:]),
    )
    return [describe(p) for p in nodes]


def select_camera(device: str, require_uvc: bool, sysfs: Path = SYSFS_V4L) -> CameraInfo:
    cams = list_cameras(sysfs)
    if device == "auto":
        candidates = [c for c in cams if c.index == 0 and (c.is_uvc or not require_uvc)]
        if not candidates:
            raise CameraError("aucune webcam utilisable trouvée")
        return candidates[0]
    node = Path(device).name
    for cam in cams:
        if cam.node == node:
            if require_uvc and not cam.is_uvc:
                raise CameraError(
                    f"{cam.device} n'est pas une webcam USB physique (pilote « {cam.driver or 'aucun'} ») : refusée"
                )
            return cam
    raise CameraError(f"caméra introuvable : {device}")


class Camera:
    """Flux d'images BGR depuis une webcam. Utilisable avec « with »."""

    def __init__(self, info: CameraInfo, width: int, height: int, warmup: int = 5):
        self.info = info
        self._width, self._height, self._warmup = width, height, warmup
        self._cap = None

    def __enter__(self) -> "Camera":
        import cv2

        cap = cv2.VideoCapture(self.info.device, cv2.CAP_V4L2)
        if not cap.isOpened():
            raise CameraError(f"impossible d'ouvrir {self.info.device} (droits ? caméra occupée ?)")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._cap = cap
        for _ in range(self._warmup):
            cap.read()
        return self

    def __exit__(self, *exc) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def frames(self, timeout_s: float) -> Iterator[np.ndarray]:
        if self._cap is None:
            raise CameraError("caméra non ouverte")
        deadline = time.monotonic() + timeout_s
        failures = 0
        while time.monotonic() < deadline:
            ok, frame = self._cap.read()
            if not ok or frame is None:
                failures += 1
                if failures > 10:
                    raise CameraError("la caméra ne renvoie plus d'images")
                continue
            failures = 0
            yield frame


class FrameGrabber:
    """Lit la caméra en continu dans un fil d'exécution séparé.

    L'aperçu (fluide) et l'analyse (plus lente) prennent chacun la dernière image
    disponible, sans s'attendre l'un l'autre. Avec une caméra simulée (tests),
    `drops_frames = False` fait attendre le lecteur : aucune image n'est sautée.
    """

    def __init__(self, cam, timeout_s: float):
        self._frames = cam.frames(timeout_s)
        self._drop = getattr(cam, "drops_frames", True)
        self._cond = threading.Condition()
        self._frame = None
        self._seq = 0
        self._taken = 0
        self._ended = False
        self._error: BaseException | None = None
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> "FrameGrabber":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify_all()
        self._thread.join(timeout=2)

    def _run(self) -> None:
        try:
            for frame in self._frames:
                with self._cond:
                    while not self._drop and self._taken < self._seq and not self._stop:
                        self._cond.wait(0.5)
                    if self._stop:
                        return
                    self._frame, self._seq = frame, self._seq + 1
                    self._cond.notify_all()
        except BaseException as exc:  # noqa: BLE001 — transmis au lecteur
            self._error = exc
        finally:
            with self._cond:
                self._ended = True
                self._cond.notify_all()

    def latest(self):
        with self._cond:
            return self._frame

    def wait_new(self, last_seq: int, timeout: float = 1.0):
        """Attend une image plus récente que `last_seq`. Renvoie (seq, image) ou (seq, None)
        à la fin du flux."""
        with self._cond:
            while self._seq <= last_seq and not self._ended and not self._stop:
                if not self._cond.wait(timeout):
                    break
            if self._error is not None:
                raise self._error
            if self._seq <= last_seq:
                return last_seq, None
            self._taken = self._seq
            self._cond.notify_all()
            return self._seq, self._frame

    @property
    def ended(self) -> bool:
        with self._cond:
            return self._ended and self._taken >= self._seq
