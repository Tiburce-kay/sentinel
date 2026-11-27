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
    assert status(tmp_path) == {"sudo": "activé", "sudo-i": "absent", "polkit-1": "absent",
                                "gdm-password": "désactivé", "kde": "absent", "kde-fingerprint": "absent"}
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


def test_desactivation_par_l_administrateur_respectee_par_les_mises_a_jour(tmp_path):
    """Le paquet active Sentinel à chaque installation ou mise à jour (--from-package),
    sauf si un administrateur l'a désactivé ; le retrait du paquet ne compte pas comme
    une désactivation volontaire."""
    from sentinel.pamconf import main, status
    pam = tmp_path / "pam.d"
    pam.mkdir()
    (pam / "sudo").write_text(DEBIAN_SUDO)
    marker = tmp_path / "pam-desactive"
    cli = lambda *a: main(["sync", *a, "--pam-dir", str(pam), "--opt-out", str(marker)])
    assert cli("--sudo", "on", "--screen", "on", "--from-package") == 0       # installation
    assert status(pam)["sudo"] == "activé"
    assert cli("--sudo", "off", "--screen", "off", "--from-package") == 0     # retrait du paquet
    assert status(pam)["sudo"] == "désactivé" and not marker.exists()
    assert cli("--sudo", "on", "--screen", "on", "--from-package") == 0       # réinstallation
    assert status(pam)["sudo"] == "activé"
    assert cli("--sudo", "off", "--screen", "off") == 0                       # l'administrateur coupe
    assert marker.exists()
    assert cli("--sudo", "on", "--screen", "on", "--from-package") == 0       # mise à jour
    assert status(pam)["sudo"] == "désactivé"                                 # choix respecté
    assert cli("--sudo", "on", "--screen", "off") == 0                        # l'administrateur réactive
    assert status(pam)["sudo"] == "activé" and not marker.exists()


# --- KDE Plasma : fichiers réels de Kali (Plasma 6.7, 27/11/2026) ---------------------

KDE = """auth    requisite       pam_nologin.so
auth\trequired\tpam_succeed_if.so user != root quiet_success
@include common-auth
auth    optional        pam_kwallet5.so
@include common-account
session optional        pam_kwallet5.so auto_start
@include common-password
"""
KDE_FINGERPRINT = """auth    requisite       pam_nologin.so
auth\trequired\tpam_succeed_if.so user != root quiet_success
auth\trequired\tpam_fprintd.so
auth    optional        pam_kwallet5.so
@include common-account
session optional        pam_kwallet5.so auto_start
password required       pam_fprintd.so
"""


def _kde(tmp_path, fingerprint=True):
    (tmp_path / "kde").write_text(KDE)
    if fingerprint:
        (tmp_path / "kde-fingerprint").write_text(KDE_FINGERPRINT)
    return tmp_path


def test_plasma6_visage_dans_la_pile_parallele_mot_de_passe_intact(tmp_path):
    """Plasma 6 : visage juste avant l'empreinte digitale (pile lancée en parallèle) ;
    la pile du mot de passe ne reçoit que la ligne « mot de passe correct »."""
    from sentinel.pamconf import disable, sync
    _kde(tmp_path)
    done = sync(sudo=False, screen=True, pam_dir=tmp_path)
    assert done["kde"] == "activé" and done["kde-fingerprint"] == "activé"
    fp = (tmp_path / "kde-fingerprint").read_text().splitlines()
    i = next(n for n, l in enumerate(fp) if "pam_sentinel.so" in l)
    assert "[success=1 default=ignore]" in fp[i] and "pam_fprintd.so" in fp[i + 1]
    kde = (tmp_path / "kde").read_text().splitlines()
    assert [l for l in kde if "pam_sentinel" in l] == [l for l in kde if "notify" in l]   # notify seule
    assert kde.index(next(l for l in kde if "notify" in l)) == kde.index("@include common-auth") + 1
    sync(sudo=False, screen=False, pam_dir=tmp_path)
    assert (tmp_path / "kde").read_text() == KDE
    assert (tmp_path / "kde-fingerprint").read_text() == KDE_FINGERPRINT
    assert disable(KDE) == KDE


