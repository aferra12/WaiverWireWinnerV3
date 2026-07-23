"""ESPN Fantasy client.

Two different notions of availability live here:

  * `get_ownership` -- the global roster percentage across all of ESPN, used to decide
    what is genuinely a waiver-wire name for the public posts.
  * `get_league_free_agents` -- who is actually unrostered in one specific league. A
    pitcher rostered 30% globally can still be sitting free in a shallow league, so this
    is the truthful filter for a personal list.

The `players_wl` view carries `ownership` without the per-player stat blocks that
`kona_player_info` returns, which cuts the payload from ~178 MB to under 1 MB.
"""

import os
from datetime import date

import pandas as pd
import requests

PLAYERS_URL = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/seasons/{season}/players"
)
LEAGUE_URL = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/seasons/{season}"
    "/segments/0/leagues/{league_id}"
)

# ESPN's status code for an unrostered player.
FREE_AGENT_STATUSES = ("FREEAGENT", "WAIVERS")

# ESPN identifies players by its own id, so ownership is joined on name.
_SUFFIXES = (" Jr.", " Sr.", " II", " III", " IV")


def normalize_name(name: str) -> str:
    """Normalize a player name for cross-source joining.

    Strips accents and generational suffixes, which are the two ways ESPN and FanGraphs
    routinely disagree (e.g. "Jonathan Loaisiga" vs "Jonathan Loáisiga").
    """
    if not isinstance(name, str):
        return ""

    import unicodedata

    cleaned = unicodedata.normalize("NFKD", name)
    cleaned = "".join(c for c in cleaned if not unicodedata.combining(c))

    for suffix in _SUFFIXES:
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]

    return cleaned.replace(".", "").replace("'", "").strip().lower()


def get_ownership(season: int = None) -> pd.DataFrame:
    """Return a DataFrame of [espnId, playerName, nameKey, percentOwned] for active players."""
    season = season or date.today().year

    response = requests.get(
        PLAYERS_URL.format(season=season),
        params={"scoringPeriodId": 0, "view": "players_wl"},
        headers={"X-Fantasy-Filter": '{"filterActive":{"value":true}}'},
        timeout=60,
    )
    response.raise_for_status()

    rows = []
    for player in response.json():
        ownership = player.get("ownership") or {}
        rows.append(
            {
                "espnId": player.get("id"),
                "espnName": player.get("fullName"),
                "nameKey": normalize_name(player.get("fullName")),
                "percentOwned": ownership.get("percentOwned"),
            }
        )

    df = pd.DataFrame(rows)
    # A name can appear twice across ESPN's universe; keep the most-owned entry.
    return df.sort_values("percentOwned", ascending=False).drop_duplicates("nameKey")


class LeagueAccessError(RuntimeError):
    """Raised when the private league cannot be read."""


def get_league_free_agents(season: int = None, league_id: str = None,
                           espn_s2: str = None, swid: str = None) -> set:
    """Return the set of normalized names unrostered in a private ESPN league.

    Credentials come from the environment (`ESPN_LEAGUE_ID`, `ESPN_S2`, `ESPN_SWID`)
    rather than being stored in code. Returns an empty set when the league is not
    configured, so the rest of the pipeline degrades to the public list alone.

    To refresh the cookies: sign in at fantasy.espn.com, open DevTools > Application >
    Cookies, and copy `espn_s2` and `SWID`. They expire periodically.
    """
    season = season or date.today().year
    league_id = league_id or os.environ.get("ESPN_LEAGUE_ID")
    espn_s2 = espn_s2 or os.environ.get("ESPN_S2")
    swid = swid or os.environ.get("ESPN_SWID")

    if not league_id:
        print("ESPN_LEAGUE_ID not set; skipping the league free-agent list.")
        return set()

    if not (espn_s2 and swid):
        print(
            "ESPN_S2 / ESPN_SWID not set; skipping the league free-agent list "
            "(the league is private and cannot be read without them)."
        )
        return set()

    cookies = {}
    if espn_s2 and swid:
        # ESPN wants SWID wrapped in braces.
        cookies = {
            "espn_s2": espn_s2,
            "SWID": swid if swid.startswith("{") else "{" + swid + "}",
        }

    # No `limit`: ESPN rejects a limit without a sort ("Filter: Limit request must be
    # accompanied by a sort"), and sorting by ownership then truncating silently drops the
    # least-owned free agents -- precisely the ones worth surfacing. Measured against a
    # real league, a 2000-row cap lost genuine free-agent relievers from the tail. The
    # unrestricted response is ~10 MB and under a second, so take the whole list.
    response = requests.get(
        LEAGUE_URL.format(season=season, league_id=league_id),
        params={"scoringPeriodId": 0, "view": "kona_player_info"},
        headers={
            "X-Fantasy-Filter": '{"players":{"filterStatus":{"value":["FREEAGENT","WAIVERS"]}}}'
        },
        cookies=cookies,
        timeout=120,
    )

    if response.status_code == 401:
        raise LeagueAccessError(
            f"ESPN league {league_id} returned 401. The league is private -- set "
            "ESPN_S2 and ESPN_SWID, and refresh them if they have expired."
        )
    response.raise_for_status()

    players = response.json().get("players", [])
    free_agents = set()
    for entry in players:
        if entry.get("status") and entry["status"] not in FREE_AGENT_STATUSES:
            continue
        name = (entry.get("player") or {}).get("fullName")
        if name:
            free_agents.add(normalize_name(name))

    print(f"  {len(free_agents)} free agents in league {league_id}")
    return free_agents
