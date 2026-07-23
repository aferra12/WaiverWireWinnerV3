"""Sends the morning picks email.

The email is the decision surface: it carries enough detail to judge the list on a phone
without opening anything. Acting on it is one tap -- either post the top five as ranked,
or open the tap-to-rank page to choose.

Gmail's mobile client strips JavaScript and ignores the CSS tricks that fake interactive
controls, so the email itself is deliberately static. All styling is inline, since Gmail
also drops <style> blocks in many contexts.
"""

import os
import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .postDrafts import COMPOSE_URLS, DEFAULT_PICK_COUNT

_CELL = "padding:10px 8px;border-bottom:1px solid #e3e6ea;font-size:15px;"
_HEAD = (
    "padding:8px;border-bottom:2px solid #cfd6dd;font-size:12px;"
    "text-transform:uppercase;letter-spacing:.04em;color:#5b6976;text-align:left;"
)
_BUTTON = (
    "display:block;padding:16px 20px;border-radius:10px;font-size:17px;"
    "font-weight:600;text-decoration:none;text-align:center;"
)


def _fmt(value, spec="{:.1f}", fallback="—"):
    """Format a possibly-missing number without leaking NaN into the email."""
    if value is None or value != value:
        return fallback
    return spec.format(value)


def _pitch_chance(pick) -> str:
    """Availability as a whole-number percentage, derived rather than passed in."""
    value = pick.get("pitchChance")
    if value is None:
        availability = pick.get("availability")
        if availability is None or availability != availability:
            return "—"
        value = round(availability * 100)
    return f"{value:.0f}" if isinstance(value, float) else str(value)


def _rows_html(picks, avg_field: str, boom_field: str, score_field: str) -> str:
    """Table body for one list, scored under the given system.

    The headline column is projected points, matching the tap page. Expected value and the
    chance of pitching sit on a sub-line so the ordering stays readable while the inputs
    behind it remain visible.
    """
    rows = []
    for index, pick in enumerate(picks, start=1):
        rest = pick.get("gamesRest")
        rest_text = _fmt(rest, "{:.0f}") if rest is not None else "—"

        flag = pick.get("flag") or ""
        flag_html = (
            f"<div style='font-size:12px;color:#a8681f;margin-top:3px;'>{flag}</div>"
            if flag
            else ""
        )

        rows.append(
            f"<tr>"
            f"<td style='{_CELL}color:#7a8794;width:24px;'>{index}</td>"
            f"<td style='{_CELL}'>"
            f"<strong>{pick['playerName']}</strong>"
            f"<div style='font-size:12px;color:#7a8794;margin-top:3px;'>"
            f"{pick.get('teamAbbrev', '')} · {pick.get('role', '')} · "
            f"vs {pick.get('opponent', '')}</div>"
            f"<div style='font-size:12px;color:#7a8794;margin-top:2px;'>"
            f"{_fmt(pick.get(score_field))} expected · "
            f"{_pitch_chance(pick)}% likely to pitch</div>"
            f"{flag_html}</td>"
            f"<td style='{_CELL}text-align:center;'>{rest_text}</td>"
            f"<td style='{_CELL}text-align:right;'><strong>"
            f"{_fmt(pick.get(avg_field))}</strong></td>"
            f"<td style='{_CELL}text-align:right;color:#7a8794;'>"
            f"{_fmt(pick.get(boom_field))}</td>"
            f"<td style='{_CELL}text-align:right;color:#7a8794;'>"
            f"{_fmt(pick.get('percentOwned'))}%</td>"
            f"</tr>"
        )
    return "".join(rows)


def _table_html(picks, avg_field, boom_field, score_field, proj_label) -> str:
    return f"""\
  <table role="presentation" cellpadding="0" cellspacing="0" width="100%"
         style="background:#fff;border-radius:10px;border-collapse:collapse;
                overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08);">
    <tr>
      <th style="{_HEAD}"></th>
      <th style="{_HEAD}">Pitcher</th>
      <th style="{_HEAD}text-align:center;">Rest</th>
      <th style="{_HEAD}text-align:right;">{proj_label}</th>
      <th style="{_HEAD}text-align:right;">Boom</th>
      <th style="{_HEAD}text-align:right;">Own</th>
    </tr>
    {_rows_html(picks, avg_field, boom_field, score_field)}
  </table>"""


