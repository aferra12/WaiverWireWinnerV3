"""Tests for the scoring math and post formatting -- the parts with no I/O."""

from datetime import date

import numpy as np
import pytest

from helpers import postDrafts, scoring


# --- fantasy points -------------------------------------------------------------------

def test_hilltopper_points_matches_legacy_formula():
    """A one-inning save with two strikeouts, using the retired pipeline's weights."""
    stat = {
        "outs": 3, "earnedRuns": 0, "wins": 0, "losses": 0, "saves": 1,
        "blownSaves": 0, "strikeOuts": 2, "hits": 0, "baseOnBalls": 0,
        "hitBatsmen": 0, "wildPitches": 0, "balks": 0, "pickoffs": 0, "holds": 0,
    }
    # 3 outs + 17 save + 10 strikeouts
    assert scoring.hilltopper_points(stat) == 30


def test_hilltopper_points_awards_quality_start():
    stat = {"outs": 18, "earnedRuns": 3, "strikeOuts": 0}
    # 18 outs - 9 earned runs + 9 quality start
    assert scoring.hilltopper_points(stat) == 18


def test_hilltopper_points_withholds_quality_start_on_four_earned_runs():
    assert scoring.hilltopper_points({"outs": 18, "earnedRuns": 4}) == 6


def test_hilltopper_points_handles_missing_fields():
    assert scoring.hilltopper_points({}) == 0


# --- ESPN standard scoring ------------------------------------------------------------

def test_espn_points_one_inning_save():
    """1 IP, 1 hit, 2 K, a save: 3 (IP) - 1 (H) + 2 (K) + 5 (SV)."""
    stat = {
        "outs": 3, "hits": 1, "earnedRuns": 0, "holds": 0,
        "baseOnBalls": 0, "strikeOuts": 2, "wins": 0, "losses": 0, "saves": 1,
    }
    assert scoring.espn_points(stat) == 9


def test_espn_points_credits_partial_innings_per_out():
    """ESPN lists 3 points per IP and credits partials, i.e. 1 point per out."""
    assert scoring.espn_points({"outs": 1}) == 1
    assert scoring.espn_points({"outs": 2}) == 2
    assert scoring.espn_points({"outs": 3}) == 3


def test_espn_points_hold_and_win_values():
    assert scoring.espn_points({"holds": 1}) == 2
    assert scoring.espn_points({"wins": 1}) == 5
    assert scoring.espn_points({"losses": 1}) == -2
    assert scoring.espn_points({"earnedRuns": 1}) == -2
    assert scoring.espn_points({"baseOnBalls": 1}) == -1


def test_espn_points_ignores_categories_outside_standard_scoring():
    """Blown saves, HBP, wild pitches, balks and pickoffs are not ESPN standard."""
    for field in ("blownSaves", "hitBatsmen", "wildPitches", "balks", "pickoffs"):
        assert scoring.espn_points({field: 3}) == 0


def test_espn_points_has_no_quality_start_bonus():
    # Hilltopper adds 9 for a quality start; ESPN standard does not.
    stat = {"outs": 18, "earnedRuns": 3}
    assert scoring.espn_points(stat) == 18 - 6
    assert scoring.hilltopper_points(stat) == 18


def test_the_two_systems_disagree_on_strikeout_heavy_outings():
    """A strikeout is worth 5 under Hilltopper and 1 under ESPN standard."""
    punchouts = {"outs": 3, "strikeOuts": 3}
    assert scoring.hilltopper_points(punchouts) == 18
    assert scoring.espn_points(punchouts) == 6


def test_scoring_systems_registry_exposes_both():
    assert set(scoring.SCORING_SYSTEMS) == {"hilltopper", "espn"}


def test_relief_appearance_points_honours_the_chosen_scorer():
    log = [{"date": date(2026, 7, 18), "isStarter": False, "outs": 3, "strikeOuts": 3}]
    assert scoring.relief_appearance_points(log, scorer=scoring.espn_points) == [6.0]
    assert scoring.relief_appearance_points(log, scorer=scoring.hilltopper_points) == [18.0]


