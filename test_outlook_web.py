"""
Tests for reading mail from Outlook on the web.

Run with:  python -m pytest test_outlook_web.py -v
       or: python test_outlook_web.py

Two halves:

  * the converter (mailsource.owa_messages): plain Python, runs anywhere.
  * the extractor (owa_extract.js): runs in real WebKit through pyobjc, against
    SAMPLE_PAGE below, a hand-made page shaped like the live reading pane
    (layout recorded in owa_extract.js). No network, no stored website data.
    Skipped where WebKit is not available.

Everything here is fictional: .invalid addresses, invented names and units.
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile

os.environ.setdefault(
    "MOVEIN_HISTORY_PATH",
    os.path.join(tempfile.mkdtemp(prefix="movein-test-"), "history.sqlite3"),
)

import config  # noqa: E402

config.TRIGGER_SENDERS = [
    "leasing.manager@example.com",
    "leasing.specialist@example.com",
    "leasing.associate@example.com",
]
config.PROPERTY_NAME = "The Property"
config.TRIGGER_SENDER_NAMES = []

import mailsource  # noqa: E402
import rules  # noqa: E402
import triggers  # noqa: E402
from models import Verdict  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# converter
# ---------------------------------------------------------------------------

def _raw(**over):
    m = {"conv_id": "CONV1", "index": 0, "subject": "New Resident Move In - 0512",
         "sender": "", "sender_name": "Leasing Lead",
         "received_text": "10/14/2026 12:33 PM", "body": "Hi Team,"}
    m.update(over)
    return m


def test_each_reading_pane_date_format_converts():
    for text in ("10/14/2026 12:33 PM", "Tue 10/14/2026 12:33 PM",
                 "Tuesday, October 14, 2026 12:33 PM"):
        (m,) = mailsource.owa_messages([_raw(received_text=text)])
        when = rules.parse_received(m["received"])
        assert when is not None, text
        # Read as local time, so compare in local time.
        local = when.astimezone()
        assert (local.year, local.month, local.day, local.hour, local.minute) == (2026, 10, 14, 12, 33)


def test_an_unreadable_date_stays_blank():
    # Blank, not a guess: rules then refuses it as evidence, and a trigger with
    # no date reads UNCLEAR.
    (m,) = mailsource.owa_messages([_raw(received_text="yesterday")])
    assert m["received"] == ""


def test_messages_older_than_the_window_are_dropped():
    from datetime import datetime, timedelta

    def stamp(days_ago):
        when = datetime.now() - timedelta(days=days_ago)
        return f"{when.month}/{when.day:02d}/{when.year} 9:00 AM"

    old = _raw(received_text=stamp(40), body="old")
    recent = _raw(received_text=stamp(2), body="recent")
    kept = mailsource.owa_messages([old, recent], days=30)
    assert [m["body"] for m in kept] == ["recent"]


def test_ids_survive_a_new_reply_shifting_positions():
    # A reply arriving moves every message one place in the pane. The id must
    # not move with it, or history double-records and notifications repeat.
    first = _raw(index=0)
    (before,) = mailsource.owa_messages([first])
    after = mailsource.owa_messages([_raw(index=0, body="new reply", received_text="10/15/2026 9:00 AM"),
                                     _raw(index=1)])
    assert before["id"] in {m["id"] for m in after}


def test_two_messages_in_the_same_minute_stay_distinct():
    a, b = mailsource.owa_messages([_raw(body="first"), _raw(body="second")])
    assert a["id"] != b["id"]


def test_sender_fields_pass_through():
    (m,) = mailsource.owa_messages([_raw(sender="auto@leasing.invalid", sender_name="")])
    assert (m["sender"], m["sender_name"]) == ("auto@leasing.invalid", "")


# ---------------------------------------------------------------------------
# extractor, in real WebKit
# ---------------------------------------------------------------------------

# Shaped like the live reading pane: subject heading, then per message an avatar
# with initials, the sender line (address for the lease email, display name for
# the reply), icon glyphs, Reply / Reply all / Forward, "To:" and the date, then
# the body. Private-use characters stand in for Outlook's icon font. Bodies keep
# their line breaks (pre-wrap), as Outlook's own body markup does; the parser
# reads one field per line.
SAMPLE_PAGE = """<!doctype html><html><head>
<style>[aria-label="Message body"], [role="document"] { white-space: pre-wrap; }</style>
</head><body>
<div role="listbox">
  <div role="option" data-convid="CONV-SAMPLE" aria-selected="true"
       aria-label="Unread Collapsed New Resident Move In - 0512">row</div>
</div>
<div role="main">
  <div role="heading"><span></span> New Resident Move In - 0512</div>
  <div class="msg">
    <div class="hdr">
      <div class="persona"><span>LL</span> <span>Leasing Lead</span>
        <span>  </span></div>
      <button>Reply</button><button>Reply all</button><button>Forward</button>
      <div><label>To:</label> <span>Team Inbox</span></div>
      <div>Tue 10/14/2026 9:05 AM</div>
    </div>
    <div aria-label="Message body">Hi Team,