def build_email_html(picks, base_url: str, token: str, on_date: date = None,
                     league_picks=None) -> str:
    """Render both lists and the two action buttons.

    `picks` is the public list, ranked by ESPN standard scoring, which is what the X and
    Patreon posts are built from. `league_picks` is the personal list -- free agents in
    the configured league, scored under Hilltopper weights.
    """
    on_date = on_date or date.today()
    pretty_date = on_date.strftime("%A, %B %-d")

    if league_picks:
        league_section = f"""
  <h2 style="font-size:17px;margin:30px 0 4px;">Available in your league</h2>
  <p style="margin:0 0 12px;color:#7a8794;font-size:13px;">
    Free agents in your Hilltopper league, ranked by Hilltopper scoring. Not part of the posts.
  </p>
  {_table_html(league_picks, 'hilltopperAdj', 'hilltopperBoom', 'hilltopperScore', 'Hilltop')}
"""
    else:
        league_section = """
  <h2 style="font-size:17px;margin:30px 0 4px;">Available in your league</h2>
  <p style="margin:0 0 12px;color:#7a8794;font-size:13px;">
    No league data — set ESPN_LEAGUE_ID, ESPN_S2 and ESPN_SWID, or refresh expired cookies.
  </p>
"""

    choose_url = f"{base_url}/pick_pitchers?token={token}"
    post_now_url = f"{base_url}/post_now?token={token}"

    return f"""\
<html>
<body style="margin:0;padding:0;background:#f4f6f8;">
<div style="max-width:640px;margin:0 auto;padding:20px 16px;
     font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
     color:#1a2633;">

  <h1 style="font-size:22px;margin:0 0 4px;">Today's Picks</h1>
  <p style="margin:0 0 20px;color:#7a8794;font-size:14px;">{pretty_date}</p>

  <h2 style="font-size:17px;margin:0 0 4px;">For the posts</h2>
  <p style="margin:0 0 12px;color:#7a8794;font-size:13px;">
    Ranked by projected ESPN standard points — what most of your audience plays.
  </p>
  {_table_html(picks, 'espnAdj', 'espnBoom', 'espnScore', 'ESPN')}
{league_section}
  <p style="margin:16px 0 8px;color:#7a8794;font-size:13px;">
    <strong>ESPN</strong> / <strong>Hilltop</strong> are projected fantasy points per
    appearance under each scoring system, and set the order.
    <strong>Expected</strong> weights those by the chance of pitching today.
    <strong>Boom</strong> is the 75th percentile — the upside case.
    <strong>Rest</strong> is team games since the pitcher last threw.
  </p>

  <div style="margin:24px 0 8px;">
    <a href="{post_now_url}"
       style="{_BUTTON}background:#2e7d32;color:#fff;margin-bottom:10px;">
      Post top {DEFAULT_PICK_COUNT} as ranked
    </a>
    <a href="{choose_url}"
       style="{_BUTTON}background:#fff;color:#1a2633;border:1px solid #cfd6dd;">
      Choose picks &rarr;
    </a>
  </div>

  <p style="margin:20px 0 0;text-align:center;font-size:13px;color:#7a8794;">
    Composers:
    <a href="{COMPOSE_URLS['x']}" style="color:#2563eb;text-decoration:none;">Open X</a>
    &nbsp;·&nbsp;
    <a href="{COMPOSE_URLS['patreon']}" style="color:#2563eb;text-decoration:none;">Open Patreon</a>
  </p>

  <p style="margin:20px 0 0;color:#9aa8b6;font-size:12px;text-align:center;">
    Waiver Wire Winner
  </p>
</div>
</body>
</html>"""


def send_email(picks, base_url: str = None, token: str = None, on_date: date = None,
               league_picks=None):
    """Send the picks email. Credentials and recipient come from the environment."""
    sender = os.environ["EMAIL_SENDER"]
    password = os.environ["EMAIL_PASSWORD"]

    # `.get(key, default)` returns "" for a key present but blank, which a .env template
    # produces constantly -- `or` is what actually falls back.
    recipient = os.environ.get("EMAIL_RECIPIENT") or sender

    base_url = base_url or os.environ.get("BASE_URL") or ""
    token = token or os.environ.get("FORM_TOKEN") or ""

    if not password:
        raise RuntimeError("EMAIL_PASSWORD is blank -- set a Gmail app password.")
    on_date = on_date or date.today()

    message = MIMEMultipart("alternative")
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = f"Waiver Wire Picks — {on_date.strftime('%-m/%-d')}"

    message.attach(
        MIMEText(
            build_email_html(picks, base_url, token, on_date, league_picks), "html"
        )
    )

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(sender, password)
        server.send_message(message)

    print(f"Picks email sent to {recipient}")
