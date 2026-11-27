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
    text = real.read_text()  # peut déjà contenir les lignes Sentinel
    assert disable(enable(text)).strip() == disable(text).strip()
    assert is_enabled(enable(text))


DEBIAN_GDM = """#%PAM-1.0
auth    requisite       pam_nologin.so
auth\trequired\tpam_succeed_if.so user != root quiet_success
@include common-auth
auth    optional        pam_gnome_keyring.so
@include common-account
"""


def test_disposition_trousseau_pour_l_ecran_de_connexion():
    out = enable(DEBIAN_GDM, keyring=True).splitlines()
    i = out.index("@include common-auth")
    assert "[success=ok default=2]" in out[i - 3] and "pam_sentinel.so" in out[i - 3]
    assert "pam_gnome_keyring.so" in out[i - 2]
    assert "[success=done default=die]" in out[i - 1] and "pam_permit.so" in out[i - 1]
    assert "notify" in out[i + 1]
    # La vérification « pas root » reste AVANT le visage.
    assert out.index("auth\trequired\tpam_succeed_if.so user != root quiet_success") < i - 3
    assert disable("\n".join(out) + "\n") == DEBIAN_GDM


def test_desactivation_d_une_ancienne_disposition_sufficient():
    old = enable(DEBIAN_GDM, keyring=False)
    assert disable(old) == DEBIAN_GDM
    assert enable(disable(old), keyring=True) != old


def test_synchronisation_des_choix_de_l_assistant(tmp_path):
    from sentinel.pamconf import status, sync
    for name, text in (("sudo", DEBIAN_SUDO), ("gdm-password", DEBIAN_GDM)):
        (tmp_path / name).write_text(text)
        (tmp_path / name).chmod(0o644)
    sync(sudo=True, screen=False, pam_dir=tmp_path)
    assert status(tmp_path) == {"sudo": "activé", "sudo-i": "absent",
                                "polkit-1": "absent", "gdm-password": "désactivé"}
    sync(sudo=False, screen=True, pam_dir=tmp_path)
    st = status(tmp_path)
    assert st["sudo"] == "désactivé" and st["gdm-password"] == "activé"
    assert (tmp_path / "sudo").read_text() == DEBIAN_SUDO   # restauré à l'identique


def test_sudo_i_qui_inclut_sudo_est_ignore(tmp_path):
    from sentinel.pamconf import sync
    (tmp_path / "sudo").write_text(DEBIAN_SUDO)
    (tmp_path / "sudo-i").write_text("#%PAM-1.0\n@include sudo\n")
    done = sync(sudo=True, screen=False, pam_dir=tmp_path)
    assert done["sudo"] == "activé" and done["sudo-i"].startswith("ignoré")