def test_plasma5_sans_pile_parallele_visage_avant_le_mot_de_passe(tmp_path):
    from sentinel.pamconf import FACE_LINE, sync
    _kde(tmp_path, fingerprint=False)
    sync(sudo=False, screen=True, pam_dir=tmp_path)
    kde = (tmp_path / "kde").read_text().splitlines()
    assert kde[kde.index("@include common-auth") - 1] == FACE_LINE


def test_disposition_kde_inattendue_ignoree_sans_bloquer_le_reste(tmp_path):
    from sentinel.pamconf import sync
    from tests.test_pamconf import DEBIAN_SUDO
    (tmp_path / "sudo").write_text(DEBIAN_SUDO)
    (tmp_path / "kde").write_text(KDE)
    (tmp_path / "kde-fingerprint").write_text("auth required pam_unix.so\n")   # sans pam_fprintd
    done = sync(sudo=True, screen=True, pam_dir=tmp_path)
    assert done["sudo"] == "activé" and done["kde-fingerprint"].startswith("ignoré")
    assert (tmp_path / "kde-fingerprint").read_text() == "auth required pam_unix.so\n"


# --- Plasma 5 sans /etc/pam.d/kde (Kubuntu 22.04, retour de testeur du 27/11/2026) ---

UBUNTU_OTHER = """#
# /etc/pam.d/other - specify the PAM fallback behaviour
#
@include common-auth
@include common-account
@include common-password
@include common-session
"""


def test_plasma5_kde_absent_cree_depuis_other_puis_supprime(tmp_path):
    """kcheckpass utilise le service « kde », absent sur Kubuntu 22.04 (PAM applique
    alors « other ») : Sentinel le crée comme copie de « other », l'active, et le
    supprime à la désactivation, ce qui rend au système son état exact d'origine."""
    from sentinel.pamconf import CREATED_HEADER, FACE_LINE, status, sync
    (tmp_path / "other").write_text(UBUNTU_OTHER)
    done = sync(sudo=False, screen=True, pam_dir=tmp_path, kde_locker=True)
    kde = (tmp_path / "kde").read_text()
    assert kde.startswith(CREATED_HEADER) and FACE_LINE in kde and done["kde"] == "activé"
    assert UBUNTU_OTHER.strip().splitlines()[-1] in kde
    assert oct((tmp_path / "kde").stat().st_mode & 0o777) == "0o644"
    done = sync(sudo=False, screen=False, pam_dir=tmp_path, kde_locker=True)
    assert not (tmp_path / "kde").exists() and status(tmp_path)["kde"] == "absent"
    assert (tmp_path / "other").read_text() == UBUNTU_OTHER


def test_pas_de_fichier_kde_cree_sans_ecran_de_verrouillage_kde(tmp_path):
    from sentinel.pamconf import sync
    (tmp_path / "other").write_text(UBUNTU_OTHER)
    sync(sudo=False, screen=True, pam_dir=tmp_path, kde_locker=False)
    assert not (tmp_path / "kde").exists()


def test_fichier_kde_fourni_par_la_distribution_jamais_supprime(tmp_path):
    from sentinel.pamconf import sync
    (tmp_path / "kde").write_text(KDE)
    sync(sudo=False, screen=True, pam_dir=tmp_path, kde_locker=True)
    sync(sudo=False, screen=False, pam_dir=tmp_path, kde_locker=True)
    assert (tmp_path / "kde").read_text() == KDE


def test_detection_de_l_ecran_de_verrouillage_kde(tmp_path):
    from sentinel.pamconf import kde_locker_present
    assert not kde_locker_present(tmp_path)
    p = tmp_path / "usr/lib/x86_64-linux-gnu/libexec/kcheckpass"
    p.parent.mkdir(parents=True)
    p.write_text("")
    assert kde_locker_present(tmp_path)