This resident's lease has been generated.

Name: Resident Kilo
Phone: 555-0161
Email: resident-kilo@test.invalid
Apartment Number: #512
Lease Start Date: 11/01/2026
</div>
  </div>
  <div class="msg">
    <div class="hdr">
      <div class="persona"><span>TM</span> <span>Team Mate</span>
        <span> </span></div>
      <button>Reply</button><button>Reply all</button><button>Forward</button>
      <div><label>To:</label> <span>Leasing Lead</span></div>
      <div>Tue 10/14/2026 9:41 AM</div>
    </div>
    <div aria-label="Message body">Hi Team,

0512 has been added to all platforms.

Thank you</div>
  </div>
  <div class="msg">
    <div class="hdr">
      <div class="persona"><span>AU</span> <span>auto1234@leasing.invalid</span></div>
      <div><label>To:</label> <span>Team Inbox</span></div>
      <div>Wed 10/15/2026 7:00 AM</div>
    </div>
    <div role="document">Automated notice.</div>
  </div>
</div>
</body></html>"""


def _webkit():
    try:
        from Foundation import NSDate, NSRunLoop  # noqa: F401
        from WebKit import WKWebView  # noqa: F401
    except ImportError:
        return False
    return True


def _run_extractor(html: str):
    """Load `html` into a throwaway WKWebView and evaluate owa_extract.js."""
    from AppKit import NSApplication
    from Foundation import NSDate, NSMakeRect, NSRunLoop
    from WebKit import WKWebsiteDataStore, WKWebView, WKWebViewConfiguration

    NSApplication.sharedApplication()
    conf = WKWebViewConfiguration.alloc().init()
    conf.setWebsiteDataStore_(WKWebsiteDataStore.nonPersistentDataStore())
    view = WKWebView.alloc().initWithFrame_configuration_(NSMakeRect(0, 0, 900, 700), conf)
    view.loadHTMLString_baseURL_(html, None)

    def spin(until, seconds=15):
        loop = NSRunLoop.currentRunLoop()
        for _ in range(int(seconds / 0.05)):
            if until():
                return True
            loop.runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.05))
        return until()

    # Give the load a moment to start, then wait for it to finish.
    spin(lambda: False, 0.2)
    assert spin(lambda: not view.isLoading()), "sample page did not load"

    box = {}

    def done(result, error):
        box["result"], box["error"] = result, error

    source = (REPO / "owa_extract.js").read_text(encoding="utf-8")
    view.evaluateJavaScript_completionHandler_(source, done)
    assert spin(lambda: "result" in box), "extractor did not return"
    assert box["error"] is None, box["error"]
    return json.loads(box["result"])


def _skip_without_webkit():
    if not _webkit():
        try:
            import pytest
        except ImportError:
            return True
        pytest.skip("WebKit (pyobjc) not available")
    return False


def test_extractor_reads_each_message_with_sender_and_date():
    if _skip_without_webkit():
        return
    msgs = _run_extractor(SAMPLE_PAGE)

    assert len(msgs) == 3
    assert {m["conv_id"] for m in msgs} == {"CONV-SAMPLE"}
    assert all(m["subject"] == "New Resident Move In - 0512" for m in msgs)

    lease, reply, auto = msgs
    assert (lease["sender"], lease["sender_name"]) == ("", "Leasing Lead")
    assert (reply["sender"], reply["sender_name"]) == ("", "Team Mate")
    assert (auto["sender"], auto["sender_name"]) == ("auto1234@leasing.invalid", "")

    assert lease["received_text"] == "10/14/2026 9:05 AM"
    assert "Apartment Number: #512" in lease["body"]
    assert "added to all platforms" in reply["body"]


def test_extractor_never_takes_a_recipient_as_the_sender():
    # The reply is addressed "To: Leasing Lead". Reading that as the sender
    # would make a colleague's reply look like a trigger.
    if _skip_without_webkit():
        return
    reply = _run_extractor(SAMPLE_PAGE)[1]
    assert reply["sender_name"] != "Leasing Lead"


def test_sample_thread_end_to_end_reads_as_handled():
    # Extractor -> converter -> triggers -> rules, as the live acceptance run did.
    if _skip_without_webkit():
        return
    saved = config.TRIGGER_SENDER_NAMES
    config.TRIGGER_SENDER_NAMES = ["Leasing Lead"]
    try:
        messages = mailsource.owa_messages(_run_extractor(SAMPLE_PAGE))
        found = triggers.find_triggers(messages)
        results = rules.evaluate(messages)
    finally:
        config.TRIGGER_SENDER_NAMES = saved

    assert [t.units for t in found] == [["0512"]]
    (r,) = results
    assert r.unit == "0512"
    assert r.verdict is Verdict.HANDLED
    assert [e.kind for e in r.evidence] == ["announcement"]


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import traceback

    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]

    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
        except Exception:
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
        else:
            passed += 1
            print(f"ok    {name}")

    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
