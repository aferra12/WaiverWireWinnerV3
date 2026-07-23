"""Mapping between FanGraphs team abbreviations and MLB Stats API team ids.

FanGraphs' RosterResource feed identifies teams by `TeamAbbName`, which agrees with
MLB's `abbreviation` for 23 of 30 clubs. The seven below differ and need aliasing.
"""

import requests

# FanGraphs TeamAbbName -> MLB Stats API abbreviation, for the clubs where they disagree.
FANGRAPHS_TO_MLB_ABBREV = {
    "ARI": "AZ",
    "CHW": "CWS",
    "KCR": "KC",
    "SDP": "SD",
    "SFG": "SF",
    "TBR": "TB",
    "WSN": "WSH",
}

_TEAMS_URL = "https://statsapi.mlb.com/api/v1/teams?sportId=1"

_cache = None


def get_team_lookup() -> dict:
    """Return {fangraphs_abbrev: {"id": mlb_team_id, "name": full name}} for all 30 clubs.

    Fetched from MLB rather than hardcoded so club renames and relocations are picked
    up automatically. Cached for the life of the process.
    """
    global _cache
    if _cache is not None:
        return _cache

    response = requests.get(_TEAMS_URL, timeout=30)
    response.raise_for_status()
    teams = response.json()["teams"]

    by_mlb_abbrev = {t["abbreviation"]: t for t in teams}

    lookup = {}
    for fg_abbrev, mlb_abbrev in FANGRAPHS_TO_MLB_ABBREV.items():
        team = by_mlb_abbrev[mlb_abbrev]
        lookup[fg_abbrev] = {"id": team["id"], "name": team["name"]}

    # Everything not aliased above shares the same abbreviation on both sides.
    for mlb_abbrev, team in by_mlb_abbrev.items():
        lookup.setdefault(mlb_abbrev, {"id": team["id"], "name": team["name"]})

    _cache = lookup
    return lookup


def to_mlb_team_id(fangraphs_abbrev: str):
    """Translate a FanGraphs team abbreviation to an MLB Stats API team id."""
    team = get_team_lookup().get(fangraphs_abbrev)
    return team["id"] if team else None
