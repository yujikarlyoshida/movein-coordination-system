"""
The confirmation sheet: "Prepare unit 0735".

One unit, everything that will happen to it, and the one click that makes it
happen. Resident details appear here because this is the screen where you
approve what is typed and sent -- and only here: the sheet is injected into
the open board window from memory and never written to a file.

Gaps are fields, not dead ends. A resident with no usable phone gets an empty,
highlighted phone field saying which step needs it; type the number the
leasing team gave you and it is used for this unit only. A name whose split
was a guess shows both halves and asks you to confirm them.

    build(...)       the sheet's HTML for one unit
    validate(...)    the same checks the sheet runs, again on the Python side,
                     so a value reaching the runner has been cleaned twice
"""

from __future__ import annotations

import html
import json

import triggers

CSS = """
#sheetveil .pop { max-width: 680px }
.sheet { max-width: 640px }
.sheet h2 { font: 600 28px/1.15 -apple-system, "SF Pro Display", system-ui, sans-serif; letter-spacing: -.025em; margin: 0 0 10px }
.verified { background: var(--soft); border-radius: 14px; padding: 9px 14px; font-size: 14px; margin-bottom: 18px }
.resident { border-top: 1px solid var(--line); padding: 16px 0 10px }
.resident h3 { font-size: 15px; margin: 0 0 10px; font-weight: 600 }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 14px; align-items: start }
.f label { display: block; font-size: 12.5px; color: var(--ink-2); margin-bottom: 3px }
.f input { width: 100%; box-sizing: border-box; font: inherit; font-size: 14px; border: 1px solid rgba(0, 0, 0, .14); border-radius: 12px; padding: 8px 12px; background: var(--card); color: var(--ink) }
.f input:focus { outline: 0; border-color: var(--accent); box-shadow: 0 0 0 4px rgba(0, 113, 227, .18) }
.f.need input { border-color: #c13fd1; background: var(--hi) }
.f .hint { font-size: 12px; color: #8a2bb0; margin-top: 3px }
.f .bad { font-size: 12px; color: #9b2c1f; margin-top: 3px; display: none }
.f.err .bad { display: block } .f.err input { border-color: #9b2c1f }
.confirmname { font-size: 13.5px; display: flex; gap: 8px; align-items: center; margin-top: 8px; background: var(--hi); border-radius: 12px; padding: 8px 12px }
.steps { border-top: 1px solid var(--line); margin-top: 10px; padding-top: 14px }
.steps label { display: flex; gap: 10px; align-items: flex-start; font-size: 14.5px; line-height: 1.45; margin-bottom: 10px }
.steps .why { font-size: 12.5px; color: var(--ink-2) }
.sendsmail { margin-left: 26px; font-size: 13.5px }
.sheet input[type=checkbox] { accent-color: var(--accent); width: 16px; height: 16px; margin: 3px 0 0; flex: none }
.sheet .foot2 { display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-top: 20px;
  padding-top: 16px; border-top: 1px solid var(--line) }
.sheet .foot2 > span:last-child { display: flex; gap: 8px; flex: none }
.sheet .fine { font-size: 12.5px; color: var(--ink-2) }
.sheet .formerr { color: #9b2c1f; font-size: 13.5px; margin-top: 8px; min-height: 18px }
.progress { list-style: none; padding: 0; margin: 14px 0 0 }
.progress li { display: flex; justify-content: space-between; gap: 12px; padding: 9px 0; border-top: 1px solid var(--line); font-size: 14.5px }
.progress .st { font-weight: 600 } .progress .st.failed { color: #9b2c1f } .progress .detail { font-size: 12.5px; color: var(--ink-2) }
.progress li.your_turn { background: var(--hi); border-radius: 14px; padding: 10px 14px; border-top: 0 }
.progress li.your_turn .detail { color: var(--ink); font-size: 14px }
.progress .turn { margin-top: 8px; display: flex; gap: 8px }
"""

_FIELDS = (("first", "First name"), ("last", "Last name"), ("email", "Email"),
           ("phone", "Phone"), ("move_in", "Move-in date"))
_FIELD_GAP = {"first": "first name", "last": "last name", "email": "email",
              "phone": "phone", "move_in": "move-in date"}
_STEP_WHY = {
    "welcome": "Composed from your template, addressed to every resident",
    "xfinity": "Contact details, unit and move-in date; Save is pressed only after the review matches",
    "riseio": "Resident, then Move into Unit with the move-in date",
}


def _esc(v) -> str:
    return html.escape(str(v or ""), quote=True)


def _needs(field: str) -> list[str]:
    """Which steps need this field filled."""
    return [label for label, needs in triggers.STEP_REQUIREMENTS if _FIELD_GAP[field] in needs]


