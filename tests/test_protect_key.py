"""packaging/protect-key.sh dans tous ses scénarios, avec des imitations de systemd.

« Chiffrer » = écrire le mode puis la clé en base64 ; le « contrôle de santé » réussit
si le service est relié au coffre ET que le coffre contient la clé qui a réellement
chiffré les visages (TRUE_KEY). Cela reproduit fidèlement l'incident du 27/11.
"""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "packaging" / "protect-key.sh"

STUBS = {
    "systemd-creds": r'''#!/bin/sh
case "$1" in
  --help) echo "encrypt decrypt" ;;
  encrypt) mode=""; for a in "$@"; do case "$a" in --with-key=*) mode="${a#--with-key=}";; esac; done
           eval in=\${$(($#-1))}; eval out=\${$#}
           { echo "$mode"; base64 -w0 "$in"; echo; } > "$out" ;;
  decrypt) [ -n "${FAIL_DECRYPT:-}" ] && exit 1
           eval in=\${$(($#-1))}; eval out=\${$#}
           tail -n +2 "$in" | base64 -d > "$out" ;;
esac
''',
    "systemctl": "#!/bin/sh\nexit 0\n",
    "systemd-analyze": '#!/bin/sh\nif [ "${TPM2:-0}" = 1 ]; then echo yes; exit 0; fi\necho "-firmware"; exit 1\n',
}
HEALTH = r'''#!/bin/sh
[ -f "$SENTINEL_DROPIN" ] && tail -n +2 "$SENTINEL_CRED" | base64 -d | cmp -s - "$TRUE_KEY"
'''


@pytest.fixture
def env(tmp_path):
    stubs = tmp_path / "bin"
    stubs.mkdir()
    for name, body in STUBS.items():
        (stubs / name).write_text(body)
        (stubs / name).chmod(0o755)
    py = tmp_path / "prefix" / "venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text(HEALTH)
    py.chmod(0o755)
    (tmp_path / "etc").mkdir()
    (tmp_path / "lib").mkdir()
    true_key = tmp_path / "vraie-cle"
    true_key.write_bytes(os.urandom(32))
    e = {**os.environ, "PATH": f"{stubs}:{os.environ['PATH']}", "SENTINEL_FORCE_SYSTEMD": "1",
         "SENTINEL_CRED": str(tmp_path / "etc" / "cred"), "SENTINEL_DROPIN": str(tmp_path / "etc" / "dropin.conf"),
         "SENTINEL_KEY": str(tmp_path / "lib" / "master.key"), "SENTINEL_TMPDIR": str(tmp_path),
         "SENTINEL_WAIT": "0", "TRUE_KEY": str(true_key), "TPM2": "0"}
    e["_root"] = str(tmp_path)
    return e


def P(env, name):
    return Path(env[{"cred": "SENTINEL_CRED", "dropin": "SENTINEL_DROPIN", "key": "SENTINEL_KEY",
                     "true": "TRUE_KEY"}[name]])


def run(env, **extra):
    e = {**env, **extra}
    r = subprocess.run(["sh", str(SCRIPT), str(Path(env["_root"]) / "prefix")], env=e,
                       capture_output=True, text=True, timeout=30)
    return r.returncode, r.stdout + r.stderr


def vault_key(env):
    import base64
    return base64.b64decode(P(env, "cred").read_text().splitlines()[1])


def test_premiere_protection(env):
    P(env, "key").write_bytes(P(env, "true").read_bytes())
    code, out = run(env)
    assert code == 0, out
    assert vault_key(env) == P(env, "true").read_bytes()
    assert P(env, "dropin").exists() and not P(env, "key").exists()


def test_incident_reinstallation_apres_desinstallation_keep_data(env):
    """Coffre présent, service non relié (drop-in retiré), et une clé NEUVE en fichier
    (créée à tort) : la clé du coffre ne doit JAMAIS être remplacée."""
    P(env, "key").write_bytes(P(env, "true").read_bytes())
    run(env)                                            # protection initiale
    P(env, "dropin").unlink()                           # désinstallation --keep-data
    P(env, "key").write_bytes(os.urandom(32))           # clé neuve créée à tort
    code, out = run(env)
    assert code != 0 and "diffèrent" in out
    assert vault_key(env) == P(env, "true").read_bytes()    # la bonne clé est intacte


