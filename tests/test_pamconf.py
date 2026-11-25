import pytest

from sentinel.pamconf import PamConfError, apply, disable, enable, is_enabled

DEBIAN_SUDO = """#%PAM-1.0

# Set up user limits from /etc/security/limits.conf.
session    required   pam_limits.so

@include common-auth
@include common-account
@include common-session-noninteractive
"""


def test_activation_encadre_le_mot_de_passe():
    out = enable(DEBIAN_SUDO).splitlines()
    i = out.index("@include common-auth")
    assert "sufficient" in out[i - 1] and "pam_sentinel.so" in out[i - 1]
    assert "notify" in out[i + 1] and "optional" in out[i + 1]
    assert is_enabled("\n".join(out))


def test_activation_idempotente_et_reversible():
    once = enable(DEBIAN_SUDO)
    assert enable(once) == once
    assert disable(once) == DEBIAN_SUDO


def test_disposition_inconnue_refusee():
    fedora = "auth       include      system-auth\naccount    include      system-auth\n"
    with pytest.raises(PamConfError):
        enable(fedora)


def test_fichier_reel_sauvegarde_et_droits(tmp_path):
    f = tmp_path / "sudo"
    f.write_text(DEBIAN_SUDO)
    f.chmod(0o644)
    assert apply("sudo", True, tmp_path) == "activé"
    assert (tmp_path / "sudo.sentinel-backup").read_text() == DEBIAN_SUDO
    assert oct(f.stat().st_mode & 0o777) == "0o644"
    assert apply("sudo", True, tmp_path) == "déjà activé"
    assert apply("sudo", False, tmp_path) == "désactivé"
    assert f.read_text() == DEBIAN_SUDO


def test_service_hors_liste_refuse(tmp_path):
    (tmp_path / "sshd").write_text(DEBIAN_SUDO)
    with pytest.raises(PamConfError):
        apply("sshd", True, tmp_path)


def test_fichier_reel_de_cette_machine():
    """La configuration sudo réelle de la machine doit être prise en charge."""
    from pathlib import Path
    real = Path("/etc/pam.d/sudo")
    if not real.exists():
        pytest.skip("pas de /etc/pam.d/sudo")
    text = real.read_text()
    assert disable(enable(text)).strip() == text.strip()