def build(unit: str, residents: list[dict], missing: list, checked_at: str,
          practice: bool, notes: list[str] | None = None) -> str:
    """
    The sheet for one unit. `residents` carry first, last, email, phone,
    move_in; `missing` is Result.missing ([(position, [field names])]).
    """
    gaps = {pos: set(fields) for pos, fields in missing}
    blocks = []
    for i, r in enumerate(residents, start=1):
        g = gaps.get(i, set())
        cells = []
        for key, label in _FIELDS:
            need = _FIELD_GAP[key] in g
            hint = f'<div class="hint">Needed for {" and ".join(_needs(key))}</div>' if need else ""
            placeholder = {"phone": "201-555-0100", "move_in": "MM/DD/YYYY"}.get(key, "")
            ph = f' placeholder="{placeholder}"' if placeholder else ""
            cls = "f need" if need else "f"
            value = _esc(r.get(key, ""))
            cells.append(
                f'<div class="{cls}" data-field="{key}">'
                f'<label for="r{i}_{key}">{label}</label>'
                f'<input id="r{i}_{key}" data-r="{i}" data-k="{key}" value="{value}" autocomplete="off"{ph}>'
                f'{hint}<div class="bad"></div></div>'
            )
        confirm = ""
        if "name check" in g:
            confirm = (f'<label class="confirmname"><input type="checkbox" data-namecheck="{i}"> '
                       "This name was split into first and last by a guess. I've checked both halves.</label>")
        who = "Resident" if len(residents) == 1 else f"{triggers.ordinal(i)} resident"
        blocks.append(f'<div class="resident"><h3>{who}</h3><div class="grid">{"".join(cells)}</div>{confirm}</div>')

    steps = "".join(
        f'<label><input type="checkbox" data-step="{key}" checked> <span>{_esc(label)}'
        f'<br><span class="why">{_esc(_STEP_WHY[key])}</span></span></label>'
        for key, label in (("welcome", "Welcome email"), ("xfinity", "Xfinity: add resident"),
                           ("riseio", "RiseIO: add resident and move into unit"))
    )
    activation = ('<label class="sendsmail"><input type="checkbox" data-activate> '
                  "Also send the RiseIO activation email. This emails the resident.</label>")
    note_html = "".join(f'<div class="fine">{_esc(n)}</div>' for n in (notes or []))

    if practice:
        action = '<button class="btn dark" data-sheet="confirm">Confirm and prepare</button>'
        fine = "Practice mode: runs on the practice pages, which save and send nothing."
    else:
        action = '<button class="btn dark" data-sheet="confirm">Start guided run</button>'
        fine = ("Guided: the app fills each system in its browser and stops at every "
                "Send, Save and Confirm for you to press.")

    return (
        f'<div class="sheet" data-unit="{_esc(unit)}">'
        f"<h2>Prepare unit {_esc(unit)}</h2>"
        f'<div class="verified">Verified first: not handled by a teammate as of {_esc(checked_at)}</div>'
        f"{''.join(blocks)}"
        f'<div class="steps">{steps}{activation}</div>'
        f"{note_html}"
        '<div class="formerr" id="formerr"></div>'
        f'<div class="foot2"><span class="fine">{_esc(fine)}</span>'
        f'<span><button class="btn" data-sheet="cancel">Cancel</button> {action}</span></div>'
        '<ul class="progress" id="progress"></ul>'
        "</div>"
    )


def progress_html(rows: list[tuple[str, str, str]]) -> str:
    """[(label, state, detail)] as the sheet's live progress list. A "your_turn"
    row carries the instruction and Done / Skip this step buttons."""
    words = {"waiting": "Waiting", "running": "Working…", "done": "Done", "failed": "Stopped",
             "your_turn": "Your turn"}
    out = []
    for label, state, detail in rows:
        turn = ""
        if state == "your_turn":
            turn = ('<div class="turn">'
                    '<button class="btn small dark" data-sheet="guided_done">Done</button> '
                    '<button class="btn small" data-sheet="guided_skip">Skip this step</button></div>')
        out.append(f'<li class="{state}"><span>{_esc(label)}<br><span class="detail">{_esc(detail)}</span>{turn}</span>'
                   f'<span class="st {state}">{words.get(state, state)}</span></li>')
    return "".join(out)


def validate(form: dict) -> tuple[dict | None, str]:
    """
    Check and clean what the sheet sent. Returns (clean job data, "") or
    (None, a sentence for the sheet). Field values are never echoed back.
    """
    steps = [s for s in form.get("steps", []) if s in ("welcome", "xfinity", "riseio")]
    if not steps:
        return None, "Tick at least one step."
    needs = {s: set(n) for s, n in zip(("welcome", "xfinity", "riseio"),
                                        (n for _, n in triggers.STEP_REQUIREMENTS))}
    residents = []
    for i, r in enumerate(form.get("residents", []), start=1):
        clean = {
            "first": " ".join(str(r.get("first", "")).split()),
            "last": " ".join(str(r.get("last", "")).split()),
            "email": triggers.clean_email(str(r.get("email", ""))),
            "phone": triggers.clean_phone(str(r.get("phone", ""))),
            "move_in": triggers.clean_date(str(r.get("move_in", ""))),
        }
        have = {"first name": clean["first"], "last name": clean["last"], "email": clean["email"],
                "phone": clean["phone"], "move-in date": clean["move_in"],
                "name check": not r.get("name_unconfirmed")}
        for s in steps:
            lacking = [f for f in needs[s] if not have.get(f)]
            if lacking:
                who = "the resident" if len(form.get("residents", [])) == 1 else f"the {triggers.ordinal(i)} resident"
                return None, f"{who.capitalize()} still needs: {triggers.gap_phrase(lacking).replace('no usable ', '').replace('no ', '')}."
        residents.append(clean)
    return {"unit": str(form.get("unit", "")), "residents": residents, "steps": steps,
            "send_activation_email": bool(form.get("activate"))}, ""


