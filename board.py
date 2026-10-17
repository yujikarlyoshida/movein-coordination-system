"""
Renders results as a self-contained HTML board.

Shows unit numbers, move-in dates, what was checked, and a status. No resident
names, email addresses or phone numbers -- not because they are filtered out
here, but because they never reach this module (see models.py).

Each unit links to an Outlook search for that unit number, so one click takes you
from "0735 needs outreach" to the actual thread, read in Outlook as yourself.
That is the whole privacy model in one gesture: the board says which unit, the
mail system says everything else.

Look: warm cream paper, deep green ink, a lime marker highlight and a soft
periwinkle, rounded cards and pill-shaped statuses. Sentence case, plain words,
no symbols or emoji anywhere in the text.
"""

from __future__ import annotations

import html
import re
import urllib.parse
from datetime import datetime

import confirm_sheet
import triggers
from models import Result, Verdict

CSS = """
:root {
  color-scheme: light;
  --paper: #f6f3ee; --card: #fbf9f5; --ink: #1f3a2e; --ink-2: #53655c;
  --line: rgba(31, 58, 46, .14); --lime: #edf49b; --peri: #d9e1fc;
}
* { box-sizing: border-box }
body {
  margin: 0; padding: 36px 40px 56px; background: var(--paper); color: var(--ink);
  font: 15px/1.5 "Avenir Next", "Manrope", ui-rounded, -apple-system, system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 980px; margin: 0 auto }
.top { display: flex; justify-content: space-between; align-items: baseline; gap: 16px; margin-bottom: 6px }
.brand { font-weight: 700; font-size: 15px; letter-spacing: -.01em }
.status { font-size: 13px; color: var(--ink-2) }
h1 {
  font-size: 40px; line-height: 1.12; font-weight: 700; letter-spacing: -.025em;
  margin: 26px 0 8px;
}
.mark { background: linear-gradient(transparent 58%, var(--lime) 58%, var(--lime) 92%, transparent 92%); padding: 0 4px }
.lede { font-size: 15px; color: var(--ink-2); margin: 0 0 26px; max-width: 640px }
.rule { height: 5px; background: linear-gradient(90deg, var(--ink) 0 93%, var(--lime) 93% 100%); border-radius: 3px; margin: 0 0 28px }
.cards { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; margin-bottom: 28px }
.card { background: var(--card); border: 1.5px solid var(--ink); border-radius: 20px; padding: 16px 18px }
.card.hot { background: var(--lime) }
.card .label { font-size: 13px; color: var(--ink-2) }
.card.hot .label { color: var(--ink) }
.card .n { font-size: 36px; font-weight: 700; letter-spacing: -.02em; line-height: 1.1; margin-top: 4px }
.table { background: var(--card); border: 1.5px solid var(--ink); border-radius: 22px; overflow: hidden }
table { width: 100%; border-collapse: collapse }
th { text-align: left; font-size: 12.5px; font-weight: 600; color: var(--ink-2); padding: 14px 20px; border-bottom: 1.5px solid var(--ink) }
td { padding: 14px 20px; border-bottom: 1px solid var(--line); vertical-align: top; font-size: 14.5px }
tr:last-child td { border-bottom: 0 }
.unit a { color: var(--ink); font-weight: 700; font-size: 16px; text-decoration: none; letter-spacing: -.01em }
.unit a:hover { text-decoration: underline; text-decoration-thickness: 2px; text-underline-offset: 3px }
.check { color: var(--ink) }
.note { font-size: 13px; color: var(--ink-2); margin-top: 3px }
.pill { display: inline-block; font-size: 13px; font-weight: 600; padding: 5px 14px; border-radius: 999px; white-space: nowrap }
.p-needs { background: var(--ink); color: var(--paper) }
.p-unclear { background: var(--lime); color: var(--ink); border: 1.5px solid var(--ink) }
.p-skipped { background: transparent; color: var(--ink); border: 1.5px solid var(--ink) }
.p-handled { background: var(--peri); color: var(--ink) }
.empty { padding: 36px 20px; text-align: center; color: var(--ink-2) }
.foot { margin-top: 26px; font-size: 12.5px; color: var(--ink-2); line-height: 1.7 }
.right { display: flex; align-items: center; gap: 14px }
.btn { font: inherit; font-size: 14px; font-weight: 600; border-radius: 999px; padding: 9px 20px; cursor: pointer;
  border: 1.5px solid var(--ink); background: var(--card); color: var(--ink) }
.btn.dark { background: var(--ink); color: var(--paper) }
.btn:hover { filter: brightness(.96) } .btn:active { transform: scale(.98) }
.btn.small { font-size: 12.5px; padding: 4px 12px }
.notice { background: var(--peri); border-radius: 14px; padding: 10px 16px; margin: 0 0 22px; font-size: 14px }
.panel { margin-top: 28px; background: var(--card); border: 1.5px solid var(--ink); border-radius: 22px; padding: 20px 22px }
.panel h2 { font-size: 20px; font-weight: 700; letter-spacing: -.015em; margin: 0 0 4px }
.panel .sub { font-size: 13.5px; color: var(--ink-2); margin: 0 0 16px }
.chips { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px }
.chip { display: inline-flex; align-items: center; gap: 10px; border: 1.5px solid var(--ink); border-radius: 999px; padding: 5px 6px 5px 14px; font-size: 13.5px }
.chip .kind { color: var(--ink-2); font-size: 12px }
.chip button { font: inherit; font-size: 12px; border: 0; background: transparent; color: var(--ink-2); cursor: pointer; padding: 3px 8px; border-radius: 999px }
.chip button:hover { background: var(--paper); color: var(--ink) }
.addrow { display: flex; gap: 10px }
.addrow input { flex: 1; font: inherit; font-size: 14px; border: 1.5px solid var(--ink); border-radius: 999px; padding: 9px 16px; background: var(--paper); color: var(--ink) }
.addrow input:focus { outline: 3px solid var(--peri) }
.seen { margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--line) }
.seen .item { display: flex; justify-content: space-between; align-items: center; padding: 6px 0; font-size: 14px }
.seen .hl { background: var(--lime); padding: 1px 6px; border-radius: 6px }
.no-app .app-only { display: none }
.sites { display: inline-flex; align-items: center; gap: 6px; margin-right: 6px }
.sites-label { font-size: 13px; color: var(--ink-2) }
.p-missing { background: transparent; color: var(--ink); border: 1.5px dashed var(--ink); margin-top: 6px; cursor: pointer }
.gap { font-size: 13px; color: var(--ink); margin-top: 3px }
.gap .blocks { color: var(--ink-2) }
.veil { position: fixed; inset: 0; background: rgba(31, 58, 46, .38); display: none; align-items: center; justify-content: center; padding: 24px; z-index: 10 }
.veil.open { display: flex }
.pop { background: var(--card); border: 1.5px solid var(--ink); border-radius: 26px; padding: 26px 28px; width: 100%; max-width: 520px; max-height: 80vh; overflow: auto }
.pop h2 { font-size: 26px; font-weight: 700; letter-spacing: -.02em; margin: 0 0 6px }
.pop .sub { font-size: 14px; color: var(--ink-2); margin: 0 0 18px }
.pop .unitgap { border-top: 1px solid var(--line); padding: 12px 0 }
.pop .unitgap b { font-size: 17px }
.pop .actions { display: flex; justify-content: flex-end; margin-top: 16px }
.p-prepare { background: var(--lime); color: var(--ink); border: 1.5px solid var(--ink); cursor: pointer; margin-left: 6px }
.p-done { background: var(--peri); color: var(--ink) }
@media (max-width: 720px) {
  body { padding: 24px 16px 40px } h1 { font-size: 30px }
  .cards { grid-template-columns: repeat(2, minmax(0, 1fr)) }
  th, td { padding: 12px 12px }
}
""" + confirm_sheet.CSS

