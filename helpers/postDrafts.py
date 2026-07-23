"""Builds the X and Patreon post bodies from the selected picks.

The formats here are lifted verbatim from the tier logic that previously lived in
`templates/pick_pitchers.html`, so posts keep reading exactly as they always have. Moving
them server-side makes them testable and keeps one source of truth.

Patreon has no post-creation API, so both outputs are plain text the approval page renders
with a copy button next to a link to the composer.
"""

from datetime import date

# Tier 1 is the strongest recommendation.
TIER_NAMES = {
    1: "Flaming Hot Wings",
    2: "Spicy Wings",
    3: "Mild Wings",
    4: "Dry Rub Wings",
}

HASHTAGS = "#winthewire #fantasy #reliever #bulk"

X_CHARACTER_LIMIT = 280

# Two pitchers per tier. X names only the first of each; Patreon lists both.
PICKS_PER_TIER = 2


def tier_for(position: int) -> int:
    """Map a zero-based tap position onto a tier.

    Taps fill each tier in pairs -- 0,1 -> tier 1; 2,3 -> tier 2; and so on. Anything
    beyond the last tier stacks onto it rather than being dropped.

    `templates/pick_pitchers.html` mirrors this exactly; the two must stay in step, which
    `tests/test_scoring.py` asserts against the same table.
    """
    return min(position // PICKS_PER_TIER + 1, len(TIER_NAMES))

COMPOSE_URLS = {
    "x": "https://x.com/compose/post",
    "patreon": "https://www.patreon.com/posts/new",
}


def _short_date(on_date: date = None) -> str:
    on_date = on_date or date.today()
    return f"{on_date.month}/{on_date.day}"


def group_by_tier(picks) -> dict:
    """Return {tier: [playerName, ...]} ordered by rank within each tier."""
    tiers = {}
    for pick in sorted(picks, key=lambda p: (p.get("tier", 1), p.get("rank", 0))):
        tier = pick.get("tier", 1)
        if tier in TIER_NAMES:
            tiers.setdefault(tier, []).append(pick["playerName"])
    return tiers


def build_x_post(picks, on_date: date = None) -> str:
    """Headline post: the top player from each tier.

    Long names can push four tiers past X's limit, so lower tiers are dropped until it
    fits. Trimming from the bottom keeps the strongest recommendations.
    """
    tiers = group_by_tier(picks)

    lines = [
        f"{TIER_NAMES[tier]} -> {tiers[tier][0]}"
        for tier in sorted(tiers)
        if tiers[tier]
    ]

    def assemble(rows):
        return (
            f"Fantasy Baseball Free Agent Adds {_short_date(on_date)}:\n\n"
            + "\n".join(rows)
            + f"\n\n{HASHTAGS}"
        )

    post = assemble(lines)
    while len(post) > X_CHARACTER_LIMIT and len(lines) > 1:
        lines.pop()
        post = assemble(lines)

    return post


def build_patreon_post(picks, on_date: date = None) -> str:
    """Long form: every player, grouped under its tier heading."""
    tiers = group_by_tier(picks)

    blocks = [
        ">" + TIER_NAMES[tier] + "\n\n" + "\n\n".join(tiers[tier])
        for tier in sorted(tiers)
        if tiers[tier]
    ]

    return "\n\n".join(blocks)


def build_all(picks, on_date: date = None) -> dict:
    """Both post bodies plus the composer links, ready for the approval page."""
    return {
        "x": build_x_post(picks, on_date),
        "patreon": build_patreon_post(picks, on_date),
        "xComposeUrl": COMPOSE_URLS["x"],
        "patreonComposeUrl": COMPOSE_URLS["patreon"],
    }


def assign_default_tiers(picks) -> list:
    """Map an already-ranked list onto tiers 1-4, two players each.

    Used by the one-tap "post as ranked" path, where there is no manual tiering step. Takes
    the top `PICKS_PER_TIER * len(TIER_NAMES)` picks in the order they were ranked.
    """
    wanted = PICKS_PER_TIER * len(TIER_NAMES)
    return [
        {
            "playerName": pick["playerName"],
            "tier": tier_for(position),
            "rank": position + 1,
        }
        for position, pick in enumerate(picks[:wanted])
    ]


DEFAULT_PICK_COUNT = PICKS_PER_TIER * len(TIER_NAMES)
