# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

"Waiver Wire Winner" is a Python/FastAPI app that recommends fantasy baseball relief pitcher
pickups. Each morning it works out which low-rostered relievers are most likely to pitch that
day, ranks them by expected fantasy points, and emails the list. From the email you either post
the top picks as ranked or open a tap-to-rank page to choose, which generates ready-to-paste X
and Patreon posts.

Relief pitchers are the focus because their usage is unpredictable and rest-driven, which is
where the edge is.

## Architecture

There is no database. Everything is computed live from three APIs on each run.

```
FanGraphs closer depth chart ──┐  candidates, role, active/IL status, last-6-day usage
MLB schedule + standings       ├──> filter ──> score ──> rank ──> email ──> post drafts
MLB per-pitcher game logs      │  per-appearance fantasy points
ESPN players                   ┘  ownership %
```

### Modules (`helpers/`)

| File | Role |
| --- | --- |
| `fangraphs.py` | RosterResource closer depth chart. **Requires `curl_cffi`** (see Cloudflare below). |
| `mlb.py` | MLB Stats API: today's games, team games played, recent schedule, per-pitcher game logs. |
| `espn.py` | Ownership percentages, plus name normalization for cross-source joins. |
| `team_map.py` | FanGraphs team abbreviation → MLB team id. |
| `scoring.py` | Pure functions: fantasy points, availability, shrinkage. No I/O — this is what tests and the backtest exercise. |
| `pitcher_picks.py` | Orchestrates the pipeline into a ranked DataFrame. |
| `emailPicks.py` | Builds and sends the morning email. |
| `postDrafts.py` | X and Patreon post bodies, using the tier names below. |
| `backtest.py` | Replays past dates through `scoring.py` to measure the model. |

### Two lists, two scoring systems

The morning email carries two tables, because they serve different audiences:

| List | Audience | Scoring | Availability filter |
| --- | --- | --- | --- |
| **For the posts** | X and Patreon readers | ESPN standard | Global roster % under `OWNERSHIP_THRESHOLD` |
| **Available in your league** | You | Hilltopper | Actual free agency in `ESPN_LEAGUE_ID` |

Both are sliced from one scored pool by `public_picks()` and `league_picks()`. Neither is
derived from the other's truncated output — a pitcher buried in the ESPN ordering can be the
best Hilltopper play available, and a name rostered 30% globally can still be free in a
shallow league.

Scoring weights live in `scoring.PITCHING_POINTS` (Hilltopper) and
`scoring.ESPN_PITCHING_POINTS` (ESPN standard), registered together in
`scoring.SCORING_SYSTEMS`. ESPN lists innings pitched as 3 points per inning and credits
partials, which is exactly 1 point per out, so `outs` is scored directly. ESPN standard has
no blown-save, HBP, wild-pitch, balk, pickoff or quality-start component.

### The model

Ranking is `availability × adjustedAvgPoints`, computed separately under each scoring system.

- **Availability** = season appearance rate (`G / team games`) × a rest factor. Rest is counted
  in *team games*, not calendar days — an off day does not rest a bullpen the way a game the
  pitcher sat out does.
- **Expected points** = mean fantasy points per relief appearance from the MLB game log, shrunk
  toward a per-role prior computed from that day's candidate pool (handles thin samples and
  recent role changes).

A role multiplier and a six-day fatigue penalty were both tried and **removed** — measured over
a 30-day backtest, neither beat noise. See the docstring on `scoring.availability_score` for the
evidence. Role still does real work via the shrinkage prior.

Do not re-add features to the availability model without running `helpers/backtest.py` first.

## ⚠️ FanGraphs is behind Cloudflare

Plain `requests` gets a 403 challenge page from `fangraphs.com` **no matter what headers are
sent** — the TLS handshake itself is fingerprinted. `curl_cffi` with `impersonate="chrome"` is
what gets through, and it is why that dependency exists. If FanGraphs starts failing:

1. Try another `impersonate` profile (`chrome131`, `safari17_0`).
2. Fall back to `cloudscraper`.
3. Last resort: a headless-browser Cloud Run Job that fetches once daily into GCS.

`fangraphs.FanGraphsBlocked` is raised specifically on a challenge response, so this failure is
distinguishable from an ordinary outage.

## Configuration

`helpers/__init__.py` calls `load_dotenv()`, so every entry point reads `.env` locally.
Real environment variables always win, so Cloud Run is unaffected.

```bash
cp .env.example .env   # then fill in the blanks; .env is gitignored
```

## ⚠️ Secrets that were committed to a public repo

`aferra12/WaiverWireWinnerV3` is public, and these were pushed to `origin/main` in plain
text before the V3 rewrite removed them:

| Secret | Where it was | Commits |
| --- | --- | --- |
| Gmail app password for `waiverwirewinner@gmail.com` | `helpers/sendEmail.py` | 6 |
| ESPN `espn_s2` / `SWID` cookies | `helpers/getLikelyPitchers.py` | 4 |

Deleting the files does not help — git history is public and forks and caches may persist.
**These must be revoked, not hidden.** Assume anything ever committed here is compromised.

