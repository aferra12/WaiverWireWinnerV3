"""Scoring model: how likely is a reliever to pitch today, and what is he worth if he does.

Pure functions only -- no network, no clock. Everything is passed in, so `backtest.py` can
replay any prior date through exactly the code that runs in production.

Final ranking is `availability x adjustedAvgPoints`. The two halves are deliberately
separate: availability answers "will he appear", expected points answers "what does an
appearance yield". Multiplying them ranks by expected contribution.
"""

from datetime import date

import numpy as np

# Point values, kept numerically identical to the retired BigQuery pipeline so results
# stay comparable to the old system.
PITCHING_POINTS = {
    "outs": 1,
    "earnedRuns": -3,
    "wins": 6,
    "losses": -3,
    "saves": 17,
    "blownSaves": -4,
    "strikeOuts": 5,
    "hits": -1,
    "baseOnBalls": -1,
    "hitBatsmen": -1,
    "wildPitches": -1,
    "balks": -7,
    "pickoffs": 7,
    "holds": 8,
}

QUALITY_START_POINTS = 9

# ESPN standard points scoring, used by most public leagues -- this is what the X and
# Patreon audiences play under.
#
# ESPN lists innings pitched as 3 points per inning and credits partial innings, which is
# exactly 1 point per out. Scoring `outs` directly avoids the float error you get from
# reconstructing thirds of an inning. Note ESPN standard has no blown-save, hit-by-pitch,
# wild-pitch, balk, pickoff or quality-start component.
ESPN_PITCHING_POINTS = {
    "outs": 1,          # 3 points per IP
    "hits": -1,
    "earnedRuns": -2,
    "holds": 2,
    "baseOnBalls": -1,
    "strikeOuts": 1,
    "wins": 5,
    "losses": -2,
    "saves": 5,
}

# Appearances of real history before a pitcher's own average outweighs his role's prior.
ROLE_PRIOR_WEIGHT = 8

HIGH_PITCH_COUNT = 25


def is_missing(value) -> bool:
    """True for None and NaN alike.

    Values arrive from pandas, where a missing number in a float column is NaN rather
    than None -- and NaN is truthy and compares False against everything, so a plain
    `is None` check silently lets it through and poisons the arithmetic downstream.
    """
    if value is None:
        return True
    try:
        return bool(np.isnan(value))
    except (TypeError, ValueError):
        return False


def hilltopper_points(stat: dict) -> float:
    """Fantasy points for one appearance under the Hilltopper league's scoring."""
    points = sum(value * (stat.get(field) or 0) for field, value in PITCHING_POINTS.items())

    # Quality start: 6+ innings and no more than 3 earned runs.
    if (stat.get("outs") or 0) >= 18 and (stat.get("earnedRuns") or 0) <= 3:
        points += QUALITY_START_POINTS

    return float(points)


def espn_points(stat: dict) -> float:
    """Fantasy points for one appearance under ESPN standard scoring."""
    return float(
        sum(value * (stat.get(field) or 0) for field, value in ESPN_PITCHING_POINTS.items())
    )


# The two scoring systems the app reports side by side.
SCORING_SYSTEMS = {
    "hilltopper": hilltopper_points,
    "espn": espn_points,
}


def relief_appearance_points(game_log: list, before: date = None, scorer=None) -> list:
    """Fantasy points for each relief appearance, optionally truncated to before a date.

    `before` is what keeps the backtest honest -- it excludes the day being predicted and
    everything after it. `scorer` selects the scoring system, defaulting to Hilltopper.
    """
    scorer = scorer or hilltopper_points

    points = []
    for appearance in game_log:
        if appearance.get("isStarter"):
            continue
        if before is not None and appearance["date"] >= before:
            continue
        points.append(scorer(appearance))
    return points


def games_rest(last_appearance: date, team_game_dates, as_of: date) -> int:
    """Count team games played since the pitcher last appeared.

    Team games, not calendar days: an off day does not rest a bullpen the way a game the
    pitcher sat out does. Returns None when there is no appearance on record.
    """
    if last_appearance is None:
        return None

    return sum(1 for game in team_game_dates if last_appearance < game < as_of)


