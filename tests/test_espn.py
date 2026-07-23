"""Tests for the ESPN client: name joining and private-league credential handling."""

from unittest.mock import patch

import pytest

from helpers import espn


# --- name normalization (how ESPN joins to FanGraphs) ---------------------------------

def test_normalize_name_strips_accents():
    """FanGraphs writes 'Loáisiga', ESPN writes 'Loaisiga'."""
    assert espn.normalize_name("Jonathan Loáisiga") == espn.normalize_name("Jonathan Loaisiga")


def test_normalize_name_strips_generational_suffixes():
    assert espn.normalize_name("Daniel Lynch IV") == espn.normalize_name("Daniel Lynch")
    assert espn.normalize_name("Ronald Acuna Jr.") == espn.normalize_name("Ronald Acuna")


def test_normalize_name_ignores_punctuation_and_case():
    assert espn.normalize_name("A.J. Puk") == espn.normalize_name("aj puk")


def test_normalize_name_handles_missing_values():
    assert espn.normalize_name(None) == ""
    assert espn.normalize_name(float("nan")) == ""


# --- private league credentials -------------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {"players": []}

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _capture_cookies(**kwargs):
    """Run get_league_free_agents against a stub and return the cookies it sent."""
    sent = {}

    def fake_get(url, params=None, headers=None, cookies=None, timeout=None):
        sent.update(cookies or {})
        return _FakeResponse()

    with patch.object(espn.requests, "get", fake_get):
        espn.get_league_free_agents(2026, league_id="123", **kwargs)
    return sent


def test_swid_braces_are_optional():
    """ESPN's cookie ships wrapped in braces; pasting it either way must work."""
    with_braces = _capture_cookies(espn_s2="tok", swid="{A1B2-C3D4}")
    without = _capture_cookies(espn_s2="tok", swid="A1B2-C3D4")
    assert with_braces["SWID"] == without["SWID"] == "{A1B2-C3D4}"


def test_espn_s2_is_sent_verbatim():
    """The cookie is URL-encoded already -- decoding or re-encoding it breaks auth."""
    raw = "FAKE%2Bcookie%2Fvalue%3D%3D"
    assert _capture_cookies(espn_s2=raw, swid="{x}")["espn_s2"] == raw


def test_missing_credentials_skip_the_league_rather_than_failing(capsys):
    assert espn.get_league_free_agents(2026, league_id="123", espn_s2="", swid="") == set()
    assert "private" in capsys.readouterr().out


def test_missing_league_id_skips_quietly():
    with patch.dict("os.environ", {"ESPN_LEAGUE_ID": ""}, clear=False):
        assert espn.get_league_free_agents(2026, league_id="") == set()


def test_expired_cookies_raise_a_clear_error():
    with patch.object(espn.requests, "get", lambda *a, **k: _FakeResponse(status_code=401)):
        with pytest.raises(espn.LeagueAccessError, match="401"):
            espn.get_league_free_agents(2026, league_id="123", espn_s2="t", swid="{s}")


def test_only_unrostered_players_are_returned():
    payload = {
        "players": [
            {"status": "FREEAGENT", "player": {"fullName": "Paul Sewald"}},
            {"status": "WAIVERS", "player": {"fullName": "Kevin Ginkel"}},
            {"status": "ONTEAM", "player": {"fullName": "Josh Hader"}},
        ]
    }
    with patch.object(espn.requests, "get", lambda *a, **k: _FakeResponse(payload=payload)):
        free = espn.get_league_free_agents(2026, league_id="123", espn_s2="t", swid="{s}")

    assert free == {espn.normalize_name("Paul Sewald"), espn.normalize_name("Kevin Ginkel")}
    assert espn.normalize_name("Josh Hader") not in free