_STATUS = {
    Verdict.NEEDS_OUTREACH: ("Needs outreach", "p-needs"),
    Verdict.UNCLEAR: ("Check", "p-unclear"),
    Verdict.SKIPPED_CORPORATE: ("Skipped, corporate", "p-skipped"),
    Verdict.HANDLED: ("Handled", "p-handled"),
}


def _esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def _outlook_search_url(unit: str) -> str:
    """Deep-link to an Outlook search for this unit."""
    return (
        "https://outlook.office.com/mail/search/id/"
        if not unit
        else "https://outlook.office.com/mail/?" + urllib.parse.urlencode({"q": unit})
    )


def _move_in(text: str, today: datetime | None = None) -> str:
    """'09/20/2026' -> 'Sep 20'; the year only when it is not this year."""
    if not text:
        return ""
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            when = datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
        label = f"{when:%b} {when.day}"
        if when.year != (today or datetime.now()).year:
            label += f", {when.year}"
        return label
    return text.strip()


def _checks(result: Result) -> tuple[str, list[str]]:
    """What was checked, in plain words, plus any notes worth a second line."""
    notes = list(result.problems)
    if result.verdict is Verdict.HANDLED:
        kinds = {e.kind for e in result.evidence}
        said = []
        if kinds & {"thread_reply", "announcement"}:
            said.append("Teammate replied")
        if "welcome_sent" in kinds:
            said.append("Welcome email already sent")
        return (", ".join(said) or "Handled by a teammate"), []
    if result.verdict is Verdict.SKIPPED_CORPORATE:
        first = notes.pop(0) if notes else ""
        signals = re.search(r"\(([^)]*)\)", first)
        what = signals.group(1) if signals else "corporate lease"
        if first.startswith("possibly"):
            return f"Possibly corporate: {what}", ["Check this one by hand"] + notes
        return what[:1].upper() + what[1:], notes
    if result.verdict is Verdict.UNCLEAR:
        if not notes:
            return "Couldn't read this lease", []
        first = notes.pop(0)
        return first[:1].upper() + first[1:], notes
    return "No teammate reply, no welcome sent", notes


