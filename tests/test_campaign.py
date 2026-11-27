import pytest

from sentinel import campaign as cp


def A(pid, typ, ok, cam="c1", challenge="", elapsed=1.0):
    return cp.Attempt(pid, cam, "normale", typ, ok, "success" if ok else "spoof", elapsed, 0.8, 0.5, challenge)


def test_identifiants_sans_nom():
    assert cp.participant_id(" P07 ") == "p07"
    for bad in ("Jean", "p1", "p0001", "../x"):
        with pytest.raises(ValueError):
            cp.participant_id(bad)


def test_aller_retour_csv_et_rapport(tmp_path):
    path = tmp_path / "essais.csv"
    for a in [A("p01", "authentique", True, challenge="gauche"), A("p01", "authentique", False),
              A("p02", "authentique", True, cam="c2"), A("p01", "photo-ecran", False),
              A("p01", "video", False), A("p02", "imposteur", False)]:
        cp.append(path, a)
    back = cp.load(path)
    assert len(back) == 6 and back[0].challenge == "gauche"
    text = cp.report(back, {"p01": "lunettes", "p02": "sans lunettes"})
    assert "6 essais, 2 participant(s), 2 webcam(s)" in text
    assert "BPCER (vrai visage refusé)        33.3 % (1/3)" in text
    assert "APCER photo-ecran" in text and "0.0 % (0/1)" in text
    assert "lunettes" in text


def test_empreinte_de_webcam_non_reversible():
    tag = cp.camera_tag("04f2:b5ab||Integrated Camera")
    assert len(tag) == 8 and "04f2" not in tag
