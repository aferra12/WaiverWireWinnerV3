"""Backtest the availability model against days that have already happened.

The question being tested is narrow and concrete: **of the relievers we ranked highest
this morning, how many actually pitched, and what did they score?**

State is rebuilt strictly from data available before the target date -- game logs are
truncated with `before=`, and rest is measured against the last outing prior to it. The
same `scoring.py` functions the production path calls are used unmodified.

Known limitation: FanGraphs `Role` is a *current* snapshot, so a pitcher who was promoted
to closer last week is backtested with today's role. The most recent date is therefore the
cleanest read; earlier dates carry mild look-ahead bias on the role feature alone.

    python3 -m helpers.backtest --days 5
    python3 -m helpers.backtest --date 2026-07-21
"""

import argparse
from datetime import date, timedelta

import numpy as np
import pandas as pd

from . import mlb, scoring
from .espn import get_ownership, normalize_name
from .fangraphs import get_depth_chart
from .pitcher_picks import EXCLUDED_ROLES, _consecutive_days_pitched

TOP_N = (10, 20)


def _appearance_on(game_log, target: date):
    """Return the relief appearance a pitcher made on `target`, or None."""
    for appearance in game_log:
        if appearance["date"] == target and not appearance["isStarter"]:
            return appearance
    return None


def usage_from_game_logs(game_logs: dict) -> pd.DataFrame:
    """Rebuild the outing history from MLB game logs rather than FanGraphs.

    FanGraphs only publishes six days of usage, which caps the backtest at six dates.
    The game logs carry the whole season and contain the same two fields rest depends on
    (date and pitch count), so deriving usage from them extends the backtest arbitrarily
    far back without changing what the model sees.
    """
    rows = [
        {
            "mlbamId": appearance["playerId"],
            "gameDate": appearance["date"],
            "pitches": appearance["pitchesThrown"],
        }
        for log in game_logs.values()
        for appearance in log
        if not appearance["isStarter"]
    ]
    return pd.DataFrame(rows, columns=["mlbamId", "gameDate", "pitches"])


def rank_as_of(target: date, pitchers, usage, game_logs, team_games, schedule, ownership,
               ownership_threshold: float, use_availability: bool = True,
               scorer=None) -> pd.DataFrame:
    """Rank candidates using only information available the morning of `target`.

    `use_availability=False` produces the naive baseline: season scoring average alone,
    ignoring rest and role entirely.
    """
    played_that_day = {
        team for team, dates in schedule.items() if target in dates
    }

    candidates = pitchers[
        pitchers["isActive"]
        & ~pitchers["isOnIl"]
        & ~pitchers["role"].isin(EXCLUDED_ROLES)
        & pitchers["mlbTeamId"].isin(played_that_day)
    ].copy()

    candidates["nameKey"] = candidates["playerName"].map(normalize_name)
    candidates = candidates.merge(
        ownership[["nameKey", "percentOwned"]], on="nameKey", how="left"
    )
    candidates = candidates[
        candidates["percentOwned"].isna()
        | (candidates["percentOwned"] < ownership_threshold)
    ]

    # Outings strictly before the target date.
    prior_usage = usage[usage["gameDate"] < target]
    last_outing = (
        prior_usage.sort_values("gameDate").groupby("mlbamId").last()[["gameDate", "pitches"]]
    )
    outings_by_pitcher = prior_usage.groupby("mlbamId")["gameDate"].apply(list).to_dict()

    rows = []
    for record in candidates.to_dict("records"):
        mlbam_id = record["mlbamId"]
        log = game_logs.get(mlbam_id, [])

        points = scoring.relief_appearance_points(log, before=target, scorer=scorer)
        hist_avg, hist_boom, appearances = scoring.summarize_points(points)

        # Appearance rate as of that morning, from the truncated log.
        team_total = team_games.get(record["mlbTeamId"])
        games_before = sum(1 for a in log if a["date"] < target)
        appearance_rate = None
        if games_before and team_total:
            elapsed = len([d for d in schedule.get(record["mlbTeamId"], []) if d < target])
            appearance_rate = min(1.0, games_before / elapsed) if elapsed else None

        last = last_outing.loc[mlbam_id] if mlbam_id in last_outing.index else None
        last_date = last["gameDate"] if last is not None else None
        last_pitches = last["pitches"] if last is not None else None

        rest = scoring.games_rest(last_date, schedule.get(record["mlbTeamId"], []), target)
        baseline = scoring.baseline_rest_from_rate(appearance_rate)
        streak = _consecutive_days_pitched(outings_by_pitcher.get(mlbam_id, []), target)

        # Six-day workload as of that morning, recomputed rather than reused.
        window_start = target - timedelta(days=6)
        recent_pitches = prior_usage[
            (prior_usage["mlbamId"] == mlbam_id) & (prior_usage["gameDate"] >= window_start)
        ]["pitches"].sum()

        availability = scoring.availability_score(
            appearance_rate, rest, baseline, last_pitches, streak, recent_pitches,
            record["role"],
        )

        rows.append(
            {
                **record,
                "histAvg": hist_avg,
                "histBoom": hist_boom,
                "appearances": appearances,
                "availability": availability if use_availability else 1.0,
                "gamesRest": rest,
            }
        )

    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame

    priors = scoring.role_priors(frame.to_dict("records"))
    frame["adjAvg"] = [
        scoring.shrink_to_role(r["histAvg"], r["appearances"], r["role"], priors)
        for r in frame.to_dict("records")
    ]
    frame["score"] = frame["availability"] * frame["adjAvg"]

    return frame.sort_values("score", ascending=False).reset_index(drop=True)


