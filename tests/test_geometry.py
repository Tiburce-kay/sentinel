from sentinel.geometry import Pose, parallax
from tests.head import FLAT, HEAD, project, rotate


def pose(pts3d, **kw):
    return Pose.of(project(rotate(pts3d, **kw)))


def test_sens_de_rotation():
    face = pose(HEAD)
    assert abs(face.yaw) < 0.02
    assert pose(HEAD, yaw_deg=25).yaw > face.yaw + 0.10     # gauche
    assert pose(HEAD, yaw_deg=-25).yaw < face.yaw - 0.10    # droite
    assert pose(HEAD, pitch_deg=20).pitch < face.pitch - 0.08  # haut
    assert pose(HEAD, pitch_deg=-20).pitch > face.pitch + 0.08  # bas


def test_relief_vrai_visage_vs_photo():
    ref_head = project(rotate(HEAD))
    ref_photo = project(rotate(FLAT))
    for kw in ({"yaw_deg": 25}, {"yaw_deg": -25}, {"pitch_deg": 20}, {"pitch_deg": -20}):
        real = parallax(ref_head, project(rotate(HEAD, **kw)))
        # Photo inclinée encore plus fort, avec perspective : elle reste plate.
        photo = parallax(ref_photo, project(rotate(FLAT, **{k: 1.6 * v for k, v in kw.items()})))
        assert real > 0.10, (kw, real)
        assert photo < 0.02, (kw, photo)


def test_photo_inclinee_ne_simule_pas_la_rotation():
    """Incliner une photo ne déplace pas le nez par rapport aux yeux comme une vraie tête."""
    flat = pose(FLAT)
    assert abs(pose(FLAT, yaw_deg=40).yaw - flat.yaw) < 0.05
