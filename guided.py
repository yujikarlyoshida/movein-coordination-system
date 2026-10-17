"""
Guided runs on the real systems: the app fills, you press every final button.

The first real units go through here. For each step the app opens the system's
tab in its own browser (browser.py), fills only the fields it has mapped with
certainty, and stops at the final control -- Send, Save, Create New Resident,
Confirm Move-In -- with a "your turn" prompt on the sheet. It carries on once it
sees you pressed it, or when you press Done on the sheet. The app itself never
presses a final button.

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

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import config

LEARN_DIR = Path.home() / "Library" / "Application Support" / "MoveInTriage" / "learned"

# How long to wait for you to press a final button before the step stops.
YOUR_TURN_TIMEOUT = 15 * 60

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
    return G.all('input,select,textarea,prism-input-text,prism-select,prism-button,button,[ng-model]')
      .filter(G.shown)
      .map(e => (e.getAttribute('data-testid') || e.getAttribute('ng-model') || e.getAttribute('label')
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
const items = G.all('input,select,textarea,[contenteditable="true"],prism-input-text,prism-select,prism-button,button,[ng-model]')
  .filter(G.shown).map(e => {
    const tag = e.tagName.toLowerCase(), button = /button/.test(tag);
    return { tag, button, type: e.getAttribute('type') || '',
             label: safe(button ? (e.getAttribute('aria-label') || G.text(e))
                                : (e.getAttribute('label') || e.getAttribute('aria-label') || e.getAttribute('placeholder') || e.getAttribute('name'))),
             testid: e.getAttribute('data-testid') || '', model: e.getAttribute('ng-model') || '',
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


class Guided:
    """One guided unit across the systems, in the app's browser."""

    def __init__(self, hooks: Hooks, wait: float = 20.0, turn_timeout: float = YOUR_TURN_TIMEOUT):
        self.h = hooks
        self.wait = wait
        self.turn_timeout = turn_timeout

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

    @staticmethod
    def _who(job, position: int) -> str:
        return "the resident" if len(job.residents) == 1 else f"resident {position}"

    # -- RiseIO ----------------------------------------------------------------

    _RISE_DIALOG = "G.all('[role=dialog], md-dialog, .modal').some(G.shown)"

    def riseio(self, job) -> str:
        """Fill Add Resident; you press Create New Resident, then Confirm Move-In."""
        bday = getattr(config, "PLACEHOLDER_BIRTHDAY", {"day": 1, "month": "January", "year": 1923})
        for position, r in enumerate(job.residents, start=1):
            who = self._who(job, position)
            self.h.show_tab("riseio")
            add_shown = "G.all('[ui-sref=\"app.resident-edit\"]').some(e => G.shown(e) && /add\\s+resident/i.test(G.text(e)))"
            if not self._yes("riseio", add_shown):
                self._do("riseio", "location.assign(location.origin + '/app/resident');")
            self._until("riseio", add_shown, "the RiseIO residents page")
            self._do("riseio", "G.all('[ui-sref=\"app.resident-edit\"]').find(e => /add\\s+resident/i.test(G.text(e))).click();")
            self._until("riseio", "G.one('[ng-model=\"user.firstName\"]')", "the Add Resident form")
            self._do(
                "riseio",
                "G.type(G.one('[ng-model=\"user.firstName\"]'), D.first);"
                "G.type(G.one('[ng-model=\"user.lastName\"]'), D.last);"
                "G.type(G.one('[ng-model=\"user.email\"]'), D.email);"
                "if (D.phone) G.type(G.one('[ng-model=\"user.phone\"]'), D.phone);"
                "G.pick(G.one('[ng-model=\"birthday.day\"]'), D.day);"
                "G.pick(G.one('[ng-model=\"birthday.month\"]'), D.month);"
                "G.pick(G.one('[ng-model=\"birthday.year\"]'), D.year);"
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
            self._your_turn(
                "riseio", "riseio",
                f"Check {who}'s details in the RiseIO tab. Account activation {mail}. "
                "Then press Create New Resident.",
                f"!G.one('[ng-model=\"user.firstName\"]') || {self._RISE_DIALOG}")
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
        return (f"{len(job.residents)} resident(s) created and moved in; you pressed the final buttons"
                + (", activation email sent" if job.send_activation_email else ", no activation email"))

    # -- Xfinity ---------------------------------------------------------------

    _XF = "G.one('[data-testid=\"resident-contact-details-{}\"]')"
    _XF_SAVE = "G.all('prism-button,button').filter(G.shown).some(b => /^save$/i.test(G.text(b)))"
    _XF_OPEN = "G.all('[data-testid^=\"resident-create-footer\"]').some(G.shown)"

    def xfinity(self, job) -> str:
        """Fill step 1, continue; each later screen is recorded and handed to you; you press Save."""
        for position, r in enumerate(job.residents, start=1):
            who = self._who(job, position)
            self.h.show_tab("xfinity")
            self._until("xfinity", "G.one('prism-button[data-testid=\"residents_hero_add_button\"]')",
                        "the Xfinity residents page (open Manage residents if it isn't showing)", timeout=60)
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

            # Unseen screens: record each, fill a move-in date if one is labelled
            # so, hand the rest to you, until the review with its Save button.
            for screen in range(2, 2 + MAX_XFINITY_SCREENS):
                self.learn("xfinity", f"screen-{screen}")
                if self._yes("xfinity", self._XF_SAVE):
                    has = self._js("xfinity",
                                   "const t = document.body.innerText + ' ' + G.all('prism-text').map(G.text).join(' ');"
                                   "return JSON.stringify({ ok: true, n: [D.email, D.unit].filter(v => t.includes(v)).length });",
                                   {"email": r["email"], "unit": job.unit}).get("n", 0)
                    self._your_turn(
                        "xfinity", "xfinity",
                        ("The review shows the email and unit. " if has == 2
                         else "Check the email and unit on the review carefully. ")
                        + f"If everything for {who} matches, press Save yourself. Save sits where Continue was.",
                        f"!{self._XF_OPEN}")
                    break
                dated = self._js("xfinity", _MOVE_IN_FIELD, {"movein": r["move_in"]}).get("dated")
                here = self._print("xfinity")
                self._your_turn(
                    "xfinity", "xfinity",
                    f"Complete this screen for {who}: choose unit {job.unit} if it asks, "
                    + ("the move-in date is filled in, " if dated else f"use move-in date {r['move_in']} if it asks, ")
                    + "leave move-out empty unless you know it. Then press Continue.",
                    f"G.print() !== D.here || !{self._XF_OPEN}", data={"here": here})
                if not self._yes("xfinity", self._XF_OPEN):
                    break
            else:
                raise RuntimeError("the wizard went on longer than expected; finish it by hand")
        return f"{len(job.residents)} resident(s) added; you pressed Save"

    # -- Outlook welcome ---------------------------------------------------------

    # The compose window, as mapped read-only: To and Cc are editable areas
    # labelled "To" / "Cc", Subject an input labelled "Subject", the body an
    # editable "Message body" box. The app opens it and fills it; Send is yours.
    _OW_BODY = "G.all('[contenteditable=\"true\"][aria-label=\"Message body\"]').find(G.shown)"

    _OW_FILL = r"""
const body = G.all('[contenteditable="true"][aria-label="Message body"]').find(G.shown);
if (!body) throw new Error('no new message is open');
const well = label => G.all('[contenteditable="true"]').find(e => G.shown(e) && e.getAttribute('aria-label') === label);
const add = (label, list) => {
  if (!list.length) return;
  const w = well(label);
  if (!w) throw new Error('could not find the ' + label + ' field');
  w.focus();
  // Typed the way a person would, so Outlook resolves each recipient.
  for (const a of list) document.execCommand('insertText', false, a + ';');
  w.blur();
};
add('To', D.to);
add('Cc', D.cc ? [D.cc] : []);
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
const well = label => G.all('[contenteditable="true"]').find(e => G.shown(e) && e.getAttribute('aria-label') === label);
const seen = w => w ? (w.innerText + ' ' + [...w.querySelectorAll('[title],[aria-label]')]
                 .map(e => (e.getAttribute('title') || '') + ' ' + (e.getAttribute('aria-label') || '')).join(' ')).toLowerCase() : '';
const to = seen(well('To')), cc = seen(well('Cc'));
const subj = G.all('input').find(i => G.shown(i) && i.getAttribute('aria-label') === 'Subject');
const html = body ? body.innerHTML : '';
return JSON.stringify({ ok: true,
  toOk: D.to.every(a => to.includes(a.toLowerCase())),
  ccOk: !D.cc || cc.includes(D.cc.toLowerCase()),
  subjectOk: !!subj && subj.value === D.subject,
  bodyOk: !D.html || (html.includes(D.names) && !html.includes('(Resident Name)') && !/fluentsystemicons/i.test(html)) });
"""

    def welcome(self, job) -> str:
        """Open a new message, fill To, Cc, Subject and your template; you press Send."""
        w = job.welcome
        data = {"to": list(w.get("to", [])), "cc": w.get("cc", "") or "", "subject": w.get("subject", ""),
                "html": w.get("html", "") or "", "names": w.get("names", "")}
        if not data["to"]:
            raise RuntimeError("no resident email address to send to")
        self.h.show_tab("outlook")

        # Never write into a message already in progress.
        if self._yes("outlook", f"(() => {{ const b = {self._OW_BODY}; return b && b.innerText.trim().length > 40; }})()"):
            raise RuntimeError("a message is already open in the Email tab; send or discard it, then try again")
        if not self._yes("outlook", self._OW_BODY):
            self._do("outlook",
                     "const b = G.all('button,[role=button]').find(e => G.shown(e) && "
                     "/^new (mail|email|message)$/i.test((e.getAttribute('aria-label') || G.text(e)).trim()));"
                     "if (!b) throw new Error('could not find New mail'); b.click();")
            self._until("outlook", self._OW_BODY, "the new message to open", timeout=30)

        import outlook_web

        self._do("outlook", self._OW_FILL, data)
        outlook_web.pause(0.8)   # let Outlook resolve the recipients
        check = self._js("outlook", self._OW_CHECK, data)
        problems = [what for key, what in (("toOk", "the To recipients"), ("ccOk", "the Cc"),
                                           ("subjectOk", "the subject"), ("bodyOk", "the body"))
                    if not check.get(key)]
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