# --- missing-value handling -----------------------------------------------------------

@pytest.mark.parametrize("value", [None, float("nan"), np.nan])
def test_is_missing_catches_none_and_nan(value):
    assert scoring.is_missing(value)


@pytest.mark.parametrize("value", [0, 0.0, 1, -3.5, "text"])
def test_is_missing_passes_real_values(value):
    assert not scoring.is_missing(value)


def test_availability_survives_nan_inputs():
    """NaN reaching the model must not produce a NaN score.

    pandas turns missing numbers into NaN, which is truthy and compares False against
    everything -- exactly the shape of bug that silently poisoned early runs.
    """
    score = scoring.availability_score(np.nan, np.nan, np.nan, np.nan, 0)
    assert 0.0 <= score <= 1.0
    assert not np.isnan(score)


# --- rest -----------------------------------------------------------------------------

def test_games_rest_counts_only_intervening_team_games():
    schedule = [date(2026, 7, 18), date(2026, 7, 19), date(2026, 7, 21)]
    # Pitched the 18th; the 19th and 21st fall between then and the 22nd.
    assert scoring.games_rest(date(2026, 7, 18), schedule, date(2026, 7, 22)) == 2


def test_games_rest_is_zero_the_day_after_pitching():
    schedule = [date(2026, 7, 20), date(2026, 7, 21)]
    assert scoring.games_rest(date(2026, 7, 21), schedule, date(2026, 7, 22)) == 0


def test_games_rest_ignores_off_days():
    """A calendar gap with no games is not rest -- only team games count."""
    schedule = [date(2026, 7, 15), date(2026, 7, 21)]
    assert scoring.games_rest(date(2026, 7, 15), schedule, date(2026, 7, 22)) == 1


def test_games_rest_without_an_appearance():
    assert scoring.games_rest(None, [date(2026, 7, 21)], date(2026, 7, 22)) is None


def test_heavy_back_to_back_is_penalised_more_than_light():
    heavy = scoring.rest_factor(0, 1.0, 30, 1)
    light = scoring.rest_factor(0, 1.0, 10, 1)
    assert heavy < light


def test_three_straight_days_is_near_disqualifying():
    assert scoring.rest_factor(0, 1.0, 5, 3) < 0.1


def test_rested_pitcher_is_boosted_but_capped():
    assert scoring.rest_factor(8, 1.0, 10, 0) == pytest.approx(1.3)


def test_baseline_rest_inverts_appearance_rate():
    # Appearing in half of a club's games implies roughly one game of rest between outings.
    assert scoring.baseline_rest_from_rate(0.5) == pytest.approx(1.0)
    assert scoring.baseline_rest_from_rate(0) is None
    assert scoring.baseline_rest_from_rate(np.nan) is None


# --- shrinkage ------------------------------------------------------------------------

def test_role_priors_average_per_role():
    rows = [
        {"role": "Closer", "histAvg": 10.0},
        {"role": "Closer", "histAvg": 20.0},
        {"role": "Setup Man", "histAvg": 6.0},
        {"role": "Setup Man", "histAvg": None},
    ]
    priors = scoring.role_priors(rows)
    assert priors["Closer"] == pytest.approx(15.0)
    assert priors["Setup Man"] == pytest.approx(6.0)


def test_role_priors_can_target_a_named_scoring_field():
    rows = [
        {"role": "Closer", "espnAvg": 4.0},
        {"role": "Closer", "espnAvg": 6.0},
    ]
    assert scoring.role_priors(rows, field="espnAvg")["Closer"] == pytest.approx(5.0)


def test_thin_sample_is_pulled_toward_role_prior():
    priors = {"Closer": 12.0, "_overall": 8.0}
    # One appearance at 30 points should not be taken at face value.
    shrunk = scoring.shrink_to_role(30.0, 1, "Closer", priors)
    assert 12.0 < shrunk < 30.0
    assert shrunk == pytest.approx((1 * 30.0 + 8 * 12.0) / 9)


