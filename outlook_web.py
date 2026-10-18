"""
Outlook on the web, inside the app's own browser window.

You sign in yourself, MFA included; the app never sees or stores a password.
The session lives in WebKit's own cookie store, like any browser's. While it is
alive the app reads the Move ins folder from the page as rendered -- the thread
list, then the reading pane of threads that are new or changed -- and nothing
else.

The window identifies itself honestly: WebKit's own user agent with
"MoveInTriage/1.0" appended. If Outlook or the tenant's sign-in policy refuses
that client, the refusal stands. This app does not disguise itself to get past
an access rule.

What this deliberately does NOT do:

  * read Outlook's access token out of the page to call Microsoft's APIs. That
    would use a first-party Microsoft client to get around the tenant's
    app-consent process, which is the step IT has not approved.
  * click anything that changes mail: send, reply, delete, move, flag, mark.
    The only clicks are selecting the folder and opening a thread, by element,
    never by screen position. Opening a thread marks it read; the owner accepted
    that for the Move ins folder, because handled is decided by replies in the
    thread, never by read status.

macOS only; AppKit and WebKit are imported lazily.

    python outlook_web.py                     open the window and sign in
    python outlook_web.py --watch [MINUTES]   sign in, then read Move ins every
                                              MINUTES (default 5) and print the
                                              board: units and verdicts only
    python outlook_web.py --probe DIR         development: also evaluate JS files
                                              dropped into DIR (see _Probe)
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import threading
import time

OUTLOOK_URL = "https://outlook.office.com/mail/"

# Appended to WebKit's own user agent, so the client is identifiable as what it is.
APP_UA_SUFFIX = "MoveInTriage/1.0"

# The folder the leasing team's threads are filed in.
FOLDER = "Move ins"

# At most this many threads are opened per pass. The first pass on a full folder
# reads the newest ones; later passes only open what changed.
MAX_OPENS_PER_PASS = 15

EXTRACT_JS = (pathlib.Path(__file__).resolve().parent / "owa_extract.js").read_text(encoding="utf-8")

# Held at module level: AppKit does not retain Python-side objects for us.
_delegates: list = []
_ui_delegate = None


def _make_ui_delegate():
    """
    Sign-in pages sometimes open a new window; keep those in the same tab.

    Outlook's own pop-outs (a thread opened in a new window) start as a blank
    page and are filled in by script. Loading that blank page here would replace
    Outlook itself, so pop-outs are refused: everything stays in the tab.

    One shared instance: an Objective-C class can only be registered once per
    process, and defining it again raised "overriding existing class".
    """
    global _ui_delegate
    if _ui_delegate is not None:
        return _ui_delegate

    from AppKit import NSObject

    class UIDelegate(NSObject):
        def webView_createWebViewWithConfiguration_forNavigationAction_windowFeatures_(
            self, webview, configuration, action, features
        ):
            url = action.request().URL()
            scheme = (url.scheme() or "").lower() if url is not None else ""
            if scheme in ("http", "https"):
                webview.loadRequest_(action.request())
            return None

    _ui_delegate = UIDelegate.alloc().init()
    return _ui_delegate


def open_window(url: str = OUTLOOK_URL, show: bool = True):
    """
    The Outlook tab of the app's browser (browser.py). Returns its WKWebView.

    With show=False the browser is created and Outlook loads, but nothing comes
    to the front: the menu bar app starts this way and only shows the browser
    when a sign-in is needed, or when asked from the menu.
    """
    import browser

    return browser.open_tab("outlook") if show else browser.view("outlook")


# ---------------------------------------------------------------------------
# Running page scripts from any thread
# ---------------------------------------------------------------------------

def _on_main_thread() -> bool:
    from Foundation import NSThread

    return bool(NSThread.isMainThread())


def pause(seconds: float) -> None:
    """Wait without freezing WebKit: spin the run loop on the main thread."""
    if not _on_main_thread():
        time.sleep(seconds)
        return
    from Foundation import NSDate, NSRunLoop

    end = time.monotonic() + seconds
    loop = NSRunLoop.currentRunLoop()
    while time.monotonic() < end:
        loop.runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.05))


def run_js(webview, source: str, timeout: float = 15.0):
    """
    Evaluate `source` in the page and return its result, JSON-decoded when the
    script returns a JSON string (every script here does).

    WebKit must be driven from the main thread. From a background thread (the
    poller) the call is handed to the main thread and this waits for it; on the
    main thread (tests, the probe) the run loop is spun until the answer arrives.
    """
    box: dict = {}
    done_event = threading.Event()

    def done(result, error):
        box["result"], box["error"] = result, error
        done_event.set()

    def call():
        webview.evaluateJavaScript_completionHandler_(source, done)

    if _on_main_thread():
        call()
        end = time.monotonic() + timeout
        while not done_event.is_set() and time.monotonic() < end:
            pause(0.05)
    else:
        from PyObjCTools import AppHelper

        AppHelper.callAfter(call)
        done_event.wait(timeout)

    if not done_event.is_set():
        raise TimeoutError("the page did not answer")
    if box["error"] is not None:
        raise RuntimeError(f"page script failed: {box['error']}")
    result = box["result"]
    if isinstance(result, str):
        try:
            return json.loads(result)
        except ValueError:
            return result
    return result


# ---------------------------------------------------------------------------
# Page scripts. Each returns a JSON string. Clicks are by element only.
# ---------------------------------------------------------------------------

_STATE_JS = """(() => JSON.stringify({
  signInPage: /login\\.microsoftonline|login\\.live/.test(location.host),
  folders: document.querySelectorAll('[role="treeitem"]').length,
}))()"""

_FOLDER_MATCH = """
  const clean = t => (t.textContent || '').replace(/[\\ue000-\\uf8ff]/g, '').trim();
  const want = %s.toLowerCase();
  const folder = [...document.querySelectorAll('[role="treeitem"]')]
    .find(t => { const c = clean(t).toLowerCase();
                 return c === want || (c.startsWith(want) && /^[\\d\\s]|^selected|^unread/.test(c.slice(want.length))); });
