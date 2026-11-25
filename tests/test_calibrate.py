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


def test_regle_de_la_moyenne_sur_fenetre():
    e = engine()
    # moyenne 0,5 : acceptée au seuil 0, refusée au seuil 1
    rows = cal.record(e, [Scene(ME, live=1.0), Scene(ME, live=0.0)] * 10, T, "reel")
    assert cal.window_accept_rate(rows, 0.5, 0.0, 4) == 1.0
    assert cal.window_accept_rate(rows, 0.5, 1.0, 4) == 0.0
    # une image non reconnue invalide toutes les fenêtres qui la contiennent
    rows = cal.record(e, [Scene(ME)] * 4 + [Scene(OTHER)] + [Scene(ME)] * 4, T, "reel")
    assert cal.window_accept_rate(rows, 0.5, 0.0, 4) == 2 / 6


def test_rapport():
    e = engine()
    rows = cal.record(e, [Scene(ME, live=2.0)] * 10, T, "reel")
    rows += cal.record(e, [Scene(ME, live=-3.0)] * 10, T, "photo-ecran")
    text = cal.report(rows, 0.5, 0.5, 3)
    assert "BPCER" in text and "photo-ecran" in text and "← seuil actuel" in text
    assert "pire -3.00" in text