def evaluate(ranked: pd.DataFrame, game_logs, target: date, scorer=None) -> dict:
    """Score a ranked list against what actually happened."""
    scorer = scorer or scoring.hilltopper_points
    result = {}

    for n in TOP_N:
        top = ranked.head(n)
        actual = [
            _appearance_on(game_logs.get(pid, []), target) for pid in top["mlbamId"]
        ]
        pitched = [a for a in actual if a is not None]

        result[f"precision@{n}"] = len(pitched) / n if n else 0.0
        # Points over the whole top-N, counting a no-show as zero -- that is the real
        # cost of recommending someone who never took the mound.
        result[f"meanPts@{n}"] = float(
            np.mean([scorer(a) if a else 0.0 for a in actual])
        ) if n else 0.0

    return result


def run(days: int = 5, target_date: date = None, ownership_threshold: float = 7.5,
        system: str = "hilltopper"):
    """Backtest the most recent `days` dates, or a single `target_date`.

    `system` selects the scoring used both to rank and to measure -- "espn" validates the
    ordering behind the public posts, "hilltopper" the personal list.
    """
    scorer = scoring.SCORING_SYSTEMS[system]
    print(f"Scoring system: {system}")
    print("Fetching FanGraphs depth chart...")
    pitchers, usage = get_depth_chart()

    print("Fetching schedule, standings and ownership...")
    season = (target_date or date.today()).year
    team_games = mlb.get_team_games_played(season)
    # Reach back past the earliest target so rest is measurable on the first date tested.
    schedule_span = days + 20 if target_date is None else 30
    schedule = mlb.get_recent_team_schedule(days=schedule_span, through=date.today())
    ownership = get_ownership(season)

    eligible = pitchers[
        pitchers["isActive"]
        & ~pitchers["isOnIl"]
        & ~pitchers["role"].isin(EXCLUDED_ROLES)
    ]
    ids = eligible["mlbamId"].dropna().astype(int).tolist()

    print(f"Fetching game logs for {len(ids)} relievers (this is the slow part)...")
    game_logs = mlb.get_pitcher_game_logs(ids, season)
    total_appearances = sum(len(v) for v in game_logs.values())
    print(f"  {total_appearances} appearances loaded")

    # Season-long usage, so the backtest is not limited to FanGraphs' six-day window.
    usage = usage_from_game_logs(game_logs)

    if target_date:
        targets = [target_date]
    else:
        # Most recent dates with games, excluding today (incomplete).
        available = sorted({d for d in usage["gameDate"]}, reverse=True)
        targets = [d for d in available if d < date.today()][:days]

    print(f"\nBacktesting {len(targets)} date(s): {[str(d) for d in targets]}\n")

    rows = []
    for target in targets:
        model = rank_as_of(target, pitchers, usage, game_logs, team_games, schedule,
                           ownership, ownership_threshold, use_availability=True,
                           scorer=scorer)
        naive = rank_as_of(target, pitchers, usage, game_logs, team_games, schedule,
                           ownership, ownership_threshold, use_availability=False,
                           scorer=scorer)

        if model.empty:
            print(f"{target}: no candidates")
            continue

        model_result = evaluate(model, game_logs, target, scorer=scorer)
        naive_result = evaluate(naive, game_logs, target, scorer=scorer)

        rows.append({"date": target, "model": model_result, "naive": naive_result,
                     "candidates": len(model)})

        print(f"--- {target}  ({len(model)} candidates) ---")
        for n in TOP_N:
            print(
                f"  precision@{n:<3} model {model_result[f'precision@{n}']:.0%}"
                f"   naive {naive_result[f'precision@{n}']:.0%}"
                f"      meanPts@{n} model {model_result[f'meanPts@{n}']:6.2f}"
                f"   naive {naive_result[f'meanPts@{n}']:6.2f}"
            )

    if not rows:
        return

    print("\n=== Aggregate over", len(rows), "dates ===")
    for n in TOP_N:
        m_p = np.mean([r["model"][f"precision@{n}"] for r in rows])
        n_p = np.mean([r["naive"][f"precision@{n}"] for r in rows])
        m_v = np.mean([r["model"][f"meanPts@{n}"] for r in rows])
        n_v = np.mean([r["naive"][f"meanPts@{n}"] for r in rows])
        print(f"  precision@{n:<3} model {m_p:.1%}  naive {n_p:.1%}   "
              f"(lift {m_p - n_p:+.1%})")
        print(f"  meanPts@{n:<3}   model {m_v:6.2f}  naive {n_v:6.2f}   "
              f"(lift {m_v - n_v:+.2f})")

    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=5, help="How many recent dates to test")
    parser.add_argument("--date", type=str, default=None, help="Single date, YYYY-MM-DD")
    parser.add_argument("--ownership", type=float, default=7.5)
    parser.add_argument("--system", choices=sorted(scoring.SCORING_SYSTEMS), default="espn",
                        help="Scoring system to rank and measure with")
    args = parser.parse_args()

    target = date.fromisoformat(args.date) if args.date else None
    run(days=args.days, target_date=target, ownership_threshold=args.ownership,
        system=args.system)


if __name__ == "__main__":
    main()