"""

_SELECT_FOLDER_JS = """(() => {%s
  if (!folder) return JSON.stringify({ found: false });
  const already = folder.getAttribute('aria-selected') === 'true';
  if (!already) folder.click();
  return JSON.stringify({ found: true, already });
})()"""

_FOLDER_SELECTED_JS = """(() => {%s
  return JSON.stringify({ selected: !!folder && folder.getAttribute('aria-selected') === 'true' });
})()"""

# Rows: conversation id, unread, and a hash of the accessible label. The label
# carries the latest message's preview, so the hash changes when a reply lands.
# Status words are left out of the hash: opening a thread removes "Unread" and
# can flip "Collapsed"/"Expanded", which is not new mail and must not trigger a
# re-read on the next pass.
_ROWS_JS = """(() => {
  const h = s => { let x = 5381; for (const c of s) x = ((x * 33) ^ c.charCodeAt(0)) >>> 0; return x.toString(16); };
  const STATUS = /(^|[ ,])(Unread|Read|Collapsed|Expanded|Selected)(?=[ ,]|$)/g;
  return JSON.stringify([...document.querySelectorAll('[role="option"][data-convid]')].map(o => {
    const label = o.getAttribute('aria-label') || '';
    return { convId: o.getAttribute('data-convid'),
             unread: /(^|[ ,])Unread([ ,]|$)/.test(label),
             labelHash: h(label.replace(STATUS, '$1').replace(/\\s+/g, ' ').trim()) };
  }));
})()"""

_OPEN_ROW_JS = """(() => {
  const id = %s;
  const row = [...document.querySelectorAll('[role="option"][data-convid]')]
    .find(o => o.getAttribute('data-convid') === id);
  if (!row) return JSON.stringify({ found: false });
  row.click();
  return JSON.stringify({ found: true });
})()"""

# What the reading pane is showing: the selected row and a fingerprint of the
# pane, so a wait can tell the new thread has replaced the previous one.
_PANE_JS = """(() => {
  const h = s => { let x = 5381; for (const c of s) x = ((x * 33) ^ c.charCodeAt(0)) >>> 0; return x.toString(16); };
  const main = document.querySelector('[role="main"]') || document.body;
  const bodies = [...main.querySelectorAll('[aria-label="Message body"], [role="document"]')];
  const sel = document.querySelector('[role="option"][aria-selected="true"][data-convid]');
  const heading = main.querySelector('[role="heading"]');
  return JSON.stringify({
    selected: sel ? sel.getAttribute('data-convid') : '',
    bodies: bodies.length,
    print: h((heading ? heading.innerText : '') + '|' + bodies.map(b => (b.innerText || '').slice(0, 200)).join('|')),
  });
})()"""


# The thread list scrolls, and Outlook only keeps the rows near the visible part
# in the page. These find the list's scrolling container from its rows and move
# it, by element.
_SCROLLER = """
  const firstRow = document.querySelector('[role="option"][data-convid]');
  let box = firstRow ? firstRow.parentElement : null;
  while (box && !(box.scrollHeight > box.clientHeight + 2 &&
                  /auto|scroll/.test(getComputedStyle(box).overflowY))) box = box.parentElement;
