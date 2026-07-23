# V3 deployment handoff

Status as of 2026-07-23. The rewrite is merged and deployed; what remains is finishing the
cutover and confirming the parts a smoke test cannot reach.

**Service URL:** `https://run-nightly-mlb-games-2f2ukamecq-uc.a.run.app`
(also reachable as `run-nightly-mlb-games-884588780829.us-central1.run.app`)

Read `CLAUDE.md` first — it covers the architecture, the model, and the two non-obvious
traps (Cloudflare, and FanGraphs usage rows being calendar cells rather than outings).

---

## Already verified — do not re-litigate

| Thing | Evidence |
| --- | --- |
| Deploy pipeline works | Run `29975316033` green, all 7 steps |
| **FanGraphs reachable from Cloud Run** | Smoke test returned `{"count":25,"leagueFreeAgents":10}` from inside the service. This was the project's one real unknown: Cloudflare fingerprints the TLS handshake and can accept a home IP while refusing a datacenter one. It accepted. |
| Private ESPN league readable from Cloud Run | Same response, `leagueFreeAgents: 10` |
| Env vars set correctly | Deploy log: `Setting: EMAIL_PASSWORD, EMAIL_RECIPIENT, EMAIL_SENDER, ESPN_LEAGUE_ID, ESPN_S2, ESPN_SWID, FORM_TOKEN, OWNERSHIP_THRESHOLD` |
| Token rejection | `403` for both a wrong and a missing token, verified live |
| Gmail app password authenticates | SMTP login tested locally against the rotated password |
| Model beats the baseline | 30-day backtest: precision@20 **50.0% vs 35.0%** naive, ESPN scoring |
| 68 tests pass, offline | `venv/bin/python -m pytest tests/ -q` |

---

## Remaining work

### 1. Repoint Cloud Scheduler — **required, the morning job is currently broken**

The existing job still calls `GET /`, which no longer exists. Until this changes there is no
morning email.

```bash
gcloud scheduler jobs list --location us-central1

gcloud scheduler jobs update http <JOB_NAME> \
  --location us-central1 \
  --uri "https://run-nightly-mlb-games-2f2ukamecq-uc.a.run.app/daily_picks" \
  --http-method GET \
  --update-headers "X-Form-Token=<FORM_TOKEN>"
```

`FORM_TOKEN` is in the local `.env` and in GitHub secrets. It goes in a **header**, not the
URL — Cloud Run writes the full query string into its request logs.

Verify:

```bash
gcloud scheduler jobs run <JOB_NAME> --location us-central1
# then confirm the email arrives
```

### 2. Confirm the email actually sends from Cloud Run — **not yet tested**

Every check so far used `send=false`, so **SMTP has never run from inside Cloud Run.** It
works locally, but the deployed environment is untested.

```bash
curl -H "X-Form-Token: <FORM_TOKEN>" \
  "https://run-nightly-mlb-games-2f2ukamecq-uc.a.run.app/daily_picks"
```

Expect a JSON body and an email within a minute. If nothing arrives, see pitfall A.

### 3. Walk the mobile flow on the real service

Open the email on a phone and confirm the whole path end to end:

- Both tables render — "For the posts" and "Available in your league"
- **Choose picks →** opens the tap page; tapping assigns tiers in pairs
  (taps 1–2 Flaming Hot, 3–4 Spicy, 5–6 Mild, 7–8 Dry Rub)
- The first of each pair shows "named on X"
- **Generate** produces both post bodies; copy buttons work
- **Post top 8 as ranked** works from the email without opening the tap page

Target is under two minutes. Note the first tap after a quiet period hits a cold start
(~2–4s) while the picks cache rebuilds.

### 4. Security cleanup

```bash
rm ~/Downloads/sanguine-robot-454100-s3-*.json   # three service-account keys
```

Then in the console: **IAM & Admin → Service Accounts → `www-cloud-run@…` → KEYS**, and
delete the keys you are not using. One is live in the `GCP_SA_KEY` secret; the others are
standing credentials with deploy access to the project.

Already handled: the Gmail app password was rotated and the old one revoked. `espn_s2` is a
fresh session token. `SWID` is unchanged and still public in git history, which is fine — it
is a stable account identifier, not a rotatable secret, and it does not authenticate without
`espn_s2`.

---

## Pitfalls, and what each looks like

### A. Gmail SMTP blocked or throttled from Cloud Run
The untested piece. Port 587 outbound is permitted on Cloud Run (25 is not), so it should
work, but it has not been proven.

