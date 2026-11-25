import os

from sentinel.peer import _logind, has_graphical_session


def S(cls, typ, state="active"):
    return {"name": "x", "class": cls, "type": typ, "state": state}


def test_session_graphique_ouverte():
    assert has_graphical_session([S("manager", "unspecified"), S("user", "wayland")])
    assert has_graphical_session([S("user", "x11", "online")])


def test_sessions_qui_ne_comptent_pas():
    # Cas réel : la session « manager » persiste à côté de la session graphique.
    assert not has_graphical_session([S("manager", "unspecified")])
    assert not has_graphical_session([S("user", "wayland", "closing")])
    assert not has_graphical_session([S("user", "tty")])
    assert not has_graphical_session([S("greeter", "wayland")])
    assert not has_graphical_session([])


def test_lecture_reelle_de_logind():
    """Sur une machine avec logind, la lecture ne plante pas et renvoie des champs complets."""
    for s in _logind.sessions(os.getuid()):
        assert set(s) == {"name", "class", "type", "state"}
