"""
Guided runs on the real systems: the app fills, you press the final buttons --
unless, for this unit, you ticked "let the app press" on the sheet.

The first real units go through here. For each step the app opens the system's
tab in its own browser (browser.py), fills only the fields it has mapped with
certainty, and stops at the final control -- Send, Save, Create New Resident,
Confirm Move-In -- with a "your turn" prompt on the sheet. It carries on once it
sees you pressed it, or when you press Done on the sheet.

Auto (opt-in per unit: Outlook's Send, Xfinity's Save resident and RiseIO's
Create New Resident; never Confirm Move-In). The app presses one of those only when
  - every value reads back exactly from the page (the review, or the form),
  - exactly one such button is on screen, found by its name, never by position,
  - a countdown passed without you pressing Skip step or Done (either holds it,
    and the button is yours as usual).
It presses once, never twice, then waits for the system's answer: an error
banner stops the step with the system's words; no answer stops it too, with a
note to check before trying again. Each press is logged to ACTION_LOG: time,
unit, system, outcome -- no names, emails or phones.

Mapped read-only before any real run (labels and ids, never values):

  RiseIO   "Add Resident": ui-sref="app.resident-edit"
           fields by AngularJS binding: user.firstName, user.lastName,
           user.email, user.phone; birthday.day / .month / .year selects
           (years 1923-2023); "ACCOUNT ACTIVATION" checkbox, ticked by default
           final: "Create New Resident", then a Move into Unit dialog (not seen)
  Xfinity  Prism web components. "Add resident": residents_hero_add_button
           (its inner native button takes the click); step 1 by test id:
           resident-contact-details-first-name / -last-name / -email /
           -phone-number; footer: resident-create-footer-continue-button
           later steps (unit, dates, review, Save): not seen
  Outlook  compose: To / Cc as editable areas labelled "To" / "Cc", an input
           labelled "Subject", an editable "Message body". The app opens New
           mail and fills all four -- the body from your captured template
           (welcome_template.py), above any automatic signature -- checks
           them, and leaves Send to you.

Steps not seen before are recorded as they appear -- structure only, into a
private file under LEARN_DIR -- so they can be built out after the run. On
those, a field is filled only when its label leaves no doubt (a "move in" date);
anything else is your turn, with a prompt saying exactly what to do.
"""

from __future__ import annotations

import re

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import config

LEARN_DIR = Path.home() / "Library" / "Application Support" / "MoveInTriage" / "learned"

# How long to wait for you to press a final button before the step stops.
YOUR_TURN_TIMEOUT = 15 * 60

# Auto: how long the press is announced before it is made, and how long to wait
# for the system to answer it.
AUTO_COUNTDOWN = 5
AUTO_CONFIRM_TIMEOUT = 45

# One line per press the app made. Units and outcomes only.
ACTION_LOG = LEARN_DIR.parent / "actions.log"

# At most this many wizard screens after Xfinity's first one, before giving up.
MAX_XFINITY_SCREENS = 6


@dataclass
class Hooks:
    """How a guided run talks to the app and to you. All callable from a worker thread."""

    views: dict        # site -> WKWebView (browser.view)
    show_tab: object   # show_tab(site): bring that tab forward
    progress: object   # progress(step, state, detail); state "your_turn" carries the instruction
    skipped: object    # skipped() -> True once you pressed "Skip this step"
    user_done: object  # user_done() -> True once you pressed "Done" for the current turn


class _Skipped(Exception):
    pass


# Deep helpers: find through open shadow roots, set a value the way the page's
# framework listens for it, click a component's inner native control. Values
# arrive as JSON in D; nothing typed is ever spliced into code.
_DEEP = r"""
const G = {
  all(sel, root) {
    root = root || document; const out = [...root.querySelectorAll(sel)];
    for (const e of root.querySelectorAll('*')) if (e.shadowRoot) out.push(...G.all(sel, e.shadowRoot));
    return out;
  },
  one(sel) { return G.all(sel)[0] || null; },
  shown(e) { return !!(e && (e.offsetWidth || e.offsetHeight || e.getClientRects().length)); },
  native(el) {
    if (!el) return null;
    if (/^(INPUT|SELECT|TEXTAREA|BUTTON)$/.test(el.tagName)) return el;
    return (el.shadowRoot && el.shadowRoot.querySelector('input,select,textarea,button'))
        || el.querySelector('input,select,textarea,button') || el;
  },
  type(el, value) {
    const n = G.native(el);
    if (!n) throw new Error('a field was not found');
    n.focus();
    const proto = n.tagName === 'SELECT' ? HTMLSelectElement.prototype
                : n.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(n, value);
    for (const t of ['input', 'change']) n.dispatchEvent(new Event(t, { bubbles: true, composed: true }));
    n.blur();
    if (n.value !== value) throw new Error('a field would not take its value');
  },
  pick(sel, wanted) {
    const n = G.native(sel);
    if (!n || n.tagName !== 'SELECT') throw new Error('a dropdown was not found');
    const opt = [...n.options].find(o => o.text.trim().toLowerCase() === String(wanted).toLowerCase());
    if (!opt) throw new Error('a dropdown has no option ' + wanted);
    n.value = opt.value;
    n.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
  },
  press(el) { const n = G.native(el); if (!n) throw new Error('a button was not found'); n.click(); },
  text(el) { return (el && (el.innerText || el.textContent) || '').replace(/\s+/g, ' ').trim(); },
  // What the current screen consists of: visible fields and button names, no values.
  print() {
    return G.all('input,select,textarea,prism-input-text,prism-select,prism-button,button,[ng-model],[data-ng-model]')
      .filter(G.shown)
      .map(e => (e.getAttribute('data-testid') || e.getAttribute('ng-model') || e.getAttribute('data-ng-model') || e.getAttribute('label')
                 || e.getAttribute('aria-label') || e.tagName) + (/BUTTON/.test(e.tagName) ? ':' + G.text(e).slice(0, 20) : ''))
      .join('|');
  },
};
"""


def _script(body: str, data: dict | None = None) -> str:
    return ("(() => {" + _DEEP + f"const D = {json.dumps(data or {})};"
            + "try {" + body + "} catch (e) { return JSON.stringify({ ok: false, detail: String(e.message || e) }); }"
            + "})()")


