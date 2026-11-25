import numpy as np
import pytest

from sentinel import calibrate as cal
from tests.test_auth import ME, OTHER, Scene, T, engine


def test_mesure_et_relecture(tmp_path):
    rows = cal.record(engine(), [Scene(ME), Scene(None), Scene(ME, live=-2)], T, "reel")
    assert [r.face for r in rows] == [True, False, True]
    assert rows[2].liveness == -2
    path = tmp_path / "c.csv"
    cal.append(path, rows)
    cal.append(path, cal.record(engine(), [Scene(OTHER)], T, "autre"))
    back = cal.load(path)
    assert len(back) == 4 and back[3].label == "autre"
    assert b"\xff\xd8" not in path.read_bytes()  # aucune image (JPEG) dans le fichier


def test_etiquette_invalide():
    with pytest.raises(ValueError):
        cal.record(engine(), [], T, "../x")


def test_regle_des_fenetres_consecutives():
    e = engine()
    # vivant : 1 image sur 2 seulement au-dessus du seuil -> aucune fenêtre de 3 acceptée
    rows = cal.record(e, [Scene(ME, live=1.0), Scene(ME, live=0.0)] * 10, T, "photo-ecran")
    assert cal.window_accept_rate(rows, 0.5, 0.5, 3) == 0.0
    rows = cal.record(e, [Scene(ME, live=1.0)] * 10, T, "reel")
    assert cal.window_accept_rate(rows, 0.5, 0.5, 3) == 1.0


def test_rapport():
    e = engine()
    rows = cal.record(e, [Scene(ME, live=2.0)] * 10, T, "reel")
    rows += cal.record(e, [Scene(ME, live=-3.0)] * 10, T, "photo-ecran")
    text = cal.report(rows, 0.5, 0.5, 3)
    assert "BPCER" in text and "photo-ecran" in text and "← actuel" in text
