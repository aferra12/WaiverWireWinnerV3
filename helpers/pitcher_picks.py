"""Builds the daily ranked list of waiver-wire relief pitchers.

Pipeline:
  FanGraphs depth chart  -> candidates, role, IL/active status, last-6-day usage
  MLB schedule           -> who plays today, team games for rest counting
  MLB standings          -> team games played (appearance-rate denominator)
  ESPN                   -> ownership
  MLB game logs          -> per-appearance fantasy points for the shortlist

Run as a dry run with no side effects:
    python3 -m helpers.pitcher_picks
"""

import os
from datetime import date, timedelta

import pandas as pd

from . import mlb, scoring
from .espn import (
    LeagueAccessError,
    get_league_free_agents,
    get_ownership,
    normalize_name,
)
from .fangraphs import get_depth_chart

DEFAULT_OWNERSHIP_THRESHOLD = 7.5

# Game logs are the only per-player fetch, so the field is trimmed first.
GAME_LOG_SHORTLIST = 60

EXCLUDED_ROLES = {"Long Reliever"}


def _consecutive_days_pitched(outing_dates, as_of: date) -> int:
    """How many days in an unbroken run up to yesterday the pitcher worked."""
    streak = 0
    cursor = as_of
    dates = set(outing_dates)
    while True:
        cursor = cursor - timedelta(days=1)
        if cursor in dates:
            streak += 1
        else:
            break
    return streak


def _flag_for(row) -> str:
    """Short caveat shown alongside a pick, or "" when nothing stands out."""
    statuses = row.get("recentStatuses") or []

    if any(s in ("AAA", "AA") for s in statuses):
        return "Just called up -- limited MLB usage history"
    if "IL" in statuses:
        return "Just off the IL -- usage may be limited"
    if not scoring.is_missing(row.get("tags")) and row.get("tags"):
        return str(row["tags"])
    if not scoring.is_missing(row.get("gamesRest")) and row["gamesRest"] > 5:
        return "Unusually idle -- check for injury news"
    if not row.get("appearances"):
        return "No relief appearances yet this season"
    return ""