def rest_factor(rest, baseline_rest, pitches_last_outing, consecutive_days) -> float:
    """Multiplier on availability from the pitcher's current rest state."""
    # Three straight days is close to disqualifying regardless of anything else.
    if consecutive_days >= 3:
        return 0.05

    if is_missing(rest):
        # No appearance on record -- recently promoted or returning. Neither favour nor bury.
        return 0.8

    if rest == 0:
        pitches = 0 if is_missing(pitches_last_outing) else pitches_last_outing
        return 0.15 if pitches >= HIGH_PITCH_COUNT else 0.5

    if not is_missing(baseline_rest) and rest >= baseline_rest:
        # Due, but the boost is capped: extra idle days stop adding signal quickly.
        return min(1.3, 1.0 + 0.15 * (rest - baseline_rest + 1))

    return 0.85


def availability_score(
    appearance_rate,
    rest,
    baseline_rest,
    pitches_last_outing,
    consecutive_days,
    recent_pitches=None,
    role=None,
) -> float:
    """Probability-like score in [0, 1] that this reliever pitches today.

    Anchored on his season appearance rate (games / team games), then adjusted for rest.

    `recent_pitches` and `role` are accepted but deliberately unused. Both were tried as
    additional multipliers and both were measured over a 30-day backtest as no better
    than noise:

      - A six-day workload penalty made results marginally *worse* (pts@20 5.11 vs 5.18
        without it). Consecutive-day usage, which `rest_factor` already handles, carries
        the signal it was meant to add.
      - A role multiplier was inert. Making it stronger, weaker, or even inverting it
        left the results unchanged -- a feature whose inversion costs nothing is not
        carrying information. Role genuinely correlates with pitching (co-closers appear
        43% of the time, middle relievers 32%), but appearance rate already measures that
        directly, so role is redundant here rather than wrong.

    Role still does real work on the scoring side, in `shrink_to_role`.
    """
    base = 0.35 if (is_missing(appearance_rate) or not appearance_rate) else appearance_rate

    score = base * rest_factor(rest, baseline_rest, pitches_last_outing, consecutive_days)

    return float(min(1.0, max(0.0, score)))


def baseline_rest_from_rate(appearance_rate):
    """Typical games of rest implied by an appearance rate.

    Replaces the old `PERCENTILE_CONT(gamesRest, 0.6)` window function: a reliever who
    appears in 40% of team games is, on average, resting 1.5 games between outings.
    """
    if is_missing(appearance_rate) or not appearance_rate or appearance_rate <= 0:
        return None
    return max(0.0, (1.0 / appearance_rate) - 1.0)


def role_priors(rows, field: str = "histAvg") -> dict:
    """Mean per-appearance points by role, computed from the candidate pool itself.

    Self-calibrating: no stored constants to drift, and it reflects the current season's
    scoring environment. Rows need `role` and `field` (None when no history). `field`
    selects which scoring system's average to build priors from.
    """
    totals = {}
    for row in rows:
        if is_missing(row.get(field)):
            continue
        totals.setdefault(row["role"], []).append(row[field])

    priors = {role: float(np.mean(values)) for role, values in totals.items()}

    observed = [v for values in totals.values() for v in values]
    priors["_overall"] = float(np.mean(observed)) if observed else 0.0

    return priors


def shrink_to_role(hist_avg, sample_size, role, priors) -> float:
    """Blend a pitcher's own average toward his role's prior.

    Handles thin samples and role changes: a middle reliever just promoted to closer gets
    pulled toward the closer prior instead of being judged only on his mop-up history.
    """
    prior = priors.get(role, priors.get("_overall", 0.0))

    if is_missing(hist_avg) or is_missing(sample_size) or not sample_size:
        return float(prior)

    weight = sample_size + ROLE_PRIOR_WEIGHT
    return float((sample_size * hist_avg + ROLE_PRIOR_WEIGHT * prior) / weight)


def summarize_points(points: list):
    """Return (mean, 75th percentile, n) for a list of per-appearance point totals."""
    if not points:
        return None, None, 0
    return float(np.mean(points)), float(np.percentile(points, 75)), len(points)
