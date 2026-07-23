"""Guards the duplicated tier logic in `templates/pick_pitchers.html`.

The page has to assign tiers client-side to render the badges as you tap, and the server
re-derives them when building the posts. That is the same rule written twice, so it can
drift. These tests read the constants straight out of the template and compare them to the
Python source of truth.
"""

import re
from pathlib import Path

import pytest

from helpers import postDrafts

TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "pick_pitchers.html"


@pytest.fixture(scope="module")
def template_text():
    return TEMPLATE.read_text()


def test_template_exists(template_text):
    assert "tierFor" in template_text


def test_picks_per_tier_matches_python(template_text):
    match = re.search(r"const PICKS_PER_TIER = (\d+);", template_text)
    assert match, "PICKS_PER_TIER not found in the template"
    assert int(match.group(1)) == postDrafts.PICKS_PER_TIER


def test_tier_names_match_python(template_text):
    match = re.search(r"const TIER_NAMES = \[(.*?)\];", template_text, re.S)
    assert match, "TIER_NAMES not found in the template"

    names = re.findall(r"'([^']+)'", match.group(1))
    expected = [postDrafts.TIER_NAMES[i] for i in sorted(postDrafts.TIER_NAMES)]
    assert names == expected


def test_template_tier_formula_matches_python(template_text):
    """Re-implement the template's formula and check it against `tier_for` for every tap."""
    match = re.search(
        r"function tierFor\(position\) \{\s*return Math\.min\("
        r"Math\.floor\(position / PICKS_PER_TIER\) \+ 1, TIER_NAMES\.length\);",
        template_text,
    )
    assert match, "tierFor no longer has the expected shape -- update this test with it"

    per_tier = postDrafts.PICKS_PER_TIER
    tiers = len(postDrafts.TIER_NAMES)

    for position in range(24):
        js_result = min(position // per_tier + 1, tiers)
        assert js_result == postDrafts.tier_for(position), f"drift at tap {position}"


def _player_card(template_text: str) -> str:
    card = re.search(
        r'<div class="player" data-index.*?>(.*?)\n        </div>', template_text, re.S
    )
    assert card, "player card markup not found"
    return card.group(1)


def test_picker_shows_espn_scoring_only(template_text):
    """The picker drives the public posts, so Hilltopper must not appear on it."""
    body = _player_card(template_text)
    assert "hilltopper" not in body.lower(), (
        "Hilltopper scoring belongs only in the email's league section"
    )


def test_headline_number_is_the_one_the_list_is_sorted_by(template_text):
    """The big number must be espnAdj, which is what `public_picks` sorts by.

    Whichever field orders the list has to be the one shown large, or the column reads as
    unsorted -- that mismatch is exactly what made an earlier version look broken.
    """
    body = _player_card(template_text)

    headline = re.search(r'<div class="proj">\{\{ (p\.\w+) \}\}</div>', body)
    assert headline, "headline number not found on the card"
    assert headline.group(1) == "p.espnAdj"

    # Expected value and the chance of pitching stay visible on the supporting line.
    assert "p.espnScore" in body
    assert "p.pitchChance" in body
