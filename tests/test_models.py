import shutil

import pytest

from sentinel.models import ModelError, load_verified, manifest


def test_manifeste_complet():
    m = manifest()
    assert set(m) == {"detector", "recognizer", "liveness"}
    assert all(len(s.sha256) == 64 for s in m.values())


def test_modele_intact_accepte(models_dir):
    assert len(load_verified(models_dir, "detector")) > 1000


def test_modele_modifie_refuse(models_dir, tmp_path):
    for spec in manifest().values():
        shutil.copy(models_dir / spec.file, tmp_path / spec.file)
    f = tmp_path / manifest()["liveness"].file
    data = bytearray(f.read_bytes())
    data[-1] ^= 0xFF
    f.write_bytes(bytes(data))
    with pytest.raises(ModelError, match="empreinte invalide"):
        load_verified(tmp_path, "liveness")


def test_modele_absent(tmp_path):
    with pytest.raises(ModelError, match="absent"):
        load_verified(tmp_path, "detector")
