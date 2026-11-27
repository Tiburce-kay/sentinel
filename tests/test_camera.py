import pytest

from sentinel.camera import CameraError, list_cameras, select_camera


def make_sysfs(tmp_path):
    """Arborescence sysfs factice : une webcam USB (2 nœuds) et une caméra virtuelle."""
    sys_v4l = tmp_path / "class"
    sys_v4l.mkdir()
    usb = tmp_path / "devices" / "usb1" / "1-8"
    iface = usb / "1-8:1.0"
    iface.mkdir(parents=True)
    (usb / "idVendor").write_text("04f2\n")
    (usb / "idProduct").write_text("b5ab\n")
    drv = tmp_path / "drivers" / "uvcvideo"
    drv.mkdir(parents=True)
    (iface / "driver").symlink_to(drv)
    for i in (0, 1):
        node = sys_v4l / f"video{i}"
        node.mkdir()
        (node / "name").write_text("Integrated Camera\n")
        (node / "index").write_text(f"{i}\n")
        (node / "device").symlink_to(iface)
    virt = sys_v4l / "video2"
    virt.mkdir()
    (virt / "name").write_text("Dummy video device (0x0000)\n")
    (virt / "index").write_text("0\n")
    return sys_v4l


def test_inventaire(tmp_path):
    cams = list_cameras(make_sysfs(tmp_path))
    assert [c.node for c in cams] == ["video0", "video1", "video2"]
    assert cams[0].is_uvc and cams[0].usb_id == "04f2:b5ab"
    assert not cams[2].is_uvc


def test_auto_choisit_le_flux_video_usb(tmp_path):
    assert select_camera("auto", True, make_sysfs(tmp_path)).node == "video0"


def test_camera_virtuelle_refusee(tmp_path):
    with pytest.raises(CameraError, match="refusée"):
        select_camera("/dev/video2", True, make_sysfs(tmp_path))


def test_camera_virtuelle_acceptee_si_desactive(tmp_path):
    assert select_camera("/dev/video2", False, make_sysfs(tmp_path)).node == "video2"


def test_identite_stable(tmp_path):
    a, b = list_cameras(make_sysfs(tmp_path))[:2]
    assert a.identity == b.identity


class _Cam:
    def __init__(self, n, drops):
        self.n, self.drops_frames = n, drops

    def frames(self, timeout):
        return iter(range(1, self.n + 1))


def test_lecteur_continu_sans_perte_en_simulation():
    from sentinel.camera import FrameGrabber

    seen, seq = [], 0
    with FrameGrabber(_Cam(50, drops=False), 5) as g:
        while True:
            seq, frame = g.wait_new(seq)
            if frame is None and g.ended:
                break
            if frame is not None:
                seen.append(frame)
    assert seen == list(range(1, 51))


def test_lecteur_continu_fin_de_flux():
    from sentinel.camera import FrameGrabber

    with FrameGrabber(_Cam(3, drops=True), 5) as g:
        import time
        time.sleep(0.1)
        assert g.latest() == 3
        seq, frame = g.wait_new(0)
        assert frame == 3
        assert g.wait_new(seq, timeout=0.2)[1] is None and g.ended


def test_exposition_reglee():
    from sentinel.camera import exposure_settled
    assert not exposure_settled([10, 40, 90, 120, 121], 5)         # encore en train de monter
    assert exposure_settled([10, 40, 90, 120, 121, 122, 121], 5)    # stable
    assert not exposure_settled([120, 121, 121], 5)                 # trop peu d'images


# --- choix parmi plusieurs webcams -------------------------------------------------

def _fake_sysfs(tmp_path, cams):
    """cams : liste de (nœud, nom, usb_id, index). Arborescence sysfs minimale."""
    import os
    root = tmp_path / "v4l"
    root.mkdir()
    for node, name, usb_id, index in cams:
        usb = tmp_path / "usb" / node
        (usb / "intf" / "drv").mkdir(parents=True)
        vid, pid = usb_id.split(":")
        (usb / "idVendor").write_text(vid)
        (usb / "idProduct").write_text(pid)
        drv = tmp_path / "drivers" / "uvcvideo"
        drv.mkdir(parents=True, exist_ok=True)
        os.symlink(drv, usb / "intf" / "driver")
        d = root / node
        d.mkdir()
        (d / "name").write_text(name)
        (d / "index").write_text(str(index))
        os.symlink(usb / "intf", d / "device")
    return root