# Structure of whatever is showing, for learning an unseen step: labels only
# when made of fixed form words (else masked), test ids, bindings, button names.
_LEARN = r"""
const WORDS = /^(?:first|last|name|email|phone|mobile|number|unit|units|apartment|select|choose|move|in|into|out|date|dates|start|end|lease|optional|required|step|of|contact|details|resident|review|save|continue|next|back|cancel|close|confirm|create|add|the|a|and|\(|\)|\*|:|-|\d|\s)+$/i;
const mask = s => (s || '').replace(/\s+/g, ' ').trim().replace(/[A-Za-z]/g, 'a').replace(/\d/g, '#').slice(0, 40);
const safe = t => { t = (t || '').replace(/\s+/g, ' ').trim(); return t && t.length <= 50 && WORDS.test(t) ? t : (t ? '~' + mask(t) : ''); };
const items = G.all('input,select,textarea,[contenteditable="true"],prism-input-text,prism-select,prism-button,button,[ng-model],[data-ng-model]')
  .filter(G.shown).map(e => {
    const tag = e.tagName.toLowerCase(), button = /button/.test(tag);
    return { tag, button, type: e.getAttribute('type') || '',
             label: safe(button ? (e.getAttribute('aria-label') || G.text(e))
                                : (e.getAttribute('label') || e.getAttribute('aria-label') || e.getAttribute('placeholder') || e.getAttribute('name'))),
             testid: e.getAttribute('data-testid') || '', model: e.getAttribute('ng-model') || e.getAttribute('data-ng-model') || '',
             options: tag === 'select' ? e.options.length : undefined };
  });
return JSON.stringify({ ok: true, path: location.pathname.replace(/[0-9a-f-]{8,}|\d+/gi, '#'), items: items.slice(0, 60) });
"""

# Field whose own label or test id says "move in": the only kind filled on an
# unseen screen, because there is no doubt what goes in it.
_MOVE_IN_FIELD = r"""
const f = G.all('prism-input-text, input').filter(G.shown).find(e =>
  /move.?in/i.test([e.getAttribute('label'), e.getAttribute('aria-label'), e.getAttribute('placeholder'),
                    e.getAttribute('name'), e.getAttribute('data-testid')].join(' ')));
let dated = false;
if (f && D.movein) {
  const n = G.native(f);
  const value = n.type === 'date' ? D.movein.replace(/^(\d\d)\/(\d\d)\/(\d{4})$/, '$3-$1-$2') : D.movein;
  try { G.type(f, value); dated = true; } catch (e) { dated = false; }
}
return JSON.stringify({ ok: true, dated });
"""


# Xfinity's Add resident wizard, as mapped on the live site: details, then a
# unit search ("Search by unit name or address", data-testid unit-test) whose
# results are option buttons named like "APT 317" over the street address,
# then a move-in date picker (resident-occupancy-dates-move-in-date, MM/DD/YYYY)
# with an optional move-out date, then the review with Save.
_XF_KIND = r"""
const unit = G.one('input[data-testid="unit-test"]');
const date = G.one('[data-testid="resident-occupancy-dates-move-in-date"]');
const save = G.all('prism-button,button').filter(G.shown).some(b => /^save( resident)?$/i.test(G.text(b)));
return JSON.stringify({ ok: true, kind: unit && G.shown(unit) ? 'unit' : date && G.shown(date) ? 'dates'
                                        : save ? 'review' : 'other' });
"""

# Type the unit as Xfinity names it (no leading zeros) into the search.
_XF_UNIT_TYPE = r"""
const n = G.one('input[data-testid="unit-test"]');
if (!n) throw new Error('could not find the unit search');
n.focus();
Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(n, D.unit);
n.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
n.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, composed: true, key: D.unit.slice(-1) }));
return JSON.stringify({ ok: true });
"""

# The one result that is exactly this unit: "APT 317" for 317, never "APT 1317".
_XF_UNIT_EXACT = (
    "G.all('button[role=option]').filter(b => new RegExp('^(apt|unit|#)?\\\\s*0*' + D.unit + '$', 'i')"
    ".test(G.text(b.querySelector('prism-text') || b))).length"
)
_XF_UNIT_PICK = r"""
const re = new RegExp('^(apt|unit|#)?\\s*0*' + D.unit + '$', 'i');
const first = b => G.text(b.querySelector('prism-text') || b);
const exact = G.all('button[role=option]').filter(b => re.test(first(b)));
if (exact.length !== 1) return JSON.stringify({ ok: true, picked: '', matches: exact.length });
exact[0].click();
return JSON.stringify({ ok: true, picked: first(exact[0]),
  selected: exact[0].getAttribute('aria-selected') === 'true' || exact[0].getAttribute('aria-checked') === 'true' });
"""

# The move-in date, typed into the picker's own text box; move-out left empty.
_XF_MOVE_IN = r"""
const dp = G.one('[data-testid="resident-occupancy-dates-move-in-date"]');
if (!dp) throw new Error('could not find the move-in date');
const n = G.all('input', dp.shadowRoot || dp).concat(G.all('input', dp))[0];
if (!n) throw new Error('could not find the move-in date box');
n.focus();
Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(n, D.movein);
for (const t of ['input', 'change']) n.dispatchEvent(new Event(t, { bubbles: true, composed: true }));
n.blur();
return JSON.stringify({ ok: true, value: n.value });
"""

# One step toward your property's residents page. In order: a property's
# own page -> its residents page; the residents page remembered from a run;
# the property list -> the one property card there, or the one whose name
# matches yours. Several that match, or none: stop and say so, never guess.
# Cards (mapped live) are focusable boxes with no link, showing the property
# name, its address and Units / Residents counts.
_XF_TO_RESIDENTS = r"""
const m = location.pathname.match(/^(\/properties\/[^\/]+)(\/.*)?$/);
if (m && !/^\/residents\/?$/.test(m[2] || '')) {
  location.assign(location.origin + m[1] + '/residents');
  return JSON.stringify({ ok: true, step: 'property page' });
}
if (D.saved && location.pathname !== D.saved) {
  location.assign(location.origin + D.saved);
  return JSON.stringify({ ok: true, step: 'remembered' });
}
const cards = G.all('div[tabindex="0"]').filter(G.shown)
  .filter(c => { const t = G.text(c); return /units/i.test(t) && /residents/i.test(t) && G.all('prism-text', c).length; });
if (!cards.length) return JSON.stringify({ ok: true, step: 'wait' });
let pick = cards;
if (cards.length > 1) {
  const words = String(D.prop || '').toLowerCase().split(/[^a-z0-9]+/).filter(w => w.length > 2 && w !== 'the');
  const name = c => G.text(G.all('prism-text', c)[0]).toLowerCase();
  pick = words.length ? cards.filter(c => words.some(w => name(c).includes(w))) : [];
  if (pick.length !== 1)
    throw new Error(cards.length + ' properties are listed in Xfinity and ' + (pick.length ? pick.length + ' match' : 'none matches')
                    + ' your property name in Settings; open yours once and the app will remember it');
}
pick[0].click();
return JSON.stringify({ ok: true, step: 'property card' });
"""