def test_reinstallation_normale_relie_le_coffre(env):
    P(env, "key").write_bytes(P(env, "true").read_bytes())
    run(env)
    P(env, "dropin").unlink()                           # désinstallation --keep-data, réinstallation
    code, out = run(env)
    assert code == 0, out
    assert P(env, "dropin").exists() and vault_key(env) == P(env, "true").read_bytes()


def test_passage_a_la_tpm2_meme_cle(env):
    P(env, "key").write_bytes(P(env, "true").read_bytes())
    run(env)
    assert P(env, "cred").read_text().startswith("host")
    code, out = run(env, TPM2="1")
    assert code == 0, out
    assert P(env, "cred").read_text().startswith("tpm2+host")
    assert vault_key(env) == P(env, "true").read_bytes() and not P(env, "key").exists()


def test_controle_en_echec_retour_arriere(env):
    """La clé en fichier n'est pas celle des visages : la protection est annulée et la
    clé en fichier conservée."""
    P(env, "key").write_bytes(os.urandom(32))
    code, out = run(env)
    assert code != 0
    assert P(env, "key").exists() and not P(env, "cred").exists() and not P(env, "dropin").exists()


def test_deja_protegee_rien_a_faire(env):
    P(env, "key").write_bytes(P(env, "true").read_bytes())
    run(env)
    before = P(env, "cred").read_bytes()
    code, out = run(env)
    assert code == 0 and "clé protégée" in out and P(env, "cred").read_bytes() == before


def test_coffre_illisible_rien_n_est_modifie(env):
    P(env, "key").write_bytes(P(env, "true").read_bytes())
    run(env)
    before = P(env, "cred").read_bytes()
    P(env, "key").write_bytes(P(env, "true").read_bytes())
    code, out = run(env, FAIL_DECRYPT="1", TPM2="1")
    assert code != 0 and P(env, "cred").read_bytes() == before and P(env, "key").exists()


def test_systemd_trop_ancien_cle_gardee_dans_le_fichier(env):
    """Ubuntu 22.04 (systemd 249, sans systemd-creds encrypt) : rien n'est chiffré ni
    effacé, le service garde sa clé dans le fichier root."""
    (Path(env["_root"]) / "bin" / "systemd-creds").write_text("#!/bin/sh\necho 'decrypt'\n")
    P(env, "key").write_bytes(P(env, "true").read_bytes())
    code, out = run(env)
    assert code == 0, out
    assert "systemd < 250" in out
    assert P(env, "key").read_bytes() == P(env, "true").read_bytes()
    assert not P(env, "cred").exists() and not P(env, "dropin").exists()


def _slow_service(env, not_ready: int):
    """Service qui ne répond qu'après `not_ready` essais (clé ouverte lentement par la TPM)."""
    py = Path(env["_root"]) / "prefix" / "venv" / "bin" / "python"
    counter = Path(env["_root"]) / "essais"
    py.write_text(f'''#!/bin/sh
n=$(cat "{counter}" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "{counter}"
[ "$n" -le {not_ready} ] && exit 3
''' + HEALTH.split("\n", 1)[1])
    return counter


def test_incident_tpm_lente_le_controle_attend_le_service(env):
    """Installation réelle du 27/11 : la TPM a mis 4,2 s à ouvrir la clé ; le contrôle,
    lancé trop tôt, avait annulé la protection. Il attend désormais le service."""
    counter = _slow_service(env, not_ready=3)
    P(env, "key").write_bytes(P(env, "true").read_bytes())
    code, out = run(env, SENTINEL_HEALTH_DELAY="0")
    assert code == 0, out
    assert int(counter.read_text()) == 4
    assert vault_key(env) == P(env, "true").read_bytes()
    assert P(env, "dropin").exists() and not P(env, "key").exists()


def test_service_muet_retour_a_la_cle_en_fichier(env):
    """Service qui ne répond jamais : après les essais, retour arrière, rien de perdu."""
    _slow_service(env, not_ready=999)
    P(env, "key").write_bytes(P(env, "true").read_bytes())
    code, out = run(env, SENTINEL_HEALTH_DELAY="0", SENTINEL_HEALTH_TRIES="5")
    assert "ne répond pas" in out
    assert P(env, "key").read_bytes() == P(env, "true").read_bytes()
    assert not P(env, "dropin").exists()
