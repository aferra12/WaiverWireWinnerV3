import hmac
import json
import os
from datetime import date

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from helpers import postDrafts
from helpers.emailPicks import send_email
from helpers.pitcher_picks import build_picks, league_picks, public_picks

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Building picks costs a FanGraphs call, an ESPN call and ~60 game-log calls -- far too
# slow to run again when the approval page is tapped. The morning job populates this and
# the approval endpoints read it. A cold instance falls back to rebuilding.
_picks_cache = {}

# Fields the approval page, email and post builders need.
PICK_FIELDS = [
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


TOKEN_HEADER = "X-Form-Token"


def _verify_token(token: str):
    """Constant-time comparison against the configured form token."""
    expected = os.environ.get("FORM_TOKEN")
    if not expected:
        raise HTTPException(status_code=500, detail="FORM_TOKEN is not configured")
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="Invalid token")


def _verify_trigger(request: Request, token: str = ""):
    """Authorize the endpoint that sends mail and makes ~65 outbound API calls.

    Accepts the token from the `X-Form-Token` header first, falling back to the query
    string. Cloud Scheduler can send a custom header, so the scheduled job keeps the token
    out of URLs entirely -- Cloud Run records the full query string in its request logs,
    and this is the only endpoint where abuse actually costs something (Gmail's daily send
    cap, and rate limiting of the Cloud Run egress IP by FanGraphs or ESPN).
    """
    _verify_token(request.headers.get(TOKEN_HEADER) or token)


def _serialize(picks_df) -> list:
    """Convert the picks DataFrame into JSON-safe dicts for templates and posts."""
    if picks_df is None or picks_df.empty:
        return []

    columns = [c for c in PICK_FIELDS if c in picks_df.columns]
    frame = picks_df[columns].copy()

    for column in (
        "espnAdj", "espnBoom", "espnScore",
        "hilltopperAdj", "hilltopperBoom", "hilltopperScore",
        "percentOwned",
    ):
        if column in frame:
            frame[column] = frame[column].round(1)

    # Availability reads better as a whole-number percentage on the card.
    if "availability" in frame:
        frame["pitchChance"] = (frame["availability"] * 100).round().astype("Int64")
    if "gamesRest" in frame:
        frame["gamesRest"] = frame["gamesRest"].astype("Int64")

    # NaN is not valid JSON and renders as "nan" in templates.
    return json.loads(frame.to_json(orient="records"))


def _get_picks(rebuild_if_missing: bool = True) -> list:
    """Today's picks, from cache when the morning job has already run."""
    today = date.today().isoformat()

    if today in _picks_cache:
        return _picks_cache[today]

    if not rebuild_if_missing:
        return []

    print("Picks cache is cold; rebuilding.")
    picks = _serialize(public_picks(build_picks()))
    _picks_cache.clear()
    _picks_cache[today] = picks
    return picks


@app.get("/daily_picks")
async def daily_picks(request: Request, token: str = "", send: bool = True):
    """Build today's picks and email them. This is the scheduled morning job.

    The most strongly guarded endpoint: the service is deployed
    --allow-unauthenticated, and this is the one that sends mail and does real work.
    Prefer the `X-Form-Token` header over `?token=`.
    """
    _verify_trigger(request, token)

    try:
        frame = build_picks()
        picks = _serialize(public_picks(frame))
        mine = _serialize(league_picks(frame))

        _picks_cache.clear()
        _picks_cache[date.today().isoformat()] = picks

        if not picks:
            print("No picks today; skipping email.")
            return {"message": "No picks today", "count": 0}

        if send:
            send_email(picks, league_picks=mine)

        return {
            "message": "Picks generated",
            "count": len(picks),
            "leagueFreeAgents": len(mine),
        }
    except Exception as exc:
        print(f"daily_picks failed: {exc}")
        raise HTTPException(status_code=500, detail=f"daily_picks failed: {exc}")


@app.get("/pick_pitchers", response_class=HTMLResponse)
async def pick_pitchers_form(request: Request, token: str = ""):
    """Tap-to-rank approval page. The token arrives in the link from the email."""
    _verify_token(token)
    picks = _get_picks()

    return templates.TemplateResponse(
        name="pick_pitchers.html",
        request=request,
        context={"pitchers": picks, "pitchers_json": json.dumps(picks)},
    )


class PlayerPick(BaseModel):
    playerName: str
    tier: int = 1
    rank: int = 1


class PickRequest(BaseModel):
    # Accepted so older clients keep working, but deliberately not checked -- see below.
    token: str = ""
    picks: list[PlayerPick]


@app.post("/pick_pitchers")
async def pick_pitchers_submit(pick_request: PickRequest):
    """Turn the chosen players into ready-to-paste X and Patreon posts.

    Intentionally unauthenticated. This handler is a pure formatter: it reads no cache,
    touches no stored data and has no side effects -- names go in, post text comes out. A
    token here would guard nothing while forcing the secret into the page's JavaScript,
    which is a worse trade. The endpoints that do real work are guarded instead.
    """
    if not pick_request.picks:
        raise HTTPException(status_code=400, detail="No players selected")

    selected = sorted(
        (pick.model_dump() for pick in pick_request.picks),
        key=lambda p: (p["tier"], p["rank"]),
    )

    return postDrafts.build_all(selected)


@app.get("/post_now", response_class=HTMLResponse)
async def post_now(request: Request, token: str = ""):
    """One-tap path: post the top picks exactly as ranked, no manual tiering."""
    _verify_token(token)

    picks = _get_picks()
    if not picks:
        raise HTTPException(status_code=404, detail="No picks available today")

    # Two per tier, taken straight down the ESPN ranking.
    drafts = postDrafts.build_all(
        postDrafts.assign_default_tiers(picks[: postDrafts.DEFAULT_PICK_COUNT])
    )

    return templates.TemplateResponse(
        name="posts.html", request=request, context={"drafts": drafts}
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