# The sheet's own behaviour, added to the board's script once.
SCRIPT = r"""
(function () {
  var bridge = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.triage;
  if (!bridge) return;
  var EMAIL = /^[^\s@<>]+@[^\s@<>]+\.[A-Za-z]{2,}$/, PHONE = /^\D*(1\D*)?\d{3}\D*\d{3}\D*\d{4}\D*$/,
      DATE = /^\d{1,2}\/\d{1,2}\/\d{2,4}$/;
  var NEEDS = { welcome: ['email'], xfinity: ['first', 'last', 'email', 'phone', 'move_in'],
                riseio: ['first', 'last', 'email'] };
  window.__sheet = {
    open: function (htmlText) {
      document.getElementById('sheet-host').innerHTML = htmlText;
      document.getElementById('sheetveil').classList.add('open');
    },
    progress: function (htmlText, finished) {
      document.getElementById('progress').innerHTML = htmlText;
      if (finished) {
        var foot = document.querySelector('.sheet .foot2');
        foot.innerHTML = '<span class="fine">Finished. Close to go back to the board.</span>' +
                         '<button class="btn dark" data-sheet="cancel">Close</button>';
      }
    },
    error: function (text) {
      document.getElementById('formerr').textContent = text;
      var b = document.querySelector('[data-sheet="confirm"]');
      if (b) { b.disabled = false; b.textContent = 'Confirm and prepare'; }
    },
  };
  function close() {
    document.getElementById('sheetveil').classList.remove('open');
    document.getElementById('sheet-host').innerHTML = '';
    bridge.postMessage({ action: 'sheet_closed', value: '' });
  }
  function check() {
    var ok = true, steps = [].slice.call(document.querySelectorAll('[data-step]')).filter(function (c) { return c.checked; })
      .map(function (c) { return c.dataset.step; });
    var need = {}; steps.forEach(function (s) { NEEDS[s].forEach(function (k) { need[k] = true; }); });
    document.querySelectorAll('.sheet .f').forEach(function (f) {
      var inp = f.querySelector('input'), k = inp.dataset.k, v = inp.value.trim(), msg = '';
      if (need[k] && !v) msg = 'Needed for the ticked steps';
      else if (v && k === 'email' && !EMAIL.test(v.replace(/^mailto:/i, ''))) msg = 'Enter an email like name@example.com';
      else if (v && k === 'phone' && !PHONE.test(v)) msg = 'Enter a 10-digit phone with area code';
      else if (v && k === 'move_in' && !DATE.test(v)) msg = 'Enter the date as MM/DD/YYYY';
      f.classList.toggle('err', !!msg); f.querySelector('.bad').textContent = msg; if (msg) ok = false;
    });
    var unchecked = [].slice.call(document.querySelectorAll('[data-namecheck]')).filter(function (c) { return !c.checked; });
    if (unchecked.length && steps.length) { ok = false; window.__sheet.error('Confirm the name split for every resident flagged above.'); }
    else if (!steps.length) { ok = false; window.__sheet.error('Tick at least one step.'); }
    else window.__sheet.error(ok ? '' : 'Fix the highlighted fields first.');
    return ok ? steps : null;
  }
  document.addEventListener('click', function (e) {
    var p = e.target.closest('[data-prepare]');
    if (p) { e.preventDefault(); bridge.postMessage({ action: 'prepare', value: p.dataset.prepare }); return; }
    var b = e.target.closest('[data-sheet]');
    if (!b) return;
    e.preventDefault();
    if (b.dataset.sheet === 'cancel') { close(); return; }
    if (b.dataset.sheet === 'guided_done' || b.dataset.sheet === 'guided_skip') {
      b.disabled = true;
      bridge.postMessage({ action: b.dataset.sheet, value: '' });
      return;
    }
    var steps = check(); if (!steps) return;
    var sheet = document.querySelector('.sheet'), residents = {};
    document.querySelectorAll('.sheet input[data-r]').forEach(function (inp) {
      (residents[inp.dataset.r] = residents[inp.dataset.r] || {})[inp.dataset.k] = inp.value.trim();
    });
    document.querySelectorAll('[data-namecheck]').forEach(function (c) {
      residents[c.dataset.namecheck].name_unconfirmed = !c.checked;
    });
    var form = { unit: sheet.dataset.unit, steps: steps,
                 activate: !!(document.querySelector('[data-activate]') || {}).checked,
                 residents: Object.keys(residents).sort().map(function (k) { return residents[k]; }) };
    b.disabled = true; b.textContent = 'Working…';
    bridge.postMessage({ action: 'confirm', value: JSON.stringify(form) });
  });
})();
"""


def form_from_json(value: str) -> dict:
    try:
        data = json.loads(value)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
