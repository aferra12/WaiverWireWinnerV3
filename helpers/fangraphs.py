"""FanGraphs RosterResource Closer Depth Chart client.

This feed is what replaced the BigQuery rest-pattern query. It gives, per bullpen arm:
a curated role (Closer / Setup Man / ...), active-roster and IL status, season rate stats,
and the last six days of outings with pitch counts.

FanGraphs sits behind Cloudflare, which fingerprints the TLS handshake -- a plain
`requests` call is rejected with a 403 challenge page no matter what headers are sent.
`curl_cffi` replays a real Chrome fingerprint, which gets through.
"""

import time

import pandas as pd
from curl_cffi import requests as cffi

from .team_map import to_mlb_team_id

CLOSER_DEPTH_CHART_URL = (
    "https://www.fangraphs.com/api/roster-resource/closer-depth-charts/data"
)
_REFERER = "https://www.fangraphs.com/roster-resource/closer-depth-chart"

IL_ROLES = {"15-Day IL", "60-Day IL"}

# Roles ordered by how predictably the arm is used, highest first.
ROLE_PRIORITY = [
    "Closer",
    "Co-Closer",
    "Closer Committee",
    "Setup Man",
    "Middle Reliever",
    "Long Reliever",
]


class FanGraphsBlocked(RuntimeError):
    """Raised when Cloudflare serves a challenge instead of the feed."""


def fetch_closer_depth_chart(attempts: int = 3) -> dict:
    """Fetch the raw closer depth chart payload, retrying with backoff."""
    last_error = None

    for attempt in range(attempts):
        try:
            response = cffi.get(
                CLOSER_DEPTH_CHART_URL,
                impersonate="chrome",
                headers={"Referer": _REFERER, "Accept": "application/json"},
                timeout=30,
            )
        except Exception as exc:  # network/TLS failure
            last_error = exc
            time.sleep(2 ** attempt)
            continue

        if response.status_code == 200:
            try:
                payload = response.json()
            except Exception as exc:
                last_error = exc
                time.sleep(2 ** attempt)
                continue
            if "dataPlayers" in payload:
                return payload
            last_error = RuntimeError("payload missing 'dataPlayers'")
        elif response.status_code in (403, 503):
            raise FanGraphsBlocked(
                f"Cloudflare challenge from FanGraphs (HTTP {response.status_code}). "
                "curl_cffi impersonation was rejected -- try a different `impersonate` "
                "profile or the headless-browser fallback."
            )
        else:
            last_error = RuntimeError(f"HTTP {response.status_code}")

        time.sleep(2 ** attempt)

    raise RuntimeError(f"Could not fetch FanGraphs closer depth chart: {last_error}")


def parse_depth_chart(payload: dict):
    """Split the payload into (pitchers, usage) DataFrames.

    `pitchers` is one row per bullpen arm with role, status and season rates.
    `usage` is one row per outing over the last six days.
    """
    records = payload["dataPlayers"]

    pitchers = []
    usage_rows = []

    for player in records:
        mlbam_id = player.get("mlbamid")
        role = player.get("Role")
        fg_team = player.get("TeamAbbName")

        pitchers.append(
            {
                "mlbamId": mlbam_id,
                "playerName": player.get("playerName"),
                "fangraphsId": player.get("playerId"),
                "teamAbbrev": fg_team,
                "mlbTeamId": to_mlb_team_id(fg_team),
                "role": role,
                "tags": player.get("Tags"),
                "throws": player.get("throws"),
                "isActive": player.get("isActive") == 1,
                "isOnIl": role in IL_ROLES,
                "games": player.get("G"),
                "inningsPitched": player.get("IP"),
                "saves": player.get("SV"),
                "saveOpportunities": player.get("SVOpp"),
                "holds": player.get("HLD"),
                "blownSaves": player.get("BS"),
                "era": player.get("ERA"),
                "strikeoutRate": player.get("K%"),
                "walkRate": player.get("BB%"),
                "swingingStrikeRate": player.get("SwStr%"),
                "shutdowns": player.get("SD"),
                "meltdowns": player.get("MD"),
                "stuffPlus": player.get("sp_stuff"),
                # Rolling six-day workload; absent for arms who have not pitched.
                "recentPitches": (player.get("pitcherTotals") or {}).get("pitches"),
                "recentInnings": (player.get("pitcherTotals") or {}).get("ip"),
                # Days in the window spent in the minors or on the IL, e.g. {"AAA"}.
                "recentStatuses": sorted(
                    {
                        entry["valueOverride"]
                        for entry in player.get("pitcherUsage") or []
                        if entry.get("valueOverride")
                    }
                ),
            }
        )

        for outing in player.get("pitcherUsage") or []:
            # Rows in this array are calendar cells, not outings. A real appearance has
            # g == 1; the rest carry a valueOverride ("AAA", "AA", "IL") recording where
            # the pitcher was that day. Treating those as outings would badly misstate rest.
            if not outing.get("g"):
                continue

            usage_rows.append(
                {
                    "mlbamId": mlbam_id,
                    "playerName": player.get("playerName"),
                    "gameDate": outing.get("gameDate"),
                    "pitches": outing.get("pitches"),
                    "inningsPitched": outing.get("ip"),
                    "battersFaced": outing.get("tbf"),
                    "saves": outing.get("sv"),
                    "holds": outing.get("hld"),
                    "blownSaves": outing.get("bs"),
                    "leverageIndex": outing.get("LI"),
                }
            )

    pitchers_df = pd.DataFrame(pitchers)
    usage_df = pd.DataFrame(usage_rows)

    if not usage_df.empty:
        usage_df["gameDate"] = pd.to_datetime(usage_df["gameDate"]).dt.date

    return pitchers_df, usage_df


def get_depth_chart():
    """Fetch and parse in one call. Returns (pitchers_df, usage_df)."""
    return parse_depth_chart(fetch_closer_depth_chart())