"""

_SCROLL_TOP_JS = """(() => {%s
  if (box) box.scrollTop = 0;
  return JSON.stringify({ scrollable: !!box });
})()""" % _SCROLLER

_SCROLL_DOWN_JS = """(() => {%s
  if (!box) return JSON.stringify({ moved: false });
  const before = box.scrollTop;
  box.scrollTop = before + Math.max(40, Math.floor(box.clientHeight * 0.8));
  return JSON.stringify({ moved: box.scrollTop > before });
})()""" % _SCROLLER


class SignedOut(Exception):
    """The window is on Microsoft's sign-in page: the session has ended."""


class NotReady(Exception):
    """Outlook has not finished loading, or the folder is missing."""


class OutlookWebReader:
    """
    Reads the Move ins folder from the window, one pass at a time.

    Keeps what each thread contained last time, keyed by conversation id with
    the list label's hash, so a pass opens only threads that are new or have
    changed, and still returns every thread's messages for the full board.

    A pass starts at the top of the list and works down a screenful at a time,
    because rows far from view are not in the page. It stops at the first thread
    whose newest message is older than the lookback window (the list is newest
    first), at the bottom of the list, after `max_scrolls`, or when the
    per-pass open cap is reached. In that last case `backlog` is set: there is
    more to read, and the next pass carries on (threads read already are not
    reopened, so it moves past them quickly). The rows below the cap are not
    counted, because their age is unknown until they are opened.
    """

    def __init__(self, webview, folder: str | None = None, max_opens: int = MAX_OPENS_PER_PASS,
                 wait: float = 10.0, lookback_days: int | None = None, max_scrolls: int = 60):
        import config

        self.webview = webview
        # The folder named in settings (config.MAIL_FOLDER), "Move ins" by default.
        self.folder = folder or getattr(config, "MAIL_FOLDER", FOLDER)
        self.max_opens = max_opens
        self.wait = wait
        self.lookback_days = lookback_days or getattr(config, "LOOKBACK_DAYS", 30)
        self.max_scrolls = max_scrolls
        self._threads: dict[str, tuple[str, list[dict]]] = {}
        # Per-pass figures, for the diagnostic line.
        self.last_opened = 0
        self.backlog = 0
        self.skipped = 0
        self.threads_seen = 0
        self.stopped_by = ""

    def _js(self, source: str):
        return run_js(self.webview, source)

    def _until(self, check, what: str):
        end = time.monotonic() + self.wait
        while time.monotonic() < end:
            if check():
                return
            pause(0.3)
        raise NotReady(f"timed out waiting for {what}")

    def state(self) -> str:
        """'signed_out', 'loading' or 'ready'."""
        s = self._js(_STATE_JS)
        if s["signInPage"]:
            return "signed_out"
        return "ready" if s["folders"] else "loading"

    def _open_and_extract(self, conv_id: str) -> list[dict] | None:
        before = self._js(_PANE_JS)
        if not (before["selected"] == conv_id and before["bodies"]):
            opened = self._js(_OPEN_ROW_JS % json.dumps(conv_id))
            if not opened["found"]:
                return None

            def ready(prev=before["print"]):
                now = self._js(_PANE_JS)
                return now["selected"] == conv_id and now["bodies"] > 0 and now["print"] != prev

            self._until(ready, "the thread to open")
            pause(0.5)  # let late parts of the thread render
        return self._js(EXTRACT_JS)

    def _older_than_window(self, records: list[dict]) -> bool:
        """True when every dated message in the thread is outside the window."""
        import mailsource
        import rules

        inside = mailsource.owa_messages(records, days=self.lookback_days)
        dated = [m for m in mailsource.owa_messages(records) if rules.parse_received(m["received"])]
        return bool(dated) and not inside

    def read_pass(self) -> list[dict]:
        """One pass. Returns raw extractor records for every thread in the window."""
        state = self.state()
        if state == "signed_out":
            raise SignedOut("signed out of Outlook; sign in again in the window")
        if state != "ready":
            raise NotReady("Outlook is still loading")

        folder = json.dumps(self.folder)
        picked = self._js(_SELECT_FOLDER_JS % (_FOLDER_MATCH % folder))
        if not picked["found"]:
            raise NotReady(f"no folder named {self.folder!r}")
        if not picked["already"]:
            self._until(lambda: self._js(_FOLDER_SELECTED_JS % (_FOLDER_MATCH % folder))["selected"],
                        "the folder to open")
            self._until(lambda: bool(self._js(_ROWS_JS)), "the thread list")

        self._js(_SCROLL_TOP_JS)
        pause(0.5)

        seen: set[str] = set()
        self.last_opened = self.backlog = self.skipped = 0
        self.stopped_by = "scroll limit"
        done = at_end = False
        for _ in range(self.max_scrolls):
            for row in self._js(_ROWS_JS):
                conv = row["convId"]
                if conv in seen:
                    continue
                seen.add(conv)
                cached = self._threads.get(conv)
                if cached and cached[0] == row["labelHash"]:
                    records = cached[1]
                elif self.last_opened + self.skipped < self.max_opens:
                    try:
                        records = self._open_and_extract(conv)
                    except NotReady:
                        # One slow or odd thread must not sink the pass. Not
                        # cached, so the next pass tries it again.
                        self.skipped += 1
                        continue
                    if records is None:
                        continue
                    self._threads[conv] = (row["labelHash"], records)
                    self.last_opened += 1
                else:
                    # Over the cap: stop here and read on a later pass.
                    # Anything known from earlier stays on the board meanwhile.
                    self.backlog = 1
                    self.stopped_by = "open cap"
                    done = True
                    break
                if self._older_than_window(records):
                    self.stopped_by = "lookback window"
                    done = True
                    break
            if done:
                break
            if at_end:
                # The rows were read once more after the list stopped moving.
                self.stopped_by = "end of list"
                break
            if not self._js(_SCROLL_DOWN_JS)["moved"]:
                # At the bottom. Rows can render a moment after the last scroll,
                # so read once more before stopping rather than miss the last one.
                at_end = True
            pause(0.6)  # let the newly visible rows render

        # A full sweep shows which threads are still in the folder; forget the
        # rest (moved out, or aged past the window).
        if self.stopped_by in ("lookback window", "end of list") and not self.skipped:
            self._threads = {c: v for c, v in self._threads.items() if c in seen}
        self.threads_seen = len(seen)
        self._js(_SCROLL_TOP_JS)
        return [m for _, records in self._threads.values() for m in records]