def test_large_sample_dominates_the_prior():
    priors = {"Closer": 12.0, "_overall": 8.0}
    shrunk = scoring.shrink_to_role(20.0, 200, "Closer", priors)
    assert shrunk == pytest.approx(20.0, abs=0.4)


def test_no_history_falls_back_to_the_prior():
    priors = {"Closer": 12.0, "_overall": 8.0}
    assert scoring.shrink_to_role(None, 0, "Closer", priors) == 12.0
    assert scoring.shrink_to_role(np.nan, 0, "Middle Reliever", priors) == 8.0


# --- appearance filtering -------------------------------------------------------------

def test_relief_points_exclude_starts_and_respect_the_cutoff():
    log = [
        {"date": date(2026, 7, 18), "isStarter": False, "outs": 3},
        {"date": date(2026, 7, 19), "isStarter": True, "outs": 18},   # a start
        {"date": date(2026, 7, 22), "isStarter": False, "outs": 3},   # on/after cutoff
    ]
    points = scoring.relief_appearance_points(log, before=date(2026, 7, 22))
    assert points == [3.0]


def test_summarize_points_on_empty_history():
    assert scoring.summarize_points([]) == (None, None, 0)


# --- post formatting ------------------------------------------------------------------

def _picks():
    return [
        {"playerName": "Paul Sewald", "tier": 1, "rank": 1},
        {"playerName": "Kevin Ginkel", "tier": 2, "rank": 1},
        {"playerName": "David Bednar", "tier": 2, "rank": 2},
    ]


# --- tap position -> tier ------------------------------------------------------------

def test_taps_fill_each_tier_in_pairs():
    """Two pitchers per tier: taps 1-2 are Flaming Hot, 3-4 Spicy, and so on."""
    assert [postDrafts.tier_for(p) for p in range(8)] == [1, 1, 2, 2, 3, 3, 4, 4]


def test_taps_past_the_last_tier_stack_onto_it():
    assert postDrafts.tier_for(8) == 4
    assert postDrafts.tier_for(12) == 4


def test_default_pick_count_is_two_per_tier():
    assert postDrafts.DEFAULT_PICK_COUNT == 8
    assert postDrafts.PICKS_PER_TIER == 2


def test_default_tiers_assign_two_players_per_tier():
    ranked = [{"playerName": f"P{i}"} for i in range(12)]
    tiers = postDrafts.assign_default_tiers(ranked)

    assert len(tiers) == 8
    assert [t["tier"] for t in tiers] == [1, 1, 2, 2, 3, 3, 4, 4]
    # Order is preserved, so the stronger of each pair is the one X names.
    assert [t["playerName"] for t in tiers[:2]] == ["P0", "P1"]


def test_default_tiers_handle_a_short_list():
    tiers = postDrafts.assign_default_tiers([{"playerName": "Only"}])
    assert tiers == [{"playerName": "Only", "tier": 1, "rank": 1}]


def test_x_post_shows_one_player_per_tier():
    post = postDrafts.build_x_post(_picks(), date(2026, 7, 22))
    assert "Fantasy Baseball Free Agent Adds 7/22:" in post
    assert "Flaming Hot Wings -> Paul Sewald" in post
    assert "Spicy Wings -> Kevin Ginkel" in post
    # The second player in a tier is Patreon-only.
    assert "David Bednar" not in post
    assert postDrafts.HASHTAGS in post


def test_patreon_post_lists_every_player_under_its_tier():
    post = postDrafts.build_patreon_post(_picks(), date(2026, 7, 22))
    assert ">Flaming Hot Wings" in post
    assert ">Spicy Wings" in post
    for name in ("Paul Sewald", "Kevin Ginkel", "David Bednar"):
        assert name in post


def test_x_post_stays_within_the_character_limit():
    picks = [
        {"playerName": "A Considerably Overlong Pitcher Name", "tier": t, "rank": 1}
        for t in range(1, 5)
    ]
    assert len(postDrafts.build_x_post(picks, date(2026, 7, 22))) <= 280