# Auto: does the review show every value? Names the fields that are missing,
# never the values.
_XF_READBACK = r"""
const t = (document.body.innerText + ' ' + G.all('prism-text').map(G.text).join(' ')).toLowerCase();
const missing = [];
if (!t.includes(D.first.toLowerCase())) missing.push('first name');
if (!t.includes(D.last.toLowerCase())) missing.push('last name');
if (!t.includes(D.email.toLowerCase())) missing.push('email');
const p = D.phone.replace(/[^0-9]/g, '').slice(-10);
if (p.length !== 10 || !new RegExp(p.split('').join('[^0-9a-z]{0,2}')).test(t)) missing.push('phone');
if (!new RegExp('(^|[^0-9])0*' + D.unit + '([^0-9]|$)').test(t)) missing.push('unit');
const m = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(D.movein);
let dated = false;
if (m) {
  const mo = +m[1], d = +m[2], y = m[3];
  const names = ['january','february','march','april','may','june','july','august','september','october','november','december'];
  const full = names[mo - 1] || '';
  dated = [D.movein, mo + '/' + d + '/' + y, full + ' ' + d + ', ' + y, full.slice(0, 3) + ' ' + d + ', ' + y,
           d + ' ' + full + ' ' + y].some(c => c && t.includes(c));
}
if (!dated) missing.push('move-in date');
return JSON.stringify({ ok: true, missing });
"""

# Auto: press Save resident -- the one visible button by that name, else nothing.
_XF_PRESS_SAVE = r"""
const re = /^save( resident)?$/i;
const hosts = G.all('prism-button').filter(G.shown).filter(b => re.test(G.text(b)));
const pool = hosts.length ? hosts : G.all('button').filter(G.shown).filter(b => re.test(G.text(b)));
if (pool.length !== 1) return JSON.stringify({ ok: true, pressed: false, n: pool.length });
G.press(pool[0]);
return JSON.stringify({ ok: true, pressed: true });
"""

_RISE_FIELD = "G.one('[ng-model=\"' + m + '\"],[data-ng-model=\"' + m + '\"]')"

# Auto: does the RiseIO form hold exactly what was typed and picked?
_RISE_READBACK = r"""
const val = m => {
  const n = G.native(""" + _RISE_FIELD + r""");
  if (!n) return '';
  return (n.tagName === 'SELECT' ? ((n.options[n.selectedIndex] || {}).text || '') : n.value).trim();
};
const missing = [];
const same = (m, want, label) => { if (val(m).toLowerCase() !== String(want).toLowerCase()) missing.push(label); };
same('user.firstName', D.first, 'first name');
same('user.lastName', D.last, 'last name');
same('user.email', D.email, 'email');
if (D.phone && !val('user.phone').replace(/[^0-9]/g, '').endsWith(D.phone.replace(/[^0-9]/g, '').slice(-10))) missing.push('phone');
same('birthday.day', D.day, 'birthday');
same('birthday.month', D.month, 'birthday');
same('birthday.year', D.year, 'birthday');
const box = G.all('input[type=checkbox]').find(c => /account\s+activation/i.test(
  G.text(c.closest('label')) || G.text(c.parentElement && c.parentElement.parentElement)));
if (!box || box.checked !== D.activate) missing.push('account activation');
return JSON.stringify({ ok: true, missing: [...new Set(missing)] });
"""

# Auto: press Create New Resident -- the one visible control by that name
# (on the live page a div whose ng-click is postUser), else nothing.
_RISE_PRESS_CREATE = r"""
const re = /^create new resident$/i;
const bound = G.all('[ng-click*="postUser"],[data-ng-click*="postUser"]').filter(G.shown).filter(e => re.test(G.text(e)));
const pool = bound.length ? bound : G.all('button').filter(G.shown).filter(e => re.test(G.text(e)));
if (pool.length !== 1) return JSON.stringify({ ok: true, pressed: false, n: pool.length });
pool[0].click();
return JSON.stringify({ ok: true, pressed: true });
"""

# How RiseIO's message reads: a plain success, or an error. Anything else is
# not taken as success.
_RISE_SAID_OK = re.compile(r"\b(successfully|resident (was )?(created|added)|created|saved)\b", re.I)
_RISE_SAID_ERROR = re.compile(r"\b(error|fail|invalid|required|already|exists|not|unable|could)\b", re.I)

# Anything RiseIO shows as an alert or error, by its words.
_RISE_ALERT = ("return JSON.stringify({ ok: true, text: G.all('[role=alert],.alert-danger,.toast-error,.md-toast-content,"
               ".error-message,.help-block.error').filter(G.shown).map(G.text).filter(Boolean)[0] || '' });")


