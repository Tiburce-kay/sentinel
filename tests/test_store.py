import json
import os

import numpy as np
import pytest

from sentinel.store import (Enrollment, StoreError, TamperedError, TemplateStore,
                            validate_username)
from tests.conftest import unit


@pytest.fixture
def store(tmp_path):
    s = TemplateStore(tmp_path / "store", strict_permissions=False)
    s.init()
    return s


def plant(path, data: bytes):
    """Dépose un fichier avec des droits corrects : seul le contenu est en cause."""
    path.write_bytes(data)
    os.chmod(path, 0o600)


def emb(n=3, seed=0):
    return np.stack([unit(seed + i) for i in range(n)])


def test_aller_retour(store):
    store.save(Enrollment("alice", emb(), camera="04f2:b5ab||cam"))
    got = store.load("alice")
    assert np.array_equal(got.embeddings, emb())
    assert got.camera == "04f2:b5ab||cam"
    assert store.users() == ["alice"]


def test_aucune_donnee_en_clair(store):
    store.save(Enrollment("alice", emb()))
    raw = (store.users_dir / "alice.tpl").read_bytes()
    assert emb().tobytes()[:16] not in raw
    assert b"embeddings" not in raw


def test_modification_detectee(store):
    store.save(Enrollment("alice", emb()))
    path = store.users_dir / "alice.tpl"
    doc = json.loads(path.read_text())
    ct = bytearray(__import__("base64").b64decode(doc["ciphertext"]))
    ct[5] ^= 1
    doc["ciphertext"] = __import__("base64").b64encode(bytes(ct)).decode()
    plant(path, json.dumps(doc).encode())
    with pytest.raises(TamperedError):
        store.load("alice")


def test_face_swap_gabarit_deplace_vers_un_autre_compte(store):
    """Copier le gabarit de l'attaquant dans le compte de la victime doit échouer."""
    store.save(Enrollment("mallory", emb(seed=99)))
    src = store.users_dir / "mallory.tpl"
    doc = json.loads(src.read_text())
    doc["user"] = "alice"
    plant(store.users_dir / "alice.tpl", json.dumps(doc).encode())
    with pytest.raises(TamperedError):
        store.load("alice")
    # Même sans modifier le champ « user », le fichier reste lié à mallory.
    plant(store.users_dir / "alice.tpl", src.read_bytes())
    with pytest.raises(TamperedError):
        store.load("alice")


def test_faceplant_gabarit_fabrique_sans_la_cle(store, tmp_path):
    """Un gabarit chiffré avec une autre clé (autre machine) est refusé."""
    other = TemplateStore(tmp_path / "autre", strict_permissions=False)
    other.init()
    other.save(Enrollment("alice", emb(seed=42)))
    plant(store.users_dir / "alice.tpl", (other.users_dir / "alice.tpl").read_bytes())
    with pytest.raises(TamperedError):
        store.load("alice")


@pytest.mark.parametrize("bad", ["../etc/passwd", "a/b", "", "Alice", "x" * 40, "-rf", "a b"])
def test_noms_dangereux_refuses(bad):
    with pytest.raises(StoreError):
        validate_username(bad)


def test_droits_trop_ouverts_refuses(store):
    os.chmod(store.key_path, 0o644)
    with pytest.raises(StoreError):
        store.check_permissions()


def test_lien_symbolique_refuse(store, tmp_path):
    store.save(Enrollment("alice", emb()))
    path = store.users_dir / "alice.tpl"
    target = tmp_path / "ailleurs.tpl"
    target.write_bytes(path.read_bytes())
    os.chmod(target, 0o600)
    path.unlink()
    path.symlink_to(target)
    with pytest.raises((StoreError, OSError)):
        store.load("alice")


def test_mode_strict_exige_root(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("test à lancer sans root")
    s = TemplateStore(tmp_path / "s", strict_permissions=False)
    s.init()
    with pytest.raises(StoreError):
        TemplateStore(tmp_path / "s", strict_permissions=True).check_permissions()


def test_compteur_echecs(store):
    assert store.failures("alice") == 0
    store.set_failures("alice", 3)
    assert store.failures("alice") == 3
    plant(store.state_dir / "alice.json", b"{corrompu")
    assert store.failures("alice") > 1000  # état illisible = verrouillé


def test_suppression(store):
    store.save(Enrollment("alice", emb()))
    assert store.remove("alice")
    assert store.load("alice") is None
    assert not store.remove("alice")


def test_gabarits_invalides_refuses(store):
    with pytest.raises(StoreError):
        store.save(Enrollment("alice", np.zeros((2, 64), np.float32)))