def diagnose(messages: list[dict]) -> dict[str, int]:
    """
    Counts only, per thread: why threads did or did not become triggers.

    A thread counts once in each column it qualifies for. "Both" is what the
    rules can act on; a thread with lease details but no known sender is the
    usual sign that a sender is missing from the configured lists.
    """
    import triggers

    threads: dict[str, dict[str, bool]] = {}
    for m in messages:
        conv = m["id"].split(":")[1] if m["id"].count(":") >= 2 else m["id"]
        t = threads.setdefault(conv, {"known": False, "details": False, "both": False})
        known = triggers.is_trigger_sender(m.get("sender", ""), m.get("sender_name", ""))
        details = triggers.mentions_trigger_event(m.get("body", ""))
        t["known"] |= known
        t["details"] |= details
        t["both"] |= known and details
    return {
        "threads": len(threads),
        "known_sender": sum(t["known"] for t in threads.values()),
        "lease_details": sum(t["details"] for t in threads.values()),
        "both": sum(t["both"] for t in threads.values()),
    }


def unknown_lease_senders(messages: list[dict]) -> list[tuple[str, int]]:
    """
    Who sent the lease emails the rules cannot act on: [(sender, threads)].

    For each thread with lease details but no message from a known sender, the
    sender of its OLDEST message with lease details -- the original lease email.
    Later messages are colleagues' replies, and a reply that quotes the lease
    carries the same details; listing those would name colleagues, not senders
    to add.

    For the owner's terminal only (see _watch). Colleague names and addresses
    must not go into the repository, logs or anything shared.
    """
    import rules
    import triggers

    threads: dict[str, list[dict]] = {}
    for m in messages:
        conv = m["id"].split(":")[1] if m["id"].count(":") >= 2 else m["id"]
        threads.setdefault(conv, []).append(m)

    counts: dict[str, int] = {}
    for msgs in threads.values():
        if any(triggers.is_trigger_sender(m.get("sender", ""), m.get("sender_name", "")) for m in msgs):
            continue
        leases = [m for m in msgs if triggers.mentions_trigger_event(m.get("body", ""))]
        if not leases:
            continue
        dated = [m for m in leases if rules.parse_received(m["received"])]
        first = min(dated, key=lambda m: rules.parse_received(m["received"])) if dated else leases[0]
        who = first.get("sender") or first.get("sender_name") or "(sender not shown)"
        counts[who] = counts.get(who, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))


# ---------------------------------------------------------------------------
# --watch: live use before the menu bar integration
# ---------------------------------------------------------------------------