_GAP_NOTE = re.compile(
    r": (no (phone number|email address) given|(email address|phone number|lease start date) couldn't be read"
    r"|name has more than two words.*)$"
)


def _gap_lines(result: Result) -> list[tuple[str, str]]:
    """[("1st resident: no phone", "Blocks Xfinity")] -- positions and field names only."""
    out = []
    for position, fields in result.missing:
        blocks = triggers.blocked_steps(fields)
        out.append((f"{triggers.ordinal(position)} resident: {triggers.gap_phrase(fields)}",
                    f"Blocks {' and '.join(blocks)}" if blocks else ""))
    return out


def missing_keys(result: Result) -> set[str]:
    """One key per missing field, for remembering what the user has seen."""
    return {f"{result.unit}:{p}:{f}" for p, fields in result.missing for f in fields}


def _row(result: Result, interactive: bool = False, done: bool = False) -> str:
    label, cls = _STATUS[result.verdict]
    if done:
        label, cls = "Done", "p-done"
    check, notes = _checks(result)
    gaps = _gap_lines(result)
    if gaps:
        # The structured lines below say the same thing more usefully.
        notes = [n for n in notes if not _GAP_NOTE.search(n)]
    link = (
        f'<a href="{_esc(_outlook_search_url(result.unit))}" target="_blank" '
        f'rel="noopener" title="Search Outlook for this unit">{_esc(result.unit)}</a>'
    )
    note_html = "".join(f'<div class="note">{_esc(n[:1].upper() + n[1:])}</div>' for n in notes)
    note_html += "".join(
        f'<div class="gap">{_esc(what)}. <span class="blocks">{_esc(blocks)}</span></div>'
        for what, blocks in gaps
    )
    prepare = (f'<span class="pill p-prepare app-only" data-prepare="{_esc(result.unit)}" '
               'title="Review and carry out this unit">Prepare</span>'
               if interactive and not done and result.verdict is Verdict.NEEDS_OUTREACH else "")
    missing_pill = ('<br><span class="pill p-missing" data-open-missing="1" '
                    'title="See what is missing">Missing info</span>') if gaps else ""
    return (
        f'<tr data-verdict="{_esc(result.verdict.value)}">'
        f'<td class="unit">{link}</td>'
        f"<td>{_esc(_move_in(result.move_in))}</td>"
        f'<td><div class="check">{_esc(check)}</div>{note_html}</td>'
        f'<td><span class="pill {cls}">{_esc(label)}</span>{prepare}{missing_pill}</td>'
        "</tr>"
    )


def _headline(needs: int, unclear: int) -> str:
    if needs and unclear:
        return (f'{needs} unit{"s" if needs != 1 else ""} <span class="mark">need you</span>, '
                f"{unclear} to check")
    if needs:
        return f'{needs} unit{"s" if needs != 1 else ""} <span class="mark">need{"s" if needs == 1 else ""} you</span>'
    if unclear:
        return f'{unclear} lease{"s" if unclear != 1 else ""} <span class="mark">to check</span>'
    return 'Nothing <span class="mark">needs you</span> right now'


_CONN = {
    "signed_in": ("Signed in", "p-handled"),
    "signed_out": ("Sign in needed", "p-unclear"),
    "loading": ("Checking", "p-skipped"),
    "not_open": ("Not open yet", "p-skipped"),
}


