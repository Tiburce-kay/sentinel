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
