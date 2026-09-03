"""
Renders results as a self-contained HTML board.

Shows unit numbers, verdicts, and the subject line of the message each unit came
from. No resident names, email addresses or phone numbers -- not because they are
filtered out here, but because they never reach this module (see models.py).

Each unit links to an Outlook search for that unit number, so one click takes you
from "0735 needs outreach" to the actual thread, read in Outlook as yourself.
That is the whole privacy model in one gesture: the board says which unit, the
mail system says everything else.
"""

from __future__ import annotations

import html
import urllib.parse
from datetime import datetime

from models import Result, Verdict

CSS = """
:root { color-scheme: light }
* { box-sizing: border-box }
body {
  margin: 0; padding: 24px 20px 48px;
  background: #fbfbfa; color: #1a1a1a;
  font: 14px/1.5 ui-sans-serif, -apple-system, system-ui, "Segoe UI", sans-serif;
}
h1 { font-size: 18px; margin: 0 0 3px; letter-spacing: -.01em }
.sub { font-size: 12.5px; color: #6b6b6b; margin-bottom: 22px }
h2 {
  font-size: 11.5px; text-transform: uppercase; letter-spacing: .06em;
  color: #6b6b6b; font-weight: 600; margin: 26px 0 10px;
  padding-bottom: 5px; border-bottom: 1px solid #e6e6e6;
}
.card {
  border: 1px solid #e2e2e2; border-radius: 7px; background: #fff;
  padding: 13px 14px; margin-bottom: 9px;
}
.card.needs    { border-left: 3px solid #c2843d }
.card.unclear  { border-left: 3px solid #b4433a; background: #fdf8f7 }
.head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px }
.unit { font-weight: 650; font-size: 15px; letter-spacing: -.01em }
.unit a { color: inherit; text-decoration: none; border-bottom: 1px dotted #bbb }
.unit a:hover { border-bottom-style: solid }
.pill {
  display: inline-block; font-size: 11px; font-weight: 600;
  padding: 3px 9px; border-radius: 20px; white-space: nowrap;
}
.p-needs   { background: #fbeedb; color: #855420 }
.p-unclear { background: #f7e4e2; color: #8f3229 }
.p-handled { background: #e5f0e8; color: #1f6435 }
.meta { font-size: 12px; color: #7a7a7a; margin-top: 4px; word-break: break-word }
.why  { margin-top: 6px; font-size: 12.5px; color: #6f6f6f }
.empty { color: #8a8a8a; font-size: 13px; padding: 6px 0 }
.foot {
  margin-top: 32px; padding-top: 12px; border-top: 1px solid #e6e6e6;
  font-size: 11.5px; color: #8a8a8a; line-height: 1.7;
}
"""


def _esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def _outlook_search_url(unit: str) -> str:
    """Deep-link to an Outlook search for this unit."""
    return (
        "https://outlook.office.com/mail/search/id/"
        if not unit
        else "https://outlook.office.com/mail/?" + urllib.parse.urlencode({"q": unit})
    )


def _pill(verdict: Verdict) -> str:
    label, cls = {
        Verdict.NEEDS_OUTREACH: ("Needs outreach", "p-needs"),
        Verdict.UNCLEAR: ("Could not read", "p-unclear"),
        Verdict.HANDLED: ("Already handled", "p-handled"),
    }[verdict]
    return f'<span class="pill {cls}">{label}</span>'


def _card(result: Result) -> str:
    cls = {
        Verdict.NEEDS_OUTREACH: " needs",
        Verdict.UNCLEAR: " unclear",
    }.get(result.verdict, "")

    link = (
        f'<a href="{_esc(_outlook_search_url(result.unit))}" target="_blank" '
        f'rel="noopener">#{_esc(result.unit)}</a>'
    )

    parts = [
        f'<div class="card{cls}">',
        '<div class="head">',
        f'<span class="unit">{link}</span>',
        _pill(result.verdict),
        "</div>",
    ]

    if result.trigger_subject:
        when = (result.trigger_received or "")[:10]
        sender = result.trigger_sender.split("@")[0].replace(".", " ").title()
        bits = [b for b in (sender, when) if b]
        prefix = " &middot; ".join(_esc(b) for b in bits)
        parts.append(
            f'<div class="meta">{_esc(result.trigger_subject)}'
            + (f"<br>{prefix}" if prefix else "")
            + "</div>"
        )

    for e in result.evidence:
        parts.append(f'<div class="why">✓ {_esc(e.describe())}</div>')

    for p in result.problems:
        parts.append(f'<div class="why">⚠ {_esc(p)}</div>')

    parts.append("</div>")
    return "".join(parts)


def _section(title: str, results: list[Result], empty_text: str) -> str:
    body = (
        "".join(_card(r) for r in results)
        if results
        else f'<div class="empty">{empty_text}</div>'
    )
    return f"<h2>{_esc(title)} ({len(results)})</h2>{body}"


def render(results: list[Result], operator: str = "") -> str:
    by = lambda v: [r for r in results if r.verdict is v]  # noqa: E731

    needs = by(Verdict.NEEDS_OUTREACH)
    unclear = by(Verdict.UNCLEAR)
    handled = by(Verdict.HANDLED)

    generated = datetime.now().strftime("%a %d %b %Y, %H:%M")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Move-In Triage</title>
<style>{CSS}</style>
</head>
<body>
<h1>Move-In Triage</h1>
<div class="sub">{len(results)} triggered unit(s) &middot; generated {_esc(generated)}</div>

{_section("Needs outreach", needs, "Nothing outstanding.")}
{_section("Could not read", unclear, "Every trigger parsed cleanly.")}
{_section("Already handled", handled, "None yet.")}

<div class="foot">
Read-only. This never sends mail, replies, or changes anything in Outlook.<br>
No resident names, email addresses or phone numbers are carried by this tool &mdash;
unit numbers and message subjects only. Click a unit to search Outlook for it.<br>
{f"Signed in as {_esc(operator)}." if operator else ""}
</div>
</body>
</html>
"""
