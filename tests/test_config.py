import pytest

from sentinel.config import ConfigError, load_config


def test_defauts(tmp_path):
    cfg = load_config(tmp_path / "absent.toml")
    assert cfg.strict_permissions and cfg.liveness.enabled


def test_surcharge(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('strict_permissions = false\n[recognition]\nthreshold = 0.6\n')
    cfg = load_config(p)
    assert cfg.recognition.threshold == 0.6


@pytest.mark.parametrize("text", [
    "inconnue = 1\n",
    "[recognition]\nthreshold = \"bas\"\n",
    "[liveness]\nenabled = 1\n",
])
def test_valeurs_invalides(tmp_path, text):
    p = tmp_path / "c.toml"
    p.write_text("strict_permissions = false\n" + text)
    with pytest.raises(ConfigError):
        load_config(p)


def test_fichier_non_root_refuse_en_mode_strict(tmp_path):
    import os
    if os.geteuid() == 0:
        pytest.skip("test à lancer sans root")
    p = tmp_path / "c.toml"
    p.write_text("[recognition]\nthreshold = 0.1\n")
    with pytest.raises(ConfigError, match="root"):
        load_config(p)