def test_infrarouge_en_dernier_et_metadonnees_ignorees(tmp_path):
    from sentinel.camera import candidate_cameras
    root = _fake_sysfs(tmp_path, [("video0", "Integrated IR Camera", "0bda:5520", 0),
                                  ("video1", "Integrated IR Camera", "0bda:5520", 1),
                                  ("video2", "Integrated Camera", "0bda:5521", 0)])
    order = [c.node for c in candidate_cameras("auto", True, sysfs=root)]
    assert order == ["video2", "video0"]


def test_camera_de_l_enrolement_en_tete(tmp_path):
    from sentinel.camera import candidate_cameras
    root = _fake_sysfs(tmp_path, [("video0", "USB Webcam", "046d:0825", 0),
                                  ("video2", "Integrated Camera", "04f2:b5ab", 0)])
    cams = candidate_cameras("auto", True, sysfs=root)
    integrated = next(c for c in cams if c.node == "video2")
    order = [c.node for c in candidate_cameras("auto", True, integrated.identity, sysfs=root)]
    assert order == ["video2", "video0"]


class _Opened:
    def __init__(self, info, opens=True, color=True):
        self.info, self._opens, self._color, self.color, self.closed = info, opens, color, None, False

    def __enter__(self):
        from sentinel.camera import CameraError
        if not self._opens:
            raise CameraError(f"impossible d'ouvrir {self.info.device}")
        self.color = self._color
        return self

    def __exit__(self, *a):
        self.closed = True


def test_webcam_en_panne_on_passe_a_la_suivante(tmp_path, monkeypatch):
    from sentinel import camera as cm
    from sentinel.config import CameraConfig
    root = _fake_sysfs(tmp_path, [("video0", "Webcam A", "1111:0001", 0), ("video2", "Webcam B", "2222:0002", 0)])
    monkeypatch.setattr(cm, "SYSFS_V4L", root)
    monkeypatch.setattr(cm, "list_cameras", lambda sysfs=root: cm.__dict__["_list"](root))
    behaviour = {"video0": dict(opens=False), "video2": {}}
    cam = cm.open_camera(CameraConfig(), factory=lambda i: _Opened(i, **behaviour[i.node]))
    assert cam.info.node == "video2"


def test_niveaux_de_gris_garde_en_dernier_recours(tmp_path, monkeypatch):
    from sentinel import camera as cm
    from sentinel.config import CameraConfig
    root = _fake_sysfs(tmp_path, [("video0", "Cam X", "1111:0001", 0), ("video2", "Cam Y", "2222:0002", 0)])
    monkeypatch.setattr(cm, "list_cameras", lambda sysfs=root: cm.__dict__["_list"](root))
    made = {}
    def factory(i):
        made[i.node] = _Opened(i, color=(i.node == "video2"))
        return made[i.node]
    assert cm.open_camera(CameraConfig(), factory=factory).info.node == "video2"
    assert made["video0"].closed
    only_grey = cm.open_camera(CameraConfig(), factory=lambda i: _Opened(i, color=False))
    assert only_grey.color is False


def test_aucune_webcam_ne_repond(tmp_path, monkeypatch):
    import pytest
    from sentinel import camera as cm
    from sentinel.config import CameraConfig
    root = _fake_sysfs(tmp_path, [("video0", "Cam X", "1111:0001", 0)])
    monkeypatch.setattr(cm, "list_cameras", lambda sysfs=root: cm.__dict__["_list"](root))
    with pytest.raises(cm.CameraError):
        cm.open_camera(CameraConfig(), factory=lambda i: _Opened(i, opens=False))


def test_detection_couleur():
    import numpy as np
    from sentinel.camera import is_color
    grey = np.repeat(np.random.default_rng(0).integers(0, 255, (48, 64, 1), dtype=np.uint8), 3, axis=2)
    colour = grey.copy()
    colour[..., 2] = 255 - colour[..., 2]
    assert not is_color(grey) and is_color(colour)