def _connections_panel(connections: list) -> str:
    """Each system's sign-in state, with a button to its tab in the app's browser."""
    rows = "".join(
        f'<div class="item"><span><b>{_esc(label)}</b></span><span>'
        f'<span class="pill {_CONN.get(state, _CONN["loading"])[1]}">{_CONN.get(state, _CONN["loading"])[0]}</span> '
        f'<button class="btn small app-only" data-action="open_site" data-value="{_esc(key)}">Open</button></span></div>'
        for key, label, state in connections
    )
    return (
        '<div class="panel"><h2>Connections</h2>'
        '<p class="sub">Sign in to each system in the app\'s own browser. '
        "The app never sees your passwords.</p>"
        f'<div class="seen" style="border:0;padding:0;margin:0">{rows}</div></div>'
    )


def _senders_panel(senders: dict, unknown: list) -> str:
    """The editable sender list, plus senders seen sending lease emails but missing."""
    chips = "".join(
        f'<span class="chip">{_esc(v)} <span class="kind">{kind}</span>'
        f'<button class="app-only" data-action="remove_sender" data-value="{_esc(v)}" '
        f'title="Remove from the list">Remove</button></span>'
        for kind, values in (("address", senders.get("addresses", [])), ("name", senders.get("names", [])))
        for v in values
    ) or '<span class="sub">No senders yet. Add the leasing team below.</span>'
    seen = ""
    if unknown:
        rows = "".join(
            f'<div class="item"><span><span class="hl">{_esc(who)}</span> sent {n} lease '
            f'email{"s" if n != 1 else ""} the app is ignoring</span>'
            f'<button class="btn small app-only" data-action="add_sender" data-value="{_esc(who)}">Add</button></div>'
            for who, n in unknown
        )
        seen = f'<div class="seen"><div class="sub" style="margin:0 0 6px">Not on your list yet</div>{rows}</div>'
    return (
        '<div class="panel"><h2>Lease email senders</h2>'
        '<p class="sub">Only emails from these senders are read as new leases. '
        "Use an email address, or a full name exactly as Outlook shows it.</p>"
        f'<div class="chips">{chips}</div>'
        '<form class="addrow app-only" id="add">'
        '<input id="sender" autocomplete="off" placeholder="leasing.manager@company.com or Jordan Lee">'
        '<button class="btn dark" type="submit">Add sender</button></form>'
        f"{seen}</div>"
    )


_SCRIPT = """
<script>
(function () {
  var bridge = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.triage;
  if (!bridge) { document.body.classList.add('no-app'); return; }
  function send(action, value) { bridge.postMessage({ action: action, value: value || '' }); }
  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-action]');
    if (!b) return;
    e.preventDefault();
    if (b.dataset.action === 'run') { b.textContent = 'Checking...'; b.disabled = true; }
    if (b.dataset.action === 'ack_missing') { document.getElementById('missing').classList.remove('open'); }
    send(b.dataset.action, b.dataset.value);
  });
  document.addEventListener('click', function (e) {
    if (e.target.closest('[data-open-missing]')) document.getElementById('missing').classList.add('open');
  });
  var form = document.getElementById('add');
  if (form) form.addEventListener('submit', function (e) {
    e.preventDefault();
    var v = document.getElementById('sender').value.trim();
    if (v) send('add_sender', v);
  });
})();
</script>"""


def _missing_popup(results: list[Result], acknowledged: set[str]) -> str:
    """The pop-up listing units that cannot be fully prepared. Opens on its own
    when something is new since the user last pressed Got it."""
    gappy = [r for r in results if r.missing]
    if not gappy:
        return ""
    keys = set().union(*(missing_keys(r) for r in gappy))
    fresh = keys - acknowledged
    units = "".join(
        f'<div class="unitgap"><b>{_esc(r.unit)}</b>'
        + "".join(f'<div class="gap">{_esc(what)}. <span class="blocks">{_esc(blocks)}</span></div>'
                  for what, blocks in _gap_lines(r))
        + "</div>"
        for r in gappy
    )
    n = len(gappy)
    return (
        f'<div class="veil{" open" if fresh else ""}" id="missing">'
        '<div class="pop" role="dialog" aria-labelledby="missing-title">'
        '<h2 id="missing-title">Some details are missing</h2>'
        f'<p class="sub">{n} unit{"s" if n != 1 else ""} can\'t be fully prepared yet. '
        "Ask the leasing team for what's missing, then run a check again. "
        "Nothing is guessed or filled in for you.</p>"
        f"{units}"
        '<div class="actions"><button class="btn dark" data-action="ack_missing" '
        f'data-value="{_esc(",".join(sorted(keys)))}">Got it</button></div>'
        "</div></div>"
    )


