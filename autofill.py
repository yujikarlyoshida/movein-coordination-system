"""
Carry out a confirmed unit: welcome email, internet portal, resident app.

Runs only after the user confirms one unit on the confirmation sheet; that
click is the explicit, per-unit instruction. Today it drives the PRACTICE
pages in practice/ -- lookalikes of the three systems that save and send
nothing. The real systems are not wired in: each needs its own decision and
its own probing, as Outlook on the web did.

How it works on a page, and why:

  * fields are found by their label, buttons by their name -- never by screen
    position. The internet portal's final Save sits exactly where Continue was;
    a click by position once saved by accident.
  * a field or button that cannot be found stops the step and says which.
    Nothing is guessed, and a half-finished form is reported, not hidden.
  * before the portal's Save, the review screen is read back and must show the
    values about to be saved.
  * the resident app's activation email is unticked unless the user chose it:
    ticking it emails the resident.
  * every step is checked afterwards against what the page reports it did.

Pure Python plus page scripts; the window itself belongs to the caller.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

import config

PRACTICE_DIR = Path(__file__).resolve().parent / "practice"
PRACTICE_PAGES = {
    "welcome": PRACTICE_DIR / "outlook.html",
    "xfinity": PRACTICE_DIR / "xfinity.html",
    "riseio": PRACTICE_DIR / "riseio.html",
}

STEP_LABELS = {
    "welcome": "Welcome email",
    "xfinity": "Xfinity: add resident",
    "riseio": "RiseIO: add resident and move into unit",
}


@dataclass
class Job:
    """One confirmed unit, exactly as the user approved it on the sheet."""

    unit: str
    residents: list[dict]                 # first, last, email, phone, move_in
    steps: list[str]                      # in order; any of STEP_LABELS
    welcome: dict = field(default_factory=dict)   # to, cc, subject, body
    send_activation_email: bool = False
    # Guided runs only: the systems whose final button the app may press for
    # this unit, ticked on the sheet ("xfinity", "riseio"). Empty: none.
    auto_final: list[str] = field(default_factory=list)


@dataclass
class StepResult:
    step: str
    ok: bool
    detail: str


# Helpers every page script starts with. Values arrive as JSON, so nothing the
# user typed is ever spliced into code.
_HELPERS = r"""
const H = {
  field(label) {
    const want = label.toLowerCase();
    const l = [...document.querySelectorAll('label')].find(x => {
      const t = x.textContent.trim().toLowerCase();
      return t === want || t.startsWith(want + ' (');
    });
    if (l) return l.htmlFor ? document.getElementById(l.htmlFor) : l.querySelector('input,select,textarea');
    return document.querySelector(`[aria-label="${label}"]`);
  },
  set(label, value) {
    const el = H.field(label);
    if (!el) throw new Error('could not find the ' + label + ' field');
    const proto = el.tagName === 'SELECT' ? HTMLSelectElement.prototype
                : el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    if (el.value !== value) throw new Error('the ' + label + ' field would not take the value');
  },
  tick(label, on) {
    const box = [...document.querySelectorAll('label')].find(x => x.textContent.trim().toLowerCase() === label.toLowerCase());
    const el = box && box.querySelector('input[type=checkbox]');
    if (!el) throw new Error('could not find the ' + label + ' checkbox');
    if (el.checked !== on) el.click();
    if (el.checked !== on) throw new Error('the ' + label + ' checkbox would not change');
  },
  press(name) {
    const b = [...document.querySelectorAll('button')].find(x => x.textContent.trim() === name && x.offsetParent !== null);
    if (!b) throw new Error('could not find the ' + name + ' button');
    b.click();
  },
  text(sel) { const e = document.querySelector(sel); return e ? e.innerText : ''; },
  err() { const e = document.querySelector('.error'); return e ? e.textContent.trim() : ''; },
};
"""


def _script(body: str, data: dict) -> str:
    """A page script with the helpers and `D` (the data) in scope. Returns JSON."""
    return (
        "(() => {" + _HELPERS
        + f"const D = {json.dumps(data)};"
        + "try {" + body + "} catch (e) { return JSON.stringify({ ok: false, detail: String(e.message || e) }); }"
        + "})()"
    )


class Runner:
    """Drives the pages in one WKWebView, one step at a time."""

    def __init__(self, webview, wait: float = 10.0, pages: dict | None = None):
        self.webview = webview
        self.wait = wait
        self.pages = pages or PRACTICE_PAGES

    # -- page plumbing -------------------------------------------------------

    def _js(self, source: str):
        import outlook_web

        return outlook_web.run_js(self.webview, source)

    def _on_main(self, fn) -> None:
        import outlook_web

        if outlook_web._on_main_thread():
            fn()
            return
        from PyObjCTools import AppHelper

        done = threading.Event()

        def call():
            try:
                fn()
            finally:
                done.set()

        AppHelper.callAfter(call)
        done.wait(self.wait)

    def _open(self, page: Path) -> None:
        import outlook_web
        from Foundation import NSURL

        url = NSURL.fileURLWithPath_(str(page))
        self._on_main(lambda: self.webview.loadFileURL_allowingReadAccessToURL_(
            url, url.URLByDeletingLastPathComponent()))
        outlook_web.pause(0.3)
        self._until("JSON.stringify(document.readyState === 'complete' && !!window.__practice)",
                    f"the {page.stem} page to load")

    def _until(self, probe: str, what: str) -> None:
        import time

        import outlook_web

        end = time.monotonic() + self.wait
        while time.monotonic() < end:
            if self._js(probe) is True:
                return
            outlook_web.pause(0.15)
        raise TimeoutError(f"timed out waiting for {what}")

    def _do(self, body: str, data: dict) -> dict:
        result = self._js(_script(body + " return JSON.stringify({ ok: true });", data))
        if not isinstance(result, dict) or not result.get("ok"):
            raise RuntimeError((result or {}).get("detail", "the page did not respond"))
        return result

    def _state(self) -> dict:
        return self._js("JSON.stringify(window.__practice)") or {}

    # -- the three steps -----------------------------------------------------

    def welcome(self, job: Job) -> str:
        w = job.welcome
        self._open(self.pages["welcome"])
        self._do("H.set('To', D.to); H.set('Cc', D.cc); H.set('Subject', D.subject);"
                 "H.set('Message body', D.body); H.press('Send');",
                 {"to": "; ".join(w.get("to", [])), "cc": w.get("cc", ""),
                  "subject": w.get("subject", ""), "body": w.get("body", "")})
        self._until("JSON.stringify(!!(window.__practice && window.__practice.sent))", "the message to send")
        sent = self._state()["sent"]
        if sent["subject"] != w.get("subject") or sorted(sent["to"].split("; ")) != sorted(w.get("to", [])):
            raise RuntimeError("the sent message does not match what was approved")
        return f"sent to {len(w.get('to', []))} resident(s)"

    def xfinity(self, job: Job) -> str:
        for position, r in enumerate(job.residents, start=1):
            self._open(self.pages["xfinity"])
            data = {"first": r["first"], "last": r["last"], "email": r["email"],
                    "phone": r["phone"], "unit": job.unit, "movein": r["move_in"]}
            # Step by step, checking the page moved on each time.
            self._do("H.set('First name', D.first); H.set('Last name', D.last);"
                     "H.set('Email', D.email); H.set('Phone', D.phone); H.press('Continue');"
                     "if (H.err()) throw new Error(H.err());", data)
            self._do("H.set('Select unit', D.unit); H.press('Continue'); if (H.err()) throw new Error(H.err());", data)
            self._do("H.set('Move in date', D.movein); H.press('Continue'); if (H.err()) throw new Error(H.err());", data)
            # Review: read back what is about to be saved before pressing Save.
            self._do(
                "const t = H.text('#review');"
                "for (const v of [D.first, D.last, D.email, D.phone, D.unit, D.movein])"
                "  if (!t.includes(v)) throw new Error('the review screen does not show ' + "
                "    (v === D.phone ? 'the phone' : v === D.email ? 'the email' : 'every value'));"
                "H.press('Save');", data)
            self._until("JSON.stringify(!!(window.__practice && window.__practice.saved))",
                        f"resident {position} to save")
            saved = self._state()["saved"]
            want = {k: data[k] for k in ("first", "last", "email", "phone", "unit", "movein")}
            if {k: saved[k] for k in want} != want:
                raise RuntimeError(f"resident {position}: what was saved does not match what was approved")
        return f"added {len(job.residents)} resident(s)"

    def riseio(self, job: Job) -> str:
        bday = getattr(config, "PLACEHOLDER_BIRTHDAY", {"day": 1, "month": "January", "year": 1923})
        for position, r in enumerate(job.residents, start=1):
            self._open(self.pages["riseio"])
            data = {"first": r["first"], "last": r["last"], "email": r["email"], "phone": r.get("phone", ""),
                    "day": str(bday["day"]), "month": bday["month"], "year": str(bday["year"]),
                    "activate": bool(job.send_activation_email), "unit": job.unit, "movein": r.get("move_in", "")}
            self._do("H.set('First name', D.first); H.set('Last name', D.last); H.set('Email', D.email);"
                     "H.set('Phone', D.phone); H.set('Day', D.day); H.set('Month', D.month); H.set('Year', D.year);"
                     "H.tick('Send account activation email', D.activate);"
                     "H.press('Create New Resident'); if (H.err()) throw new Error(H.err());", data)
            self._until("JSON.stringify(!!document.querySelector('.veil.on'))", "the Move into Unit dialog")
            self._do("H.set('Unit', D.unit); H.set('Move-in date', D.movein); H.press('Confirm Move-In');", data)
            self._until("JSON.stringify(!!(window.__practice && window.__practice.moved))",
                        f"resident {position} to move in")
            state = self._state()
            if state["moved"]["unit"] != job.unit or state["created"]["email"] != r["email"]:
                raise RuntimeError(f"resident {position}: the record does not match what was approved")
            if state["activation_email_sent"] != bool(job.send_activation_email):
                raise RuntimeError("the activation email setting did not take")
            status = self._js("JSON.stringify(document.getElementById('pstatus').textContent)")
            if status != "Future":
                raise RuntimeError(f"resident {position}: not shown as moving in")
        return f"added {len(job.residents)} resident(s), moved into {job.unit}" + (
            ", activation email sent" if job.send_activation_email else ", no activation email")

    # -- the whole unit --------------------------------------------------------

    def run(self, job: Job, progress=None) -> list[StepResult]:
        """
        Each step in order. A failed step is reported and the rest still run:
        they are independent systems, and a stopped portal should not hold up
        the welcome email. `progress(step, state, detail)` is called as each
        step starts ("running") and ends ("done" or "failed").
        """
        results = []
        for step in job.steps:
            if progress:
                progress(step, "running", "")
            try:
                detail = getattr(self, step)(job)
                results.append(StepResult(step, True, detail))
            except Exception as exc:  # noqa: BLE001 -- report, never hide
                results.append(StepResult(step, False, str(exc)))
            if progress:
                progress(step, "done" if results[-1].ok else "failed", results[-1].detail)
        return results