def build_picks(
    as_of: date = None,
    ownership_threshold: float = None,
) -> pd.DataFrame:
    """Score today's candidate relievers under both scoring systems.

    Returns the full scored pool ordered by ESPN standard points. Use `public_picks` for
    the list behind the posts and `league_picks` for the personal one.
    """
    as_of = as_of or date.today()
    if ownership_threshold is None:
        # `or` rather than a .get default: a blank .env entry yields "", not None.
        ownership_threshold = float(
            os.environ.get("OWNERSHIP_THRESHOLD") or DEFAULT_OWNERSHIP_THRESHOLD
        )

    print("Fetching FanGraphs closer depth chart...")
    pitchers, usage = get_depth_chart()
    print(f"  {len(pitchers)} bullpen arms, {len(usage)} outings in the last 6 days")

    print("Fetching MLB schedule and standings...")
    playing_today = mlb.get_todays_games(as_of)
    if not playing_today:
        print("No games scheduled today.")
        return pd.DataFrame()

    team_games_played = mlb.get_team_games_played(as_of.year)
    recent_schedule = mlb.get_recent_team_schedule(days=14, through=as_of)
    print(f"  {len(playing_today)} teams playing")

    # --- Cheap filters, before any per-player work -------------------------------------
    candidates = pitchers[
        pitchers["isActive"]
        & ~pitchers["isOnIl"]
        & ~pitchers["role"].isin(EXCLUDED_ROLES)
        & pitchers["mlbTeamId"].isin(playing_today.keys())
    ].copy()
    print(f"  {len(candidates)} active, non-IL arms on teams playing today")

    print("Fetching ESPN ownership...")
    ownership = get_ownership(as_of.year)
    candidates["nameKey"] = candidates["playerName"].map(normalize_name)
    candidates = candidates.merge(
        ownership[["nameKey", "percentOwned"]], on="nameKey", how="left"
    )

    # Free agency in the personal league is a separate, stricter truth: a pitcher rostered
    # 30% globally can still be unowned in a shallow league, so this pool is kept wider
    # than the public one rather than being a subset of it.
    try:
        league_free_agents = get_league_free_agents(as_of.year)
    except LeagueAccessError as exc:
        print(f"  {exc}")
        league_free_agents = set()

    candidates["isLeagueFreeAgent"] = candidates["nameKey"].isin(league_free_agents)

    # Unmatched names keep a null ownership and are retained -- an arm ESPN has not
    # listed is almost certainly unowned, which is exactly what we are looking for.
    widely_available = (
        candidates["percentOwned"].isna()
        | (candidates["percentOwned"] < ownership_threshold)
    )
    candidates["isWidelyAvailable"] = widely_available

    candidates = candidates[widely_available | candidates["isLeagueFreeAgent"]].copy()
    print(
        f"  {int(candidates['isWidelyAvailable'].sum())} under {ownership_threshold}% owned, "
        f"{int(candidates['isLeagueFreeAgent'].sum())} free in your league"
    )

    if candidates.empty:
        return pd.DataFrame()

    # --- Rest and availability ---------------------------------------------------------
    last_outing = (
        usage.sort_values("gameDate").groupby("mlbamId").last()[["gameDate", "pitches"]]
    )
    outings_by_pitcher = usage.groupby("mlbamId")["gameDate"].apply(list).to_dict()

    rows = []
    for record in candidates.to_dict("records"):
        mlbam_id = record["mlbamId"]
        team_id = record["mlbTeamId"]
        team_game_dates = recent_schedule.get(team_id, [])

        last = last_outing.loc[mlbam_id] if mlbam_id in last_outing.index else None
        last_date = last["gameDate"] if last is not None else None
        last_pitches = last["pitches"] if last is not None else None

        team_total_games = team_games_played.get(team_id)
        appearance_rate = None
        games = record.get("games")
        if not scoring.is_missing(games) and games and team_total_games:
            appearance_rate = min(1.0, games / team_total_games)

        rest = scoring.games_rest(last_date, team_game_dates, as_of)
        baseline = scoring.baseline_rest_from_rate(appearance_rate)
        streak = _consecutive_days_pitched(outings_by_pitcher.get(mlbam_id, []), as_of)

        record.update(
            {
                "lastOuting": last_date,
                "lastOutingPitches": last_pitches,
                "gamesRest": rest,
                "baselineRest": baseline,
                "appearanceRate": appearance_rate,
                "consecutiveDays": streak,
                "availability": scoring.availability_score(
                    appearance_rate,
                    rest,
                    baseline,
                    last_pitches,
                    streak,
                    record.get("recentPitches"),
                    record["role"],
                ),
                "opponent": playing_today[team_id]["opponent"],
            }
        )
        rows.append(record)

    frame = pd.DataFrame(rows)

    # --- Score the shortlist ------------------------------------------------------------
    # Pre-rank on availability alone so the expensive game-log fetch is bounded.
    shortlist = frame.nlargest(min(GAME_LOG_SHORTLIST, len(frame)), "availability")
    print(f"Fetching game logs for {len(shortlist)} pitchers...")
    game_logs = mlb.get_pitcher_game_logs(shortlist["mlbamId"].tolist(), as_of.year)

    shortlist = shortlist.copy()

    # Score every appearance under both systems. The public posts serve standard-scoring
    # leagues; the personal list uses the Hilltopper weights.
    for system, scorer in scoring.SCORING_SYSTEMS.items():
        history = {
            mlbam_id: scoring.summarize_points(
                scoring.relief_appearance_points(log, before=as_of, scorer=scorer)
            )
            for mlbam_id, log in game_logs.items()
        }

        avg_field = f"{system}Avg"
        boom_field = f"{system}Boom"

        shortlist[avg_field] = shortlist["mlbamId"].map(
            lambda i: history.get(i, (None, None, 0))[0]
        )
        shortlist[boom_field] = shortlist["mlbamId"].map(
            lambda i: history.get(i, (None, None, 0))[1]
        )
        shortlist["appearances"] = shortlist["mlbamId"].map(
            lambda i: history.get(i, (None, None, 0))[2]
        )

        priors = scoring.role_priors(shortlist.to_dict("records"), field=avg_field)
        shortlist[f"{system}Adj"] = [
            scoring.shrink_to_role(row[avg_field], row["appearances"], row["role"], priors)
            for row in shortlist.to_dict("records")
        ]
        shortlist[f"{system}Score"] = shortlist["availability"] * shortlist[f"{system}Adj"]

    shortlist["flag"] = [_flag_for(row) for row in shortlist.to_dict("records")]

    # The full scored pool, ordered for the posts. Callers slice it -- the two lists are
    # ranked by different scoring systems, so neither may be derived from the other's
    # truncated output.
    return (
        shortlist.sort_values("espnScore", ascending=False)
        .reset_index(drop=True)
    )