def render(results: list[Result], operator: str = "", status: str = "",
           senders: dict | None = None, unknown: list | None = None, notice: str = "",
           acknowledged: set[str] | None = None, done_units: set[str] | None = None,
           connections: list | None = None) -> str:
    """
    The whole board as one HTML page.

    `operator` is accepted for callers that still pass it and is not shown.
    `senders` ({"addresses": [...], "names": [...]}) adds the editable sender
    panel, with `unknown` [(sender, threads)] as one-click suggestions; the
    in-app board passes them, the plain HTML file from the CLI does not.
    `notice` is a one-line message at the top, such as "Added the address".
    `done_units` are units carried out this session; they show as Done.
    `connections` [(key, label, state)] adds the sign-in panel for the app's
    browser (live mode only).
    `acknowledged` holds missing-field keys the user has already seen (see
    missing_keys); the missing-info pop-up opens by itself only for new ones.
    `status` is the watching line for the top right ("Watching Move ins, last
    check 4:31 PM"); without it the board says when it was generated.
    """
    by = lambda v: [r for r in results if r.verdict is v]  # noqa: E731
    interactive = senders is not None
    needs, unclear = by(Verdict.NEEDS_OUTREACH), by(Verdict.UNCLEAR)
    handled, skipped = by(Verdict.HANDLED), by(Verdict.SKIPPED_CORPORATE)

    generated = datetime.now().strftime("%I:%M %p").lstrip("0")
    status_text = status or f"Updated {generated}"

    cards = [
        ("Need outreach", len(needs), bool(needs)),
        ("Handled", len(handled), False),
        ("Skipped, corporate", len(skipped), False),
        ("Check", len(unclear), bool(unclear)),
    ]
    cards_html = "".join(
        f'<div class="card{" hot" if hot else ""}"><div class="label">{_esc(label)}</div>'
        f'<div class="n">{n}</div></div>'
        for label, n, hot in cards
    )

    # Order: what needs you, then what to check, then skipped, then done.
    order = needs + unclear + skipped + handled
    if order:
        table = (
            '<div class="table"><table>'
            "<thead><tr><th style=\"width:14%\">Unit</th><th style=\"width:16%\">Move-in</th>"
            "<th>Checks</th><th style=\"width:20%\">Status</th></tr></thead><tbody>"
            + "".join(_row(r, interactive, r.unit in (done_units or set())) for r in order)
            + "</tbody></table></div>"
        )
    else:
        table = ('<div class="table"><div class="empty">No move-in leases in the window yet. '
                 "The board fills in after the first check.</div></div>")

    # Open browser: the app's own browser (Outlook, RiseIO, Xfinity tabs), from
    # the top of the board, whenever there are systems to sign in to.
    browser_button = ""
    if interactive and connections is not None:
        browser_button = (
            '<span class="sites app-only"><span class="sites-label">Open</span>'
            + "".join(f'<button class="btn small" data-action="open_site" data-value="{key}">{label}</button>'
                      for key, label in (("outlook", "Email"), ("riseio", "RiseIO"), ("xfinity", "Xfinity")))
            + "</span>")
    run_button = (browser_button + '<button class="btn dark app-only" data-action="run">Run check</button>'
                  if interactive else "")
    notice_html = f'<div class="notice">{_esc(notice)}</div>' if notice else ""
    panel = _senders_panel(senders, unknown or []) if interactive else ""
    if interactive and connections:
        panel = _connections_panel(connections) + panel
    popup = _missing_popup(results, acknowledged or set()) if interactive else ""
    # Empty on disk: the sheet's resident details are injected from memory when
    # it opens, so they never reach this file.
    sheet_host = ('<div class="veil" id="sheetveil"><div class="pop" role="dialog" '
                  'aria-label="Prepare unit"><div id="sheet-host"></div></div></div>') if interactive else ""

    lede = ("Checked against every reply in the thread before anything is suggested. "
            "Read-only: nothing is sent, saved or changed in Outlook.")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Move-in triage</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<div class="top"><div class="brand">Move-in triage</div><div class="right"><div class="status">{_esc(status_text)}</div>{run_button}</div></div>
{notice_html}
<h1>{_headline(len(needs), len(unclear))}</h1>
<p class="lede">{_esc(lede)}</p>
<div class="rule"></div>
<div class="cards">{cards_html}</div>
{table}
{panel}
{popup}
{sheet_host}
<div class="foot">
Units and statuses only. Residents' names, email addresses and phone numbers stay in Outlook.
Select a unit to search Outlook for its thread.
</div>
</div>
{_SCRIPT if interactive else ""}
{"<script>" + confirm_sheet.SCRIPT + "</script>" if interactive else ""}
</body>
</html>
"""
