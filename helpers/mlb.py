"""MLB Stats API client.

Supplies everything FanGraphs does not: which teams play today, how many games each club
has played (the denominator for a reliever's appearance rate), the recent team schedule
(so rest is counted in *team games* rather than calendar days), and per-appearance game
logs used to score each pitcher.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import requests

SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
STANDINGS_URL = "https://statsapi.mlb.com/api/v1/standings"
GAME_LOG_URL = "https://statsapi.mlb.com/api/v1/people/{player_id}/stats"

_TIMEOUT = 30


def get_todays_games(on_date: date = None) -> dict:
    """Return {mlb_team_id: {"opponent": str, "opposingStarter": str, "isHome": bool}}.

    Teams absent from the result are not playing, which is the primary daily filter.
    """
    on_date = on_date or date.today()

    response = requests.get(
        SCHEDULE_URL,
        params={
            "sportId": 1,
            "date": on_date.strftime("%Y-%m-%d"),
            "gameType": "R",
            "hydrate": "probablePitcher",
        },
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    dates = response.json().get("dates", [])

    teams = {}
    if not dates:
        return teams

    for game in dates[0].get("games", []):
        matchup = game.get("teams", {})
        away = matchup.get("away", {})
        home = matchup.get("home", {})
        away_team = away.get("team", {})
        home_team = home.get("team", {})

        # Doubleheaders produce two entries per club; keep the first.
        teams.setdefault(
            away_team.get("id"),
            {
                "opponent": home_team.get("name", ""),
                "opposingStarter": home.get("probablePitcher", {}).get("fullName", "TBD"),
                "isHome": False,
            },
        )
        teams.setdefault(
            home_team.get("id"),
            {
                "opponent": away_team.get("name", ""),
                "opposingStarter": away.get("probablePitcher", {}).get("fullName", "TBD"),
                "isHome": True,
            },
        )

    return teams


def get_team_games_played(season: int = None) -> dict:
    """Return {mlb_team_id: games played so far this season}."""
    season = season or date.today().year

    response = requests.get(
        STANDINGS_URL,
        params={"leagueId": "103,104", "season": season, "standingsTypes": "regularSeason"},
        timeout=_TIMEOUT,
    )
    response.raise_for_status()

    games_played = {}
    for record in response.json().get("records", []):
        for team_record in record.get("teamRecords", []):
            team_id = team_record.get("team", {}).get("id")
            wins = team_record.get("wins") or 0
            losses = team_record.get("losses") or 0
            games_played[team_id] = wins + losses

    return games_played


def get_recent_team_schedule(days: int = 14, through: date = None) -> dict:
    """Return {mlb_team_id: sorted list of dates that club played} over a recent window.

    Used to convert "last pitched on 7/18" into "has had N team games of rest", which is
    what actually governs bullpen availability -- a team off day is not a rest day in the
    same sense as a game the pitcher sat out.
    """
    through = through or date.today()
    start = through - timedelta(days=days)

    response = requests.get(
        SCHEDULE_URL,
        params={
            "sportId": 1,
            "startDate": start.strftime("%Y-%m-%d"),
            "endDate": through.strftime("%Y-%m-%d"),
            "gameType": "R",
        },
        timeout=_TIMEOUT,
    )
    response.raise_for_status()

    schedule = {}
    for day in response.json().get("dates", []):
        game_date = date.fromisoformat(day["date"])
        for game in day.get("games", []):
            # Only completed games count as games the pitcher could have appeared in.
            if game.get("status", {}).get("detailedState") != "Final":
                continue
            for side in ("away", "home"):
                team_id = game.get("teams", {}).get(side, {}).get("team", {}).get("id")
                if team_id is not None:
                    schedule.setdefault(team_id, set()).add(game_date)

    return {team_id: sorted(dates) for team_id, dates in schedule.items()}


def get_pitcher_game_log(player_id: int, season: int = None) -> list:
    """Return one dict per appearance for a pitcher this season.

    Each entry carries the raw counting stats needed to compute fantasy points, plus the
    date and whether it was a start.
    """
    season = season or date.today().year

    try:
        response = requests.get(
            GAME_LOG_URL.format(player_id=player_id),
            params={
                "stats": "gameLog",
                "group": "pitching",
                "season": season,
                "gameType": "R",
            },
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        stat_groups = response.json().get("stats", [])
    except requests.exceptions.RequestException as exc:
        print(f"Game log fetch failed for {player_id}: {exc}")
        return []

    if not stat_groups:
        return []

    appearances = []
    for split in stat_groups[0].get("splits", []):
        stat = split.get("stat", {})
        appearances.append(
            {
                "playerId": player_id,
                "date": date.fromisoformat(split["date"]),
                "isStarter": (stat.get("gamesStarted") or 0) > 0,
                "pitchesThrown": stat.get("numberOfPitches") or 0,
                **{field: stat.get(field) or 0 for field in _COUNTING_STATS},
            }
        )

    return appearances


_COUNTING_STATS = (
    "outs",
    "earnedRuns",
    "wins",
    "losses",
    "saves",
    "blownSaves",
    "strikeOuts",
    "hits",
    "baseOnBalls",
    "hitBatsmen",
    "wildPitches",
    "balks",
    "pickoffs",
    "holds",
)


def get_pitcher_game_logs(player_ids, season: int = None, max_workers: int = 12) -> dict:
    """Fetch game logs for many pitchers concurrently. Returns {player_id: [appearances]}."""
    season = season or date.today().year

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = pool.map(lambda pid: (pid, get_pitcher_game_log(pid, season)), player_ids)
        return dict(results)