class Guided:
    """One guided unit across the systems, in the app's browser."""

    def __init__(self, hooks: Hooks, wait: float = 20.0, turn_timeout: float = YOUR_TURN_TIMEOUT,
                 countdown: float = AUTO_COUNTDOWN, confirm_timeout: float = AUTO_CONFIRM_TIMEOUT):
        self.h = hooks
        self.wait = wait
        self.turn_timeout = turn_timeout
        self.countdown = countdown
        self.confirm_timeout = confirm_timeout

    # -- plumbing --------------------------------------------------------------

    def _js(self, site: str, body: str, data: dict | None = None) -> dict:
        import outlook_web

        out = outlook_web.run_js(self.h.views[site], _script(body, data))
        return out if isinstance(out, dict) else {"ok": False, "detail": "the page did not answer"}

    def _do(self, site: str, body: str, data: dict | None = None) -> None:
        r = self._js(site, body + " return JSON.stringify({ ok: true });", data)
        if not r.get("ok"):
            raise RuntimeError(r.get("detail", "the page did not respond"))

    def _yes(self, site: str, test: str, data: dict | None = None) -> bool:
        return bool(self._js(site, f"return JSON.stringify({{ ok: true, yes: !!({test}) }});", data).get("yes"))

    def _until(self, site: str, test: str, what: str, timeout: float | None = None,
               data: dict | None = None, allow_done: bool = False) -> None:
        import outlook_web

        end = time.monotonic() + (timeout or self.wait)
        while time.monotonic() < end:
            if self._yes(site, test, data):
                return
            if self.h.skipped():
                raise _Skipped()
            if allow_done and self.h.user_done():
                return
            outlook_web.pause(0.5)
        raise TimeoutError(f"timed out waiting for {what}")

    def _your_turn(self, site: str, step: str, instruction: str, done_test: str = "false",
                   data: dict | None = None) -> None:
        """Hand over: say what to check and press, then wait until it is done
        on the page, or until you press Done on the sheet."""
        self.h.progress(step, "your_turn", instruction)
        self._until(site, done_test, "you", timeout=self.turn_timeout, data=data, allow_done=True)
        self.h.progress(step, "running", "Carrying on.")

    def _print(self, site: str) -> str:
        return str(self._js(site, "return JSON.stringify({ ok: true, p: G.print() });").get("p", ""))

    def learn(self, site: str, stage: str) -> None:
        """Record what is showing (structure only, no values) for later build-out."""
        r = self._js(site, _LEARN)
        if not r.get("ok"):
            return
        LEARN_DIR.mkdir(parents=True, exist_ok=True)
        path = LEARN_DIR / f"{site}-{stage}.json"
        fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"site": site, "stage": stage, "path": r.get("path"), "items": r.get("items")}, fh, indent=2)

    # -- auto -------------------------------------------------------------------

    @staticmethod
    def _auto(job, site: str) -> bool:
        return site in (getattr(job, "auto_final", None) or [])

    @staticmethod
    def _log(job, site: str, what: str) -> None:
        """One line per press the app made: time, unit, system, outcome."""
        path = Path(ACTION_LOG)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\tunit {job.unit}\t{site}\t{what}\n")

    def _go_ahead(self, step: str, what: str) -> bool:
        """Announce the press and count down. False if you pressed Skip step
        or Done meanwhile: the press is held and the button is yours."""
        import math

        import outlook_web

        end = time.monotonic() + self.countdown
        while (left := end - time.monotonic()) > 0:
            self.h.progress(step, "auto", f"Everything reads back exactly. Pressing {what} in "
                                          f"{math.ceil(left)} s. Press Skip step to stop and press it yourself.")
            tick = time.monotonic() + min(1.0, left)
            while time.monotonic() < tick:
                if self.h.skipped() or self.h.user_done():
                    self.h.progress(step, "running", "Held. The button is yours.")
                    return False
                outlook_web.pause(0.1)
        self.h.progress(step, "running", f"Pressing {what}.")
        return True

    @staticmethod
    def _who(job, position: int) -> str:
        return "the resident" if len(job.residents) == 1 else f"resident {position}"

    # -- RiseIO ----------------------------------------------------------------

    # The Move into Unit dialog: a dialog that says so. Found live: any visible
    # dialog-like element counted, and RiseIO's own page has one, so a press
    # was logged "created" in the same second, unconfirmed.
    _RISE_DIALOG = "G.all('[role=dialog], md-dialog, .modal').filter(G.shown).some(d => /move/i.test(G.text(d)))"

    def riseio(self, job) -> str:
        """Fill Add Resident; you press Create New Resident, then Confirm Move-In."""
        bday = getattr(config, "PLACEHOLDER_BIRTHDAY", {"day": 1, "month": "January", "year": 1923})
        by_app = 0
        for position, r in enumerate(job.residents, start=1):
            who = self._who(job, position)
            self.h.show_tab("riseio")
            add_shown = "G.all('[ui-sref=\"app.resident-edit\"]').some(e => G.shown(e) && /add\\s+resident/i.test(G.text(e)))"
            if not self._yes("riseio", add_shown):
                self._do("riseio", "location.assign(location.origin + '/app/resident');")
            self._until("riseio", add_shown, "the RiseIO residents page")
            self._do("riseio", "G.all('[ui-sref=\"app.resident-edit\"]').find(e => /add\\s+resident/i.test(G.text(e))).click();")
            self._until("riseio", "G.one('[ng-model=\"user.firstName\"],[data-ng-model=\"user.firstName\"]')", "the Add Resident form")
            self._do(
                "riseio",
                "G.type(G.one('[ng-model=\"user.firstName\"],[data-ng-model=\"user.firstName\"]'), D.first);"
                "G.type(G.one('[ng-model=\"user.lastName\"],[data-ng-model=\"user.lastName\"]'), D.last);"
                "G.type(G.one('[ng-model=\"user.email\"],[data-ng-model=\"user.email\"]'), D.email);"
                "if (D.phone) G.type(G.one('[ng-model=\"user.phone\"],[data-ng-model=\"user.phone\"]'), D.phone);"
                "G.pick(G.one('[ng-model=\"birthday.day\"],[data-ng-model=\"birthday.day\"]'), D.day);"
                "G.pick(G.one('[ng-model=\"birthday.month\"],[data-ng-model=\"birthday.month\"]'), D.month);"
                "G.pick(G.one('[ng-model=\"birthday.year\"],[data-ng-model=\"birthday.year\"]'), D.year);"
                # The activation box: the checkbox whose surrounding label reads ACCOUNT ACTIVATION.
                "const box = G.all('input[type=checkbox]').find(c => /account\\s+activation/i.test("
                "  G.text(c.closest('label')) || G.text(c.parentElement && c.parentElement.parentElement)));"
                "if (!box) throw new Error('could not find the account activation checkbox');"
                "if (box.checked !== D.activate) box.click();"
                "if (box.checked !== D.activate) throw new Error('the account activation checkbox would not change');",
                {"first": r["first"], "last": r["last"], "email": r["email"], "phone": r.get("phone", ""),
                 "day": str(bday["day"]), "month": bday["month"], "year": str(bday["year"]),
                 "activate": bool(job.send_activation_email)})
            mail = ("is ticked, so RiseIO will email them an activation link"
                    if job.send_activation_email else "is unticked, so no email goes to them")
            note, created = "", False
            if self._auto(job, "riseio"):
                missing = self._js("riseio", _RISE_READBACK, {
                    "first": r["first"], "last": r["last"], "email": r["email"], "phone": r.get("phone", ""),
                    "day": str(bday["day"]), "month": bday["month"], "year": str(bday["year"]),
                    "activate": bool(job.send_activation_email)}).get("missing", ["the form"])
                if missing:
                    note = f"The form did not read back exactly ({', '.join(missing)}), so this one is yours. "
                elif self._go_ahead("riseio", "Create New Resident"):
                    self._riseio_create(job, who)
                    by_app += 1
                    created = True
            if not created:
                self._your_turn(
                    "riseio", "riseio",
                    note + f"Check {who}'s details in the RiseIO tab. Account activation {mail}. "
                    "Then press Create New Resident.",
                    f"!G.one('[ng-model=\"user.firstName\"],[data-ng-model=\"user.firstName\"]') || {self._RISE_DIALOG}")
            # The Move into Unit dialog has not been seen: record it, fill only a
            # date whose label says move-in, and hand over.
            try:
                self._until("riseio", self._RISE_DIALOG, "the Move into Unit dialog", timeout=20)
            except TimeoutError:
                self._your_turn("riseio", "riseio",
                                f"Open Move into Unit for {who} and choose unit {job.unit}, "
                                "then press Confirm Move-In. Press Done here when finished.")
                continue
            self.learn("riseio", "move-into-unit")
            dated = self._js("riseio", _MOVE_IN_FIELD, {"movein": r.get("move_in", "")}).get("dated")
            self._your_turn(
                "riseio", "riseio",
                f"In Move into Unit, choose unit {job.unit}. "
                + ("The move-in date is filled in. " if dated else f"Enter the move-in date {r.get('move_in', '')}. ")
                + "Check it, then press Confirm Move-In.",
                f"!{self._RISE_DIALOG}")
        n = len(job.residents)
        who_pressed = ("you pressed the final buttons" if not by_app else
                       "the app pressed Create New Resident, you confirmed each move-in" if by_app == n else
                       f"the app pressed Create New Resident for {by_app}, you for {n - by_app}; you confirmed each move-in")
        return (f"{n} resident(s) created and moved in; {who_pressed}"
                + (", activation email sent" if job.send_activation_email else ", no activation email"))

    def _riseio_create(self, job, who: str) -> None:
        """Press Create New Resident once, then wait for RiseIO's answer."""
        import outlook_web

        got = self._js("riseio", _RISE_PRESS_CREATE)
        if not got.get("pressed"):
            raise RuntimeError(f"Create New Resident was not pressed for {who}: "
                               f"{got.get('n', 0)} buttons by that name were showing, not one. Nothing was created.")
        self._log(job, "riseio", "pressed Create New Resident")
        form = "G.one('[ng-model=\"user.firstName\"],[data-ng-model=\"user.firstName\"]')"
        end = time.monotonic() + self.confirm_timeout
        while time.monotonic() < end:
            if self._yes("riseio", f"!({form} && G.shown({form})) || {self._RISE_DIALOG}"):
                self._log(job, "riseio", "created")
                return
            said = self._js("riseio", _RISE_ALERT).get("text")
            if said and _RISE_SAID_OK.search(said) and not _RISE_SAID_ERROR.search(said):
                self._log(job, "riseio", "created (RiseIO said so)")
                return
            if said:
                self._log(job, "riseio", "refused")
                raise RuntimeError(f"RiseIO did not confirm {who}: it said \"{said}\". "
                                   "It was pressed once and not again. Check the details, and whether "
                                   "this resident already exists in RiseIO.")
            outlook_web.pause(0.5)
        self._log(job, "riseio", "no answer")
        raise RuntimeError(f"RiseIO did not answer Create New Resident for {who} within "
                           f"{int(self.confirm_timeout)} seconds. It was pressed once and not again; "
                           "check the residents list before trying again.")

    # -- Xfinity ---------------------------------------------------------------

    _XF = "G.one('[data-testid=\"resident-contact-details-{}\"]')"
    # The review's final button: "Save resident" on the live wizard.
    _XF_SAVE = "G.all('prism-button,button').filter(G.shown).some(b => /^save( resident)?$/i.test(G.text(b)))"
    # Shown when Xfinity refuses a save ("An unknown error occurred...").
    _XF_ERROR = "G.all('[data-testid=\"res-create-error-banner\"]').some(G.shown)"
    _XF_ERROR_TEXT = ("return JSON.stringify({ ok: true, text: G.all('[data-testid=\"res-create-error-banner-description\"]')"
                      ".filter(G.shown).map(G.text)[0] || G.all('[data-testid=\"res-create-error-banner\"]').filter(G.shown).map(G.text)[0] || '' });")
    _XF_OPEN = "G.all('[data-testid^=\"resident-create-footer\"]').some(G.shown)"

    def xfinity(self, job) -> str:
        """Fill step 1, continue; each later screen is recorded and handed to you; you press Save."""
        by_app = 0
        for position, r in enumerate(job.residents, start=1):
            who = self._who(job, position)
            self.h.show_tab("xfinity")
            # The tab is often on the property's overview page; its residents
            # page is the same address plus /residents (the "Manage residents"
            # link). Go there if the Add resident button isn't showing.
            import settings

            self._xfinity_to_residents()
            here = self._js("xfinity", "return JSON.stringify({ ok: true, path: location.pathname });").get("path", "")
            settings.remember_xfinity_residents(here)
            self._do("xfinity", "G.press(G.one('prism-button[data-testid=\"residents_hero_add_button\"]'));")
            self._until("xfinity", self._XF.format("first-name"), "the Add resident wizard")
            self._do("xfinity",
                     "G.type(" + self._XF.format("first-name") + ", D.first);"
                     "G.type(" + self._XF.format("last-name") + ", D.last);"
                     "G.type(" + self._XF.format("email") + ", D.email);"
                     "G.type(" + self._XF.format("phone-number") + ", D.phone);",
                     {"first": r["first"], "last": r["last"], "email": r["email"], "phone": r["phone"]})
            # Continue on a filled first screen is not a final button.
            before = self._print("xfinity")
            self._do("xfinity", "G.press(G.one('prism-button[data-testid=\"resident-create-footer-continue-button\"]'));")
            self._until("xfinity", "G.print() !== D.before", "the next screen", data={"before": before})

            # The later screens: the unit and the move-in date are filled and
            # continued when they can be verified; anything else, or anything
            # ambiguous, is recorded and handed to you, up to the review's Save.
            bare = job.unit.lstrip("0") or "0"
            for screen in range(2, 2 + MAX_XFINITY_SCREENS):
                # Found live: right after Continue the next screen is drawn
                # in stages (footer first, the unit search a moment later).
                # Give it time to become a screen we know before deciding.
                kind = self._settled_kind()
                self.learn("xfinity", f"screen-{screen}")
                if kind == "unit" and self._xfinity_unit(bare):
                    continue
                if kind == "dates" and self._xfinity_dates(r.get("move_in", "")):
                    continue
                if self._yes("xfinity", self._XF_SAVE):
                    if self._auto(job, "xfinity"):
                        missing = self._js("xfinity", _XF_READBACK, {
                            "first": r["first"], "last": r["last"], "email": r["email"], "phone": r["phone"],
                            "unit": bare, "movein": r.get("move_in", "")}).get("missing", ["the review"])
                        if not missing and self._go_ahead("xfinity", "Save resident"):
                            self._xfinity_save(job, who)
                            by_app += 1
                            break
                        note = (f"The review did not read back exactly ({', '.join(missing)}), so this one is yours. "
                                if missing else "")
                    else:
                        note = ""
                    has = self._js("xfinity",
                                   "const t = document.body.innerText + ' ' + G.all('prism-text').map(G.text).join(' ');"
                                   "const unit = new RegExp('(^|\\\\D)0*' + D.unit + '(\\\\D|$)').test(t);"
                                   "return JSON.stringify({ ok: true, n: (t.includes(D.email) ? 1 : 0) + (unit ? 1 : 0) });",
                                   {"email": r["email"], "unit": bare}).get("n", 0)
                    self._your_turn(
                        "xfinity", "xfinity",
                        note + ("The review shows the email and unit. " if has == 2
                         else "Check the email and unit on the review carefully. ")
                        + f"If everything for {who} matches, press Save resident yourself.",
                        f"!{self._XF_OPEN} || {self._XF_ERROR}")
                    # Xfinity refused it: stop here with its own words, rather
                    # than taking the error for a new screen.
                    if self._yes("xfinity", self._XF_ERROR):
                        said = self._js("xfinity", self._XF_ERROR_TEXT).get("text") or "an error"
                        raise RuntimeError(f"Xfinity did not save {who}: it said \"{said}\". "
                                           "Nothing more was tried. Check the details and the move-in date, "
                                           "and whether this resident already exists in Xfinity.")
                    break
                dated = self._js("xfinity", _MOVE_IN_FIELD, {"movein": r["move_in"]}).get("dated")
                here = self._print("xfinity")
                self._your_turn(
                    "xfinity", "xfinity",
                    f"Complete this screen for {who}: choose unit {bare} (APT {bare}) if it asks, "
                    + ("the move-in date is filled in, " if dated else f"use move-in date {r['move_in']} if it asks, ")
                    + "leave move-out empty unless you know it. Then press Continue.",
                    f"G.print() !== D.here || !{self._XF_OPEN}", data={"here": here})
                if not self._yes("xfinity", self._XF_OPEN):
                    break
            else:
                raise RuntimeError("the wizard went on longer than expected; finish it by hand")
        n = len(job.residents)
        return f"{n} resident(s) added; " + (
            "you pressed Save" if not by_app else
            "the app pressed Save resident after each review read back exactly" if by_app == n else
            f"the app pressed Save resident for {by_app}, you for {n - by_app}")

    def _xfinity_save(self, job, who: str) -> None:
        """Press Save resident once, then wait for Xfinity's answer."""
        import outlook_web

        got = self._js("xfinity", _XF_PRESS_SAVE)
        if not got.get("pressed"):
            raise RuntimeError(f"Save resident was not pressed for {who}: "
                               f"{got.get('n', 0)} buttons by that name were showing, not one. Nothing was saved.")
        self._log(job, "xfinity", "pressed Save resident")
        end = time.monotonic() + self.confirm_timeout
        while time.monotonic() < end:
            if self._yes("xfinity", self._XF_ERROR):
                said = self._js("xfinity", self._XF_ERROR_TEXT).get("text") or "an error"
                self._log(job, "xfinity", "refused")
                raise RuntimeError(f"Xfinity did not save {who}: it said \"{said}\". "
                                   "It was pressed once and not again. Check the details and the move-in date, "
                                   "and whether this resident already exists in Xfinity.")
            if not self._yes("xfinity", self._XF_OPEN):
                self._log(job, "xfinity", "saved")
                return
            outlook_web.pause(0.5)
        self._log(job, "xfinity", "no answer")
        raise RuntimeError(f"Xfinity did not answer Save resident for {who} within "
                           f"{int(self.confirm_timeout)} seconds. It was pressed once and not again; "
                           "check the residents list before trying again.")

    # Add resident, drawn: found live, while the window is in the background
    # the component exists but its inner button is not drawn yet, and a click
    # on the empty shell does nothing. Ready only once the inner button is there.
    _XF_ADD = "(b => !!b && G.native(b) !== b)(G.one('prism-button[data-testid=\"residents_hero_add_button\"]'))"

    def _xfinity_to_residents(self) -> None:
        """From anywhere in Xfinity to your property's residents page, without
        you: found live, the tab opens on the organisation's property list
        after a restart."""
        import outlook_web
        import settings

        data = {"saved": settings.xfinity_residents(),
                "prop": getattr(config, "PROPERTY_NAME", "") or settings.load()["property"]["name"]}
        for _ in range(4):
            if self._yes("xfinity", self._XF_ADD):
                return
            before = self._js("xfinity", "return JSON.stringify({ ok: true, at: location.href + G.print() });").get("at", "")
            r = self._js("xfinity", _XF_TO_RESIDENTS, data)
            if not r.get("ok"):
                raise RuntimeError(r.get("detail", "could not reach the Xfinity residents page"))
            if r.get("step") == "wait":
                outlook_web.pause(1.0)
                continue
            # A page change, or the residents page itself, before the next step.
            self._until("xfinity",
                        f"{self._XF_ADD} || (location.href + G.print()) !== D.before",
                        "Xfinity to open the next page", timeout=30, data={"before": before})
            outlook_web.pause(1.0)
        self._until("xfinity", self._XF_ADD,
                    "the Xfinity residents page to finish drawing (it draws only while the Xfinity tab is on screen)",
                    timeout=30)

    def _settled_kind(self, timeout: float = 8.0) -> str:
        """The wizard screen showing: unit, dates or review, once it is drawn;
        "other" if it is still none of those after `timeout` seconds."""
        import outlook_web

        end = time.monotonic() + timeout
        while True:
            kind = self._js("xfinity", _XF_KIND).get("kind", "other")
            if kind != "other" or time.monotonic() >= end:
                return kind
            outlook_web.pause(0.3)

    def _xfinity_continue(self) -> None:
        """Press Continue (not a final button) and wait for the next screen."""
        before = self._print("xfinity")
        self._do("xfinity", "G.press(G.one('prism-button[data-testid=\"resident-create-footer-continue-button\"]'));")
        self._until("xfinity", "G.print() !== D.before", "the next screen", data={"before": before})

    def _xfinity_unit(self, bare: str) -> bool:
        """Search the unit and choose the one exact match. False (hand it to
        you) when there is not exactly one, or the choice didn't take."""
        self._do("xfinity", _XF_UNIT_TYPE, {"unit": bare})
        try:
            self._until("xfinity", f"({_XF_UNIT_EXACT}) >= 1", "the unit search results",
                        timeout=15, data={"unit": bare})
        except TimeoutError:
            return False
        got = self._js("xfinity", _XF_UNIT_PICK, {"unit": bare})
        if not got.get("picked"):
            return False
        # Found live: the chosen option does not always report itself selected,
        # though Continue takes it. The proof is the wizard moving on to the
        # dates; still on the unit screen (or no move at all) = yours to do.
        try:
            self._xfinity_continue()
        except TimeoutError:
            return False
        return self._settled_kind() != "unit"

    def _xfinity_dates(self, move_in: str) -> bool:
        """Type the move-in date (MM/DD/YYYY) and continue; move-out stays
        empty. False (hand it to you) if the box would not take the date."""
        if not re.fullmatch(r"\d{2}/\d{2}/\d{4}", move_in or ""):
            return False
        try:
            got = self._js("xfinity", _XF_MOVE_IN, {"movein": move_in})
        except RuntimeError:
            return False
        if got.get("value") != move_in:
            return False
        self._xfinity_continue()
        return True

    # -- Outlook welcome ---------------------------------------------------------

    # The compose window, as mapped read-only: To and Cc are editable areas
    # labelled "To" / "Cc", Subject an input labelled "Subject", the body an
    # editable "Message body" box. The app opens it and fills it; Send is yours.
    _OW_BODY = "G.all('[contenteditable=\"true\"][aria-label=\"Message body\"]').find(G.shown)"
    _OW_IN_PROGRESS = ("(() => { const b = " + _OW_BODY + "; if (!b) return false;"
                       " const subj = G.all('input').find(i => G.shown(i) && i.getAttribute('aria-label') === 'Subject');"
                       " const wells = G.all('[contenteditable=\"true\"]').filter(e => G.shown(e) && /^(To|Cc|Bcc)$/.test(e.getAttribute('aria-label') || ''));"
                       " return !!(subj && subj.value.trim()) || wells.some(w => w.innerText.replace(/[\\u200b\\s]/g, ''))"
                       " || b.innerText.trim().length > 40; })()")

    # Recipients, as mapped live: To and Cc are editable areas; a typed
    # address becomes a recipient (a "_Entity" span labelled with the address)
    # when its suggestion is picked. Typed with a trailing semicolon it stays
    # loose text that Outlook flags as an error, and for a directory address
    # an early Enter does nothing (the suggestions arrive a moment later) --
    # both found live. So: type one address, click the suggestion that holds
    # exactly that address, Enter only if none comes, and wait until it shows
    # as a recipient.
    _OW_WELL = ("const well = G.all('[contenteditable=\"true\"]').find(e => G.shown(e) && "
                "e.getAttribute('aria-label') === D.label);")
    _OW_ADD = _OW_WELL + r"""
if (!well) throw new Error('could not find the ' + D.label + ' field');
well.focus();
const sel = window.getSelection(); sel.selectAllChildren(well); sel.collapseToEnd();
document.execCommand('insertText', false, D.addr);
"""
    # The suggestion holding exactly this address, clicked. Never a near match.
    _OW_PICK = r"""
const want = D.addr.toLowerCase();
const hits = G.all('[role="option"]').filter(G.shown).filter(o => !o.closest('[aria-label="Message list"]'))
  .filter(o => G.text(o).toLowerCase().split(/[\s<>()"';,]+/).includes(want));
if (hits.length !== 1) return JSON.stringify({ ok: true, picked: false, n: hits.length });
hits[0].click();
return JSON.stringify({ ok: true, picked: true });
"""
    _OW_ENTER = _OW_WELL + r"""
if (!well) throw new Error('could not find the ' + D.label + ' field');
well.focus();
for (const t of ['keydown', 'keypress', 'keyup'])
  well.dispatchEvent(new KeyboardEvent(t, { key: 'Enter', code: 'Enter', keyCode: 13, which: 13,
                                            bubbles: true, cancelable: true, composed: true }));
"""
    # The recipients in a field, and whether loose text is left over. Found
    # live: an outside address's entry is labelled with the address; someone
    # in your directory's entry carries only their name, the address nowhere
    # on the page.
    _OW_RECIPIENTS = _OW_WELL + r"""
if (!well) return JSON.stringify({ ok: true, found: [], count: 0, loose: false });
const ents = [...well.querySelectorAll('[class*="_EType_RECIPIENT"], ._Entity')];
const found = ents.map(e => (e.getAttribute('aria-label') || e.title || '').trim().toLowerCase()).filter(Boolean);
const loose = [...well.childNodes].some(n => n.nodeType === 3 && n.textContent.replace(/[\u200b\s]/g, ''));
return JSON.stringify({ ok: true, found, count: ents.length, loose });
"""

    _OW_FILL = r"""
const body = G.all('[contenteditable="true"][aria-label="Message body"]').find(G.shown);
if (!body) throw new Error('no new message is open');
const subj = G.all('input').find(i => G.shown(i) && i.getAttribute('aria-label') === 'Subject');
if (!subj) throw new Error('could not find the Subject field');
G.type(subj, D.subject);
if (D.html) {
  // At the very top of the body: an automatic signature stays below it.
  body.focus();
  const r = document.createRange(); r.setStart(body, 0); r.collapse(true);
  const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r);
  const before = body.innerHTML.length;
  document.execCommand('insertHTML', false, D.html);
  if (body.innerHTML.length <= before) {
    // The editor ignored the insert: put it in directly, then tell the editor.
    body.insertAdjacentHTML('afterbegin', D.html);
    body.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
  }
}
"""

    _OW_CHECK = r"""
const body = G.all('[contenteditable="true"][aria-label="Message body"]').find(G.shown);
const subj = G.all('input').find(i => G.shown(i) && i.getAttribute('aria-label') === 'Subject');
const html = body ? body.innerHTML : '';
return JSON.stringify({ ok: true,
  subjectOk: !!subj && subj.value === D.subject,
  bodyOk: !D.html || (html.includes(D.names) && !html.includes('(Resident Name)') && !/fluentsystemicons/i.test(html)) });
"""

    def _recipients(self, label: str, addresses: list[str]) -> bool:
        """Add each address to To or Cc as a recipient. True when every one is
        confirmed and no loose text is left in the field. Confirmed: its entry
        is labelled with the address (outside addresses), or the app clicked
        the one suggestion holding exactly that address and that added exactly
        one recipient (your directory, whose entries show only a name)."""
        import outlook_web

        def state():
            return self._js("outlook", self._OW_RECIPIENTS, {"label": label})

        confirmed = []
        for addr in addresses:
            want = addr.strip().lower()
            before = state().get("count", 0)
            self._do("outlook", self._OW_ADD, {"label": label, "addr": addr})
            # Its suggestion, once Outlook has looked it up; Enter if none comes.
            picked = False
            end = time.monotonic() + 8
            while time.monotonic() < end:
                if self._js("outlook", self._OW_PICK, {"addr": want}).get("picked"):
                    picked = True
                    break
                outlook_web.pause(0.3)
            else:
                self._do("outlook", self._OW_ENTER, {"label": label})
            end = time.monotonic() + 5
            while True:
                now = state()
                ok = want in now.get("found", []) or (picked and now.get("count", 0) == before + 1)
                if ok or time.monotonic() >= end:
                    break
                outlook_web.pause(0.25)
            confirmed.append(ok)
        return all(confirmed) and not state().get("loose")

    # Send: the one visible button named exactly Send (never "More send options").
    _OW_PRESS_SEND = r"""
const hits = G.all('button,[role=button]').filter(G.shown)
  .filter(b => ((b.getAttribute('aria-label') || G.text(b)) || '').trim() === 'Send');
if (hits.length !== 1) return JSON.stringify({ ok: true, pressed: false, n: hits.length });
hits[0].click();
return JSON.stringify({ ok: true, pressed: true });
"""
    # Anything Outlook says instead of sending, by its words.
    _OW_SAID = ("return JSON.stringify({ ok: true, text: G.all('[role=alertdialog],[role=dialog],[role=alert]')"
                ".filter(G.shown).map(G.text).filter(t => t && t.length > 3)[0] || '' });")

    def _outlook_send(self, job, data: dict) -> None:
        """Check everything once more, press Send once, and wait for Outlook to
        take the message (the compose closes). Stops on anything else."""
        import outlook_web

        # The countdown gave time for the message to change: read it all again.
        check = self._js("outlook", self._OW_CHECK, data)
        to = self._js("outlook", self._OW_RECIPIENTS, {"label": "To"})
        cc = self._js("outlook", self._OW_RECIPIENTS, {"label": "Cc"})
        same = (check.get("subjectOk") and check.get("bodyOk")
                and to.get("count") == len(data["to"]) and not to.get("loose")
                and cc.get("count") == (1 if data["cc"] else 0) and not cc.get("loose"))
        if not same:
            raise RuntimeError("Not sent: the message changed during the countdown. It is still open in the "
                               "Email tab for you to check and send.")
        got = self._js("outlook", self._OW_PRESS_SEND)
        if not got.get("pressed"):
            raise RuntimeError(f"Not sent: {got.get('n', 0)} Send buttons were showing, not one. "
                               "The message is still open in the Email tab.")
        self._log(job, "outlook", "pressed Send")
        end = time.monotonic() + self.confirm_timeout
        while time.monotonic() < end:
            if not self._yes("outlook", self._OW_BODY):
                self._log(job, "outlook", "sent")
                return
            said = self._js("outlook", self._OW_SAID).get("text")
            if said:
                self._log(job, "outlook", "refused")
                raise RuntimeError(f"Outlook did not send the welcome email: it said \"{said[:160]}\". "
                                   "It was pressed once and not again.")
            outlook_web.pause(0.5)
        self._log(job, "outlook", "no answer")
        raise RuntimeError(f"Outlook did not take the welcome email within {int(self.confirm_timeout)} seconds. "
                           "Send was pressed once and not again; check Sent Items before trying again.")

    def welcome(self, job) -> str:
        """Open a new message, fill To, Cc, Subject and your template; you press
        Send, or the app does when ticked for this unit and every check passes."""
        w = job.welcome
        data = {"to": list(w.get("to", [])), "cc": w.get("cc", "") or "", "subject": w.get("subject", ""),
                "html": w.get("html", "") or "", "names": w.get("names", "")}
        if not data["to"]:
            raise RuntimeError("no resident email address to send to")
        self.h.show_tab("outlook")

        # Never write into a message already in progress: one with a subject,
        # any recipient, or more in its body than a signature. Found live: a
        # body-only check let a run fill a half-written draft a second time.
        if self._yes("outlook", self._OW_IN_PROGRESS):
            raise RuntimeError("a message is already open in the Email tab; send or discard it, then try again")
        if not self._yes("outlook", self._OW_BODY):
            self._do("outlook",
                     "const b = G.all('button,[role=button]').find(e => G.shown(e) && "
                     "/^new( (mail|email|message))?$/i.test((e.getAttribute('aria-label') || G.text(e)).trim()));"
                     "if (!b) throw new Error('could not find New mail'); b.click();")
            self._until("outlook", self._OW_BODY, "the new message to open", timeout=30)

        import outlook_web

        if not self._yes("outlook", self._OW_BODY):
            raise RuntimeError("no new message is open")
        to_ok = self._recipients("To", data["to"])
        cc_ok = self._recipients("Cc", [data["cc"]] if data["cc"] else [])
        self._do("outlook", self._OW_FILL, data)
        outlook_web.pause(0.5)
        check = self._js("outlook", self._OW_CHECK, data)
        check.update(toOk=to_ok, ccOk=cc_ok)
        problems = [what for key, what in (("toOk", "the To recipients"), ("ccOk", "the Cc"),
                                           ("subjectOk", "the subject"), ("bodyOk", "the body"))
                    if not check.get(key)]
        if data["html"] and self._auto(job, "welcome"):
            if not problems and self._go_ahead("welcome", "Send"):
                self._outlook_send(job, data)
                return "the welcome email was sent by the app after every check passed"
            if problems:
                self.h.progress("welcome", "running", "Not sent: something did not read back exactly.")
        if data["html"]:
            lead = ("Everything checks out: recipients, subject and your template with the names filled in. "
                    if not problems else f"Look closely at {', '.join(problems)}: the app could not confirm it. ")
            self._your_turn(
                "outlook", "welcome",
                lead + "Look it over in the Email tab, then press Send yourself.",
                f"!{self._OW_BODY}")
            return "the welcome email was filled from your template; you pressed Send"

        # No captured template yet: the fields are filled, the body is yours.
        self._your_turn(
            "outlook", "welcome",
            "To, Cc and Subject are filled. Insert your Welcome template (Mail template), "
            "replace (Resident Name) with the first name(s), check it, and press Send.",
            f"!{self._OW_BODY}")
        return "the welcome email was sent from your template"

    # -- the whole unit ------------------------------------------------------------

    def run(self, job, steps: list[str]) -> list[tuple[str, bool, str]]:
        """Each step in order. A stopped or skipped step does not stop the others."""
        results = []
        for step in steps:
            self.h.progress(step, "running", "")
            try:
                detail = getattr(self, step)(job)
                results.append((step, True, detail))
                self.h.progress(step, "done", detail)
            except _Skipped:
                results.append((step, False, "Skipped. Do this one by hand."))
                self.h.progress(step, "failed", "Skipped. Do this one by hand.")
            except Exception as exc:  # noqa: BLE001 -- report, never hide
                results.append((step, False, str(exc)))
                self.h.progress(step, "failed", str(exc))
        return results