- **Symptom:** `/daily_picks` returns 500, or succeeds with no email arriving.
- **Check:** `gcloud run services logs read run-nightly-mlb-games --region us-central1 --limit 50`
- **Likely causes:** `SMTPAuthenticationError` means the app password is wrong or was
  revoked. A timeout means egress is blocked. Silence with a 200 means it sent and the mail
  is in spam — check there first.

### B. ESPN cookies expire
`espn_s2` is a session token with a finite life. This *will* happen.

- **Symptom:** email still arrives, but "Available in your league" is replaced by a note
  naming the missing variables. Logs show `LeagueAccessError` and a 401.
- **Fix:** re-copy `espn_s2` and `SWID` from fantasy.espn.com DevTools → Application →
  Cookies, update both the GitHub secret and `.env`, redeploy.
- **By design:** the public list never depends on these, so the posts keep working.

### C. Cloudflare starts refusing Cloud Run
Verified working today, but it is an arms race and could regress at any time.

- **Symptom:** deploy smoke test fails, or the morning job 500s. Logs show
  `FanGraphsBlocked` or a 403 from `fangraphs.com`.
- **Fix:** escalation ladder in `CLAUDE.md` — try another `curl_cffi` `impersonate` profile
  (`chrome131`, `safari17_0`), then `cloudscraper`, then a headless-browser Cloud Run Job
  writing daily JSON to GCS.
- The error type is distinct from an ordinary outage, so the logs will say which it is.

### D. Cold starts on the tap page
`min-instances` is 0, so the container scales to zero between the morning email and when you
tap the link. The picks cache lives in process memory, so that tap rebuilds it.

- **Symptom:** first page load takes ~2–4s. Not a bug.
- **Fix if it annoys you:** `gcloud run services update run-nightly-mlb-games --region
  us-central1 --min-instances 1`. Costs a few dollars a month. Deliberately left off.

### E. Never edit `--set-env-vars` back into the workflow
Three deploys failed getting here, two from shell quoting. The rules that came out of it:

- Never interpolate `${{ secrets.* }}` into a `run:` body. GitHub substitutes raw text
  before the shell parses it, so a value containing quotes — a service-account JSON —
  executes as commands. Pass secrets through `env:` and read them as shell variables.
- Never use inline `--set-env-vars`. It needs a delimiter present in no value; comma is the
  default and `@` collides with email addresses. The workflow writes a YAML file with
  `json.dumps` instead, which has no delimiter at all.
- A blank value must be **omitted**, not written as empty — Cloud Run rejects nulls.

### F. Two scoring systems, deliberately ordered differently
Easy to "fix" by mistake.

- The public list is sorted by **projected ESPN points** (`espnAdj`), not expected value.
  Expected value ranks better in backtest (50% vs 35% precision@20), but the list only seeds
  a manual choice of eight, and expected value plus chance-of-pitching are shown on every
  row so the judgement happens per player.
- Whatever field sorts a list must be the one displayed large, or the column reads as
  unsorted. `tests/test_template_parity.py` enforces this.
- Hilltopper scoring appears **only** in the email's league section. A test asserts it never
  returns to the tap page.

### G. Do not re-add availability features without a backtest
A role multiplier and a six-day fatigue penalty were both tried and removed — neither beat
noise over 30 days, and *inverting* the role multiplier changed nothing, which is what a
feature carrying no information looks like. Evidence is in the `scoring.availability_score`
docstring. Run `venv/bin/python -m helpers.backtest --days 30` before touching that model.

---

## Useful commands

```bash
# local dry run, no side effects
venv/bin/python -m helpers.pitcher_picks

# measure the model
venv/bin/python -m helpers.backtest --days 30                      # ESPN scoring
venv/bin/python -m helpers.backtest --days 30 --system hilltopper

venv/bin/python -m pytest tests/ -q

# local server
FORM_TOKEN=dev BASE_URL=http://localhost:8080 venv/bin/python main.py

# deployed service
gcloud run services logs read run-nightly-mlb-games --region us-central1 --limit 50
gcloud run services describe run-nightly-mlb-games --region us-central1
gh run list --limit 5
```

---

## Open questions worth a decision

- **`min-instances`** — leave at 0 and accept a cold start on the first tap, or pay a few
  dollars a month for an instant page?
- **Ties in the tap list** — several pitchers display the same rounded projected points
  (4.3, 4.3). The ordering underneath is correct; the display rounds. Show another decimal,
  or leave it?
- **Workload Identity Federation** — would remove the long-lived `GCP_SA_KEY` entirely.
  More setup, nothing to leak or rotate.
- **The retired BigQuery table** — `sanguine-robot-454100-s3.waiver_wire_winner.player_game_logs`
  is no longer read or written. Kept as a historical archive; delete it whenever.