### Rotating the Gmail app password

1. Sign in as **`waiverwirewinner@gmail.com`** (the sending account, not your personal one).
2. Go to <https://myaccount.google.com/apppasswords>. This requires 2-Step Verification;
   enable it at <https://myaccount.google.com/signinoptions/twosv> if prompted.
3. **Revoke the existing app password** first — that is what actually kills the leaked one.
4. Create a new one, named e.g. `Waiver Wire Winner`. Google shows 16 characters in four
   groups; spaces are cosmetic and may be included or stripped.
5. Put it in `.env` as `EMAIL_PASSWORD`, and set it on Cloud Run.
6. Confirm no one else used the account meanwhile: <https://myaccount.google.com/notifications>.

### Rotating the ESPN cookies

Signing out of ESPN everywhere invalidates `espn_s2`. Then sign back in and copy the fresh
values per the instructions above.

venv/bin/python -m helpers.pitcher_picks                    # dry run, prints both tables
venv/bin/python -m helpers.backtest --days 30              # measure vs real outcomes (ESPN)
venv/bin/python -m helpers.backtest --days 30 --system hilltopper
venv/bin/python -m pytest tests/ -q                        # scoring and formatting tests

FORM_TOKEN=dev BASE_URL=http://localhost:8080 venv/bin/python main.py
```

## Endpoints

The service is deployed `--allow-unauthenticated`, so guarding is graded by what an endpoint
actually costs if abused rather than applied uniformly.

| Endpoint | Guard | Why |
| --- | --- | --- |
| `GET /daily_picks` | `X-Form-Token` header, or `?token=` | Sends mail and makes ~65 outbound API calls. The one that matters. |
| `GET /pick_pitchers?token=…` | `?token=` | Read-only page, but a cold cache lets it trigger a rebuild. |
| `GET /post_now?token=…` | `?token=` | Same. |
| `POST /pick_pitchers` | **none** | Pure formatter: names in, post text out. Reads nothing, no side effects. |
| `GET /health` | none | Liveness check. |

`POST /pick_pitchers` is deliberately open. A token there would guard nothing while forcing
the secret into page JavaScript, which is strictly worse. It still accepts a `token` field so
older clients keep working; the value is ignored.

`/daily_picks` prefers the header because **Cloud Run records the full query string in its
request logs**, so a URL token ends up in Cloud Logging. Cloud Scheduler sends a custom
header, keeping the token out of URLs for the only caller that matters:

```bash
gcloud scheduler jobs update http waiver-wire-daily \
  --uri "https://<service-url>/daily_picks" \
  --http-method GET \
  --update-headers "X-Form-Token=<FORM_TOKEN>"
```

Picks are cached in process memory keyed by date, since rebuilding costs ~60 game-log calls. A
cold instance rebuilds transparently (~2s). `min-instances` is 0, so tapping the email link
some hours after the morning job will usually hit a cold start.

## Environment variables

| Variable | Purpose |
| --- | --- |
| `FORM_TOKEN` | Shared secret in the email links. Required. |
| `BASE_URL` | Public service URL, used to build those links. |
| `EMAIL_SENDER` / `EMAIL_PASSWORD` | Gmail account and **app password**. |
| `EMAIL_RECIPIENT` | Defaults to `EMAIL_SENDER`. |
| `OWNERSHIP_THRESHOLD` | Max global roster % for the public list. Default `7.5`. |
| `ESPN_LEAGUE_ID` | Hilltopper league id. Without it the personal list is skipped. |
| `ESPN_S2` / `ESPN_SWID` | Cookies for the private league. See below. |

### Refreshing the ESPN league cookies

The Hilltopper league is private — an unauthenticated read returns `401 You are not
authorized to view this League`. Sign in at fantasy.espn.com, open DevTools → Application →
Cookies, and copy `espn_s2` and `SWID`. **They expire periodically**, and when they do
`espn.LeagueAccessError` is raised, the email still sends, and the personal list is replaced
with a note saying so. The public list never depends on these.

## Post format

Tiers, strongest first: **Flaming Hot Wings**, **Spicy Wings**, **Mild Wings**, **Dry Rub Wings**.
On the approval page, tap order maps onto these; further taps stack onto the last tier. X shows
one player per tier plus `#winthewire #fantasy #reliever #bulk`; Patreon lists every player under
a `>Tier Name` heading. Both are copy-to-clipboard — **Patreon has no post-creation API**, and X
posting was likewise made copy-and-paste.

## Notes

- All dates are ISO format (`YYYY-MM-DD`).
- `hilltopperPts` weights are in `scoring.PITCHING_POINTS` and match the retired BigQuery
  pipeline exactly, so historical comparisons stay valid.
- FanGraphs `pitcherUsage` entries are calendar cells, not outings. Only rows with `g == 1` are
  appearances; the rest carry a `valueOverride` (`AAA`, `AA`, `IL`) recording where the pitcher
  was. Treating those as outings badly misstates rest.
- The BigQuery table `sanguine-robot-454100-s3.waiver_wire_winner.player_game_logs` is no longer
  read or written, but has been left in place as a historical archive.