def _watch(webview, minutes: float) -> None:
    """Background thread: a pass every `minutes`, board printed as units only."""
    import mailsource
    import rules
    from models import Verdict

    reader = OutlookWebReader(webview)
    print(f"watching {FOLDER!r} every {minutes:g} min. Sign in in the window if asked.", flush=True)
    last_problem = ""
    last_unknown: list = []
    while True:
        stamp = time.strftime("%H:%M")
        # Not ready or signed out: look again in seconds, not a full interval,
        # so signing in (or Outlook finishing its load) is picked up at once.
        delay = 20
        try:
            records = reader.read_pass()
        except SignedOut as exc:
            problem = str(exc)
        except (NotReady, TimeoutError) as exc:
            problem = f"not ready: {exc}"
        except Exception as exc:  # noqa: BLE001 -- keep watching; say what broke
            problem = f"pass failed: {type(exc).__name__}: {exc}"
        else:
            problem = ""
            # A backlog means the board is incomplete; come back sooner.
            delay = 60 if (reader.backlog or reader.skipped) else minutes * 60
            messages = mailsource.owa_messages(records, days=reader.lookback_days)
            results = rules.evaluate(messages)
            needs = [r for r in results if r.verdict is Verdict.NEEDS_OUTREACH]
            unclear = [r for r in results if r.verdict is Verdict.UNCLEAR]
            handled = [r for r in results if r.verdict is Verdict.HANDLED]
            skipped = [r for r in results if r.verdict is Verdict.SKIPPED_CORPORATE]
            d = diagnose(messages)
            print(f"[{stamp}] {len(needs)} need outreach, {len(unclear)} unclear, "
                  f"{len(skipped)} skipped (corporate), {len(handled)} handled", flush=True)
            print(f"        threads in window {d['threads']}: known sender {d['known_sender']}, "
                  f"lease details {d['lease_details']}, both {d['both']} | "
                  f"listed {reader.threads_seen}, opened {reader.last_opened}, "
                  f"skipped {reader.skipped}, stopped at {reader.stopped_by}", flush=True)
            if reader.backlog:
                print("        more to read: board incomplete until then; next pass in 1 min", flush=True)
            for r in needs + unclear:
                print(f"    #{r.unit}  {r.verdict.value}", flush=True)
            # Lease emails the rules cannot act on, and who sent them. Printed
            # here only, for the owner to choose which belong in config_local.py;
            # repeated only when the list changes.
            unknown = unknown_lease_senders(messages)
            if unknown and unknown != last_unknown:
                print("        lease emails from senders not on your lists "
                      "(their units are not judged until you add them):", flush=True)
                for who, n in unknown:
                    print(f"          - {who}  ({n} thread{'s' if n != 1 else ''})", flush=True)
            last_unknown = unknown
        # Say a problem once, not every 20 seconds while it lasts.
        if problem and problem != last_problem:
            print(f"[{stamp}] {problem}", flush=True)
        last_problem = problem
        time.sleep(delay)


class _Probe:
    """
    Development only. Every half second, if DIR/cmd.js exists, evaluate it in the
    page and write the JSON result to DIR/out.json.

    The probe scripts used while building this return structure only -- counts,
    attribute names, booleans -- never message text, so a development session
    does not copy anyone's mail into a log.
    """

    def __init__(self, webview, directory: str):
        self.webview = webview
        self.dir = directory
        os.makedirs(directory, exist_ok=True)

    def tick(self) -> None:
        cmd = os.path.join(self.dir, "cmd.js")
        if not os.path.exists(cmd):
            return
        with open(cmd, encoding="utf-8") as fh:
            source = fh.read()
        os.remove(cmd)

        def done(result, error):
            out = {"result": result, "error": str(error) if error else None}
            with open(os.path.join(self.dir, "out.json"), "w", encoding="utf-8") as fh:
                json.dump(out, fh, default=str)

        self.webview.evaluateJavaScript_completionHandler_(source, done)


def main() -> int:
    from AppKit import NSApplication, NSApplicationActivationPolicyRegular
    from Foundation import NSTimer

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)

    webview = open_window()

    if "--probe" in sys.argv:
        directory = sys.argv[sys.argv.index("--probe") + 1]
        probe = _Probe(webview, directory)
        _delegates.append(probe)
        NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.5, True, lambda _t: probe.tick())

    if "--watch" in sys.argv:
        i = sys.argv.index("--watch")
        minutes = float(sys.argv[i + 1]) if i + 1 < len(sys.argv) and sys.argv[i + 1][0].isdigit() else 5.0
        threading.Thread(target=_watch, args=(webview, minutes), daemon=True).start()

    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