def public_picks(picks: pd.DataFrame, limit: int = 25) -> pd.DataFrame:
    """The list behind the X and Patreon posts: widely available, ESPN standard scoring.

    Ordered by projected points per appearance rather than by expected value. Expected
    value (points x availability) ranks better on its own -- over a 30-day backtest it
    held precision@20 of 50% against 35% for points alone. Here the ordering only seeds a
    manual selection: eight arms are picked by hand with both the expected value and the
    chance of pitching shown on every row, so that judgement is applied per player instead
    of being baked into the sort.
    """
    if picks is None or picks.empty:
        return pd.DataFrame()

    available = picks[picks["isWidelyAvailable"]] if "isWidelyAvailable" in picks else picks
    return (
        available.sort_values("espnAdj", ascending=False)
        .head(limit)
        .reset_index(drop=True)
    )


def league_picks(picks: pd.DataFrame, limit: int = 10) -> pd.DataFrame:
    """The personal list: free agents in the configured league, by Hilltopper points.

    Ranked over the whole scored pool, not the public list -- an arm buried in the ESPN
    ordering can still be the best Hilltopper play available. Sorted by projected points
    to match the public list; expected value and the chance of pitching sit alongside.
    """
    if picks is None or picks.empty or "isLeagueFreeAgent" not in picks.columns:
        return pd.DataFrame()

    available = picks[picks["isLeagueFreeAgent"]]
    if available.empty:
        return pd.DataFrame()

    return (
        available.sort_values("hilltopperAdj", ascending=False)
        .head(limit)
        .reset_index(drop=True)
    )


DISPLAY_COLUMNS = [
    "playerName",
    "teamAbbrev",
    "role",
    "gamesRest",
    "percentOwned",
    "availability",
    "espnAdj",
    "espnBoom",
    "espnScore",
    "hilltopperAdj",
    "hilltopperBoom",
    "hilltopperScore",
    "isLeagueFreeAgent",
    "opponent",
    "flag",
]

_ROUNDED = (
    "availability", "espnAdj", "espnBoom", "espnScore",
    "hilltopperAdj", "hilltopperBoom", "hilltopperScore",
)


def _print_table(frame, title):
    print(f"\n{title}")
    display = frame[[c for c in DISPLAY_COLUMNS if c in frame.columns]].copy()
    for column in _ROUNDED:
        if column in display:
            display[column] = display[column].round(2)
    print(display.to_string(index=True))


def main():
    picks = build_picks()
    if picks.empty:
        print("No picks today.")
        return

    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 60)

    _print_table(
        public_picks(picks),
        "=== PUBLIC PICKS (ESPN standard scoring — drives X/Patreon) ===",
    )

    mine = league_picks(picks)
    if mine.empty:
        print(
            "\n=== YOUR LEAGUE (Hilltopper scoring) ===\n"
            "No league free agents found. Set ESPN_LEAGUE_ID, ESPN_S2 and ESPN_SWID."
        )
    else:
        _print_table(mine, "=== YOUR LEAGUE (Hilltopper scoring — free agents only) ===")


if __name__ == "__main__":
    main()
