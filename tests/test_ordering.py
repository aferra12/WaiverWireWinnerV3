"""The displayed number and the sort order must not drift apart.

An earlier version sorted by expected value while showing projected points, so the column
read as unsorted. These tests pin the sort field for each list.
"""

import pandas as pd
import pytest

from helpers.pitcher_picks import league_picks, public_picks


def _frame():
    """A pool where the two orderings genuinely disagree."""
    return pd.DataFrame(
        [
            # High projection, unlikely to pitch -- ranks first by points, not by value.
            {"playerName": "Rested Ace", "espnAdj": 5.0, "espnScore": 1.0,
             "hilltopperAdj": 9.0, "hilltopperScore": 1.8, "availability": 0.20,
             "isWidelyAvailable": True, "isLeagueFreeAgent": True},
            # Modest projection, very likely to pitch.
            {"playerName": "Workhorse", "espnAdj": 3.0, "espnScore": 1.8,
             "hilltopperAdj": 7.0, "hilltopperScore": 4.2, "availability": 0.60,
             "isWidelyAvailable": True, "isLeagueFreeAgent": True},
            {"playerName": "Middle", "espnAdj": 4.0, "espnScore": 1.4,
             "hilltopperAdj": 8.0, "hilltopperScore": 2.8, "availability": 0.35,
             "isWidelyAvailable": True, "isLeagueFreeAgent": False},
        ]
    )


def test_public_picks_sort_by_projected_points():
    ordered = public_picks(_frame())
    assert list(ordered["playerName"]) == ["Rested Ace", "Middle", "Workhorse"]


def test_public_picks_projected_column_is_descending():
    values = list(public_picks(_frame())["espnAdj"])
    assert values == sorted(values, reverse=True)


def test_public_picks_excludes_players_above_the_ownership_cut():
    frame = _frame()
    frame.loc[frame["playerName"] == "Middle", "isWidelyAvailable"] = False
    assert "Middle" not in list(public_picks(frame)["playerName"])


def test_league_picks_sort_by_projected_hilltopper_points():
    ordered = league_picks(_frame())
    assert list(ordered["playerName"]) == ["Rested Ace", "Workhorse"]
    values = list(ordered["hilltopperAdj"])
    assert values == sorted(values, reverse=True)


def test_league_picks_only_include_league_free_agents():
    assert "Middle" not in list(league_picks(_frame())["playerName"])


def test_league_picks_are_not_limited_to_the_public_list():
    """A name buried in the ESPN order can still lead the Hilltopper list."""
    frame = _frame()
    frame.loc[frame["playerName"] == "Workhorse", "hilltopperAdj"] = 99.0
    assert list(league_picks(frame)["playerName"])[0] == "Workhorse"


@pytest.mark.parametrize("fn", [public_picks, league_picks])
def test_empty_input_is_handled(fn):
    assert fn(pd.DataFrame()).empty
    assert fn(None).empty
