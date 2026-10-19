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

# Never the real welcome template: a scratch location with nothing in it.
os.environ.setdefault("MOVEIN_TEMPLATE_PATH", os.path.join(tempfile.mkdtemp(prefix="movein-test-"), "no-template.txt"))

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
Phone: 201-555-0161
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


def _load_page(html: str, base_url: str | None = None):
    """Load `html` into a throwaway WKWebView (no stored website data)."""
    from AppKit import NSApplication
    from Foundation import NSURL, NSMakeRect
    from WebKit import WKWebsiteDataStore, WKWebView, WKWebViewConfiguration

    import outlook_web

    NSApplication.sharedApplication()
    conf = WKWebViewConfiguration.alloc().init()
    conf.setWebsiteDataStore_(WKWebsiteDataStore.nonPersistentDataStore())
    view = WKWebView.alloc().initWithFrame_configuration_(NSMakeRect(0, 0, 900, 700), conf)
    view.loadHTMLString_baseURL_(html, NSURL.URLWithString_(base_url) if base_url else None)

    # Give the load a moment to start, then wait for it to finish.
    outlook_web.pause(0.2)
    for _ in range(300):
        if not view.isLoading():
            break
        outlook_web.pause(0.05)
    assert not view.isLoading(), "sample page did not load"
    return view


def _run_extractor(html: str):
    """Load `html` and evaluate owa_extract.js on it."""
    import outlook_web

    return outlook_web.run_js(_load_page(html), outlook_web.EXTRACT_JS)


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
    assert [e.kind for e in r.evidence] == ["thread_reply", "announcement"]


# ---------------------------------------------------------------------------
# the polling loop (OutlookWebReader), on a fake folder page in real WebKit
# ---------------------------------------------------------------------------

# A folder list, a thread list and a reading pane that fills in a moment after a
# row is clicked, as Outlook's does. window.addReply() stands in for a colleague
# replying: it adds a message and changes the row's label, as a new preview does.
FOLDER_PAGE = r"""<!doctype html><html><head>
<style>[aria-label="Message body"] { white-space: pre-wrap; }</style></head><body>
<div role="tree">
  <div role="treeitem" aria-selected="true">&#xe8ea; Inbox 3 unread</div>
  <div role="treeitem" aria-selected="false">&#xe8ea;&#xe8eb; Move ins 2 unread</div>
</div>
<div role="listbox" id="list"></div>
<div role="main" id="pane"></div>
<script>
function ago(minutes) {
  const d = new Date(Date.now() - minutes * 60000);
  let h = d.getHours(), ap = h >= 12 ? 'PM' : 'AM'; h = h % 12 || 12;
  return 'Tue ' + (d.getMonth() + 1) + '/' + d.getDate() + '/' + d.getFullYear() + ' ' +
         h + ':' + String(d.getMinutes()).padStart(2, '0') + ' ' + ap;
}
const LEASE = (unit, who) => ({ who, when: ago(60 * 24),
  body: "Hi Team,\nThis resident's lease has been generated.\n\nName: Resident " + who +
        "\nPhone: 201-555-0170\nEmail: r-" + unit + "@test.invalid\nApartment Number: #" + unit +
        "\nLease Start Date: 11/01/2026\n" });
const threads = {
  'C-0512': { subject: 'New Resident Move In - 0512', label: 'Unread Collapsed lease 0512',
              msgs: [LEASE('512', 'Lima'),
                     { who: 'Team Mate', when: ago(60 * 23),
                       body: 'Hi Team,\n0512 has been added to all platforms.' }] },
  'C-0612': { subject: 'New Resident Move In - 0612', label: 'Unread lease 0612',
              msgs: [LEASE('612', 'Mike')] },
};
window.opens = 0;
function sender(m) { return m.who.startsWith('Lima') || m.who.startsWith('Mike') ? 'Leasing Lead' : m.who; }
function renderRows() {
  const list = document.getElementById('list');
  list.innerHTML = '';
  for (const [id, t] of Object.entries(threads)) {
    const row = document.createElement('div');
    row.setAttribute('role', 'option'); row.setAttribute('data-convid', id);
    row.setAttribute('aria-label', t.label); row.setAttribute('aria-selected', 'false');
    row.textContent = t.subject;
    row.addEventListener('click', () => openThread(id));
    list.appendChild(row);
  }
}
function openThread(id) {
  window.opens++;
  // Opening marks it read: Outlook drops "Unread" from the row's label.
  threads[id].label = threads[id].label.replace(/^Unread /, '');
  document.querySelectorAll('[role="option"]').forEach(o => {
    o.setAttribute('aria-selected', o.getAttribute('data-convid') === id ? 'true' : 'false');
    if (o.getAttribute('data-convid') === id) o.setAttribute('aria-label', threads[id].label);
  });
  setTimeout(() => {
    const t = threads[id];
    document.getElementById('pane').innerHTML =
      '<div role="heading"> ' + t.subject + '</div>' +
      t.msgs.map(m => '<div><div><span>LL</span> <span>' + sender(m) + '</span></div>' +
        '<button>Reply</button><div><label>To:</label> <span>Team Inbox</span></div>' +
        '<div>' + m.when + '</div><div aria-label="Message body"></div></div>').join('');
    document.querySelectorAll('[aria-label="Message body"]').forEach((b, i) => b.textContent = t.msgs[i].body);
  }, 150);
}
document.querySelectorAll('[role="treeitem"]')[1].addEventListener('click', e => {
  document.querySelectorAll('[role="treeitem"]').forEach(f => f.setAttribute('aria-selected', 'false'));
  e.currentTarget.setAttribute('aria-selected', 'true');
  setTimeout(renderRows, 150);
});
window.addReply = (id, body) => {
  threads[id].msgs.push({ who: 'Team Mate', when: ago(10), body });
  threads[id].label = 'Unread Collapsed lease ' + id + ' ' + body.length;
  renderRows();
};
</script></body></html>"""


def _verdicts(records, days=30):
    # Filtered to the lookback window exactly as the watcher does: a pass also
    # returns the first thread past the window, which it opened to find the edge.
    saved = config.TRIGGER_SENDER_NAMES
    config.TRIGGER_SENDER_NAMES = ["Leasing Lead"]
    try:
        return {r.unit: r.verdict for r in rules.evaluate(mailsource.owa_messages(records, days=days))}
    finally:
        config.TRIGGER_SENDER_NAMES = saved


def test_a_pass_opens_the_folder_and_reads_every_thread():
    if _skip_without_webkit():
        return
    import outlook_web

    view = _load_page(FOLDER_PAGE)
    reader = outlook_web.OutlookWebReader(view, wait=10)
    records = reader.read_pass()

    assert reader.last_opened == 2
    assert _verdicts(records) == {"0512": Verdict.HANDLED, "0612": Verdict.NEEDS_OUTREACH}


def test_the_reader_keeps_its_hands_off_while_you_use_outlook():
    # Found live: the reader clicked through threads in the same tab while
    # someone was composing, so their typing kept being interrupted.
    if _skip_without_webkit():
        return
    import outlook_web

    view = _load_page(FOLDER_PAGE)
    reader = outlook_web.OutlookWebReader(view, wait=10, hands_off=lambda: True)
    try:
        reader.read_pass()
        raise AssertionError("expected InUse")
    except outlook_web.InUse:
        pass
    assert reader.last_opened == 0
    # Selected nothing: the reading pane still shows no thread.
    assert outlook_web.run_js(view, "JSON.stringify(!!document.querySelector('[role=option][aria-selected=true]'))") is False


def test_a_pass_stops_midway_when_you_start_using_outlook():
    if _skip_without_webkit():
        return
    import outlook_web

    view = _load_page(FOLDER_PAGE)
    calls = {"n": 0}

    def hands_off():
        calls["n"] += 1
        return calls["n"] > 3          # you open Outlook partway through

    reader = outlook_web.OutlookWebReader(view, wait=10, hands_off=hands_off)
    try:
        reader.read_pass()
        raise AssertionError("expected InUse")
    except outlook_web.InUse:
        pass
    assert reader.last_opened < 2       # it stopped before opening the rest
    # And picks up normally once you're done.
    reader.hands_off = lambda: False
    reader.read_pass()
    assert reader.last_opened + len(reader._threads) >= 2


def test_outlook_counts_as_in_use_while_shown_and_briefly_after():
    import time

    import browser

    saved = browser._outlook_seen_at
    try:
        browser._outlook_seen_at = 0.0
        assert not browser.outlook_in_use()
        browser._outlook_seen_at = time.monotonic()
        assert browser.outlook_in_use()
        browser._outlook_seen_at = time.monotonic() - browser.HANDS_OFF_GRACE_SECONDS - 1
        assert not browser.outlook_in_use()
    finally:
        browser._outlook_seen_at = saved


def test_unchanged_threads_are_not_reopened():
    # Opening is the one interaction with the page; a pass with nothing new
    # should not touch anything, yet still report the whole folder.
    if _skip_without_webkit():
        return
    import outlook_web

    view = _load_page(FOLDER_PAGE)
    reader = outlook_web.OutlookWebReader(view, wait=10)
    reader.read_pass()
    records = reader.read_pass()

    assert reader.last_opened == 0
    assert outlook_web.run_js(view, "String(window.opens)") == 2
    assert _verdicts(records) == {"0512": Verdict.HANDLED, "0612": Verdict.NEEDS_OUTREACH}


def test_a_reply_reopens_only_that_thread_and_updates_the_verdict():
    if _skip_without_webkit():
        return
    import outlook_web

    view = _load_page(FOLDER_PAGE)
    reader = outlook_web.OutlookWebReader(view, wait=10)
    reader.read_pass()
    outlook_web.run_js(view, "window.addReply('C-0612', 'Hi Team,\\n0612 has been added to all platforms.'); '1'")
    records = reader.read_pass()

    assert reader.last_opened == 1
    assert _verdicts(records) == {"0512": Verdict.HANDLED, "0612": Verdict.HANDLED}


def test_opens_per_pass_are_capped():
    if _skip_without_webkit():
        return
    import outlook_web

    reader = outlook_web.OutlookWebReader(_load_page(FOLDER_PAGE), max_opens=1, wait=10)
    reader.read_pass()
    assert reader.last_opened == 1


def test_a_thread_that_will_not_open_is_skipped_not_fatal():
    # One slow or odd thread must not blank the board for every other unit.
    if _skip_without_webkit():
        return
    import outlook_web

    page = FOLDER_PAGE.replace("function openThread(id) {",
                               "function openThread(id) { if (id === 'C-0612') return;")
    # Long enough for the good thread to open on a busy machine; the broken one
    # never will.
    reader = outlook_web.OutlookWebReader(_load_page(page), wait=3)
    records = reader.read_pass()
    assert reader.skipped == 1
    assert _verdicts(records) == {"0512": Verdict.HANDLED}


def test_the_sign_in_page_is_reported_as_signed_out():
    if _skip_without_webkit():
        return
    import outlook_web

    view = _load_page("<html><body>Sign in</body></html>", "https://login.microsoftonline.com/")
    reader = outlook_web.OutlookWebReader(view, wait=1)
    assert reader.state() == "signed_out"
    try:
        reader.read_pass()
    except outlook_web.SignedOut:
        pass
    else:
        raise AssertionError("a sign-in page must raise SignedOut, not return an empty board")


def test_a_missing_folder_is_not_ready_rather_than_empty():
    # An empty result would read as "nothing needs you" -- the dangerous answer.
    if _skip_without_webkit():
        return
    import outlook_web

    page = FOLDER_PAGE.replace("Move ins 2 unread", "Archive")
    reader = outlook_web.OutlookWebReader(_load_page(page), wait=1)
    try:
        reader.read_pass()
    except outlook_web.NotReady:
        pass
    else:
        raise AssertionError("a missing folder must raise NotReady")


# A thread list in a short scrolling box, virtualized like Outlook's: only rows
# near the visible part exist in the page. Twelve lease threads for units 0701 to
# 0712, newest first, four days apart, so the 30-day window holds eight of them.
SCROLL_PAGE = r"""<!doctype html><html><head>
<style>
  #box { height: 100px; overflow-y: auto; }
  #inner { position: relative; }
  [role="option"] { position: absolute; height: 20px; left: 0; right: 0; }
  [aria-label="Message body"] { white-space: pre-wrap; }
</style></head><body>
<div role="tree"><div role="treeitem" aria-selected="true">&#xe8ea; Move ins</div></div>
<div id="box"><div id="inner" role="listbox"></div></div>
<div role="main" id="pane"></div>
<script>
function ago(minutes) {
  const d = new Date(Date.now() - minutes * 60000);
  let h = d.getHours(), ap = h >= 12 ? 'PM' : 'AM'; h = h % 12 || 12;
  return 'Tue ' + (d.getMonth() + 1) + '/' + d.getDate() + '/' + d.getFullYear() + ' ' +
         h + ':' + String(d.getMinutes()).padStart(2, '0') + ' ' + ap;
}
const threads = [];
for (let i = 0; i < 12; i++) {
  const unit = String(701 + i);
  threads.push({ id: 'S-' + unit, unit, when: ago(i * 4 * 24 * 60 + 60) });
}
const box = document.getElementById('box'), inner = document.getElementById('inner');
inner.style.height = (threads.length * 20) + 'px';
window.opens = 0;
function render() {
  const first = Math.max(0, Math.floor(box.scrollTop / 20) - 1);
  const last = Math.min(threads.length, first + 8);
  const selected = (document.querySelector('[aria-selected="true"][data-convid]') || {}).dataset;
  inner.innerHTML = '';
  for (let i = first; i < last; i++) {
    const t = threads[i];
    const row = document.createElement('div');
    row.setAttribute('role', 'option'); row.setAttribute('data-convid', t.id);
    row.setAttribute('aria-label', 'Unread lease ' + t.unit);
    row.setAttribute('aria-selected', selected && selected.convid === t.id ? 'true' : 'false');
    row.style.top = (i * 20) + 'px';
    row.textContent = 'Lease ' + t.unit;
    row.addEventListener('click', () => open(t));
    inner.appendChild(row);
  }
}
function open(t) {
  window.opens++;
  document.querySelectorAll('[role="option"]').forEach(o =>
    o.setAttribute('aria-selected', o.getAttribute('data-convid') === t.id ? 'true' : 'false'));
  setTimeout(() => {
    document.getElementById('pane').innerHTML =
      '<div role="heading">New Resident Move In - ' + t.unit + '</div>' +
      '<div><div><span>LL</span> <span>Leasing Lead</span></div><div><label>To:</label> <span>Team Inbox</span></div>' +
      '<div>' + t.when + '</div><div aria-label="Message body"></div></div>';
    document.querySelector('[aria-label="Message body"]').textContent =
      "Hi Team,\nThis resident's lease has been generated.\n\nName: Resident November\n" +
      "Phone: 201-555-0180\nEmail: r-" + t.unit + "@test.invalid\nApartment Number: #" + t.unit +
      "\nLease Start Date: 12/01/2026\n";
  }, 100);
}
box.addEventListener('scroll', render);
render();
</script></body></html>"""

WINDOW_UNITS = {f"0{701 + i}" for i in range(8)}


def test_a_pass_scrolls_down_to_the_lookback_window():
    # Rows below the first screenful are not even in the page until scrolled to.
    if _skip_without_webkit():
        return
    import outlook_web

    reader = outlook_web.OutlookWebReader(_load_page(SCROLL_PAGE), wait=10, max_opens=50)
    records = reader.read_pass()

    assert reader.stopped_by == "lookback window"
    assert set(_verdicts(records)) == WINDOW_UNITS
    assert set(_verdicts(records).values()) == {Verdict.NEEDS_OUTREACH}
    # It opened the eight in the window plus the first one past it, and no more.
    assert reader.last_opened == 9


def test_with_a_long_window_a_pass_reaches_the_end_of_the_list():
    if _skip_without_webkit():
        return
    import outlook_web

    reader = outlook_web.OutlookWebReader(_load_page(SCROLL_PAGE), wait=10, max_opens=50,
                                          lookback_days=1000)
    records = reader.read_pass()
    assert reader.stopped_by == "end of list"
    assert len(_verdicts(records, days=1000)) == 12


def test_the_open_cap_leaves_a_backlog_that_later_passes_clear():
    if _skip_without_webkit():
        return
    import outlook_web

    reader = outlook_web.OutlookWebReader(_load_page(SCROLL_PAGE), wait=10, max_opens=5)
    first = reader.read_pass()
    assert reader.last_opened == 5
    assert reader.backlog > 0
    assert reader.stopped_by == "open cap"     # stops rather than listing rows of unknown age
    assert len(_verdicts(first)) == 5          # incomplete, and the backlog says so

    second = reader.read_pass()
    assert reader.backlog == 0
    assert set(_verdicts(second)) == WINDOW_UNITS


def test_the_diagnostic_counts_known_senders_and_lease_details_per_thread():
    # Counts only. A thread with lease details but no known sender is the sign
    # that a sender is missing from the configured lists.
    lease = ("This resident's lease has been generated.\nName: Resident Oscar\n"
             "Email: o@test.invalid\nApartment Number: #900\nLease Start Date: 12/01/2026\n")
    raw = [
        _raw(conv_id="A", sender_name="Leasing Lead", body=lease),
        _raw(conv_id="A", sender_name="Team Mate", body="0900 added to all platforms",
             received_text="10/14/2026 1:00 PM"),
        _raw(conv_id="B", sender_name="Someone New", body=lease.replace("900", "901")),
        _raw(conv_id="C", sender_name="Leasing Lead", body="Lunch on Friday?"),
    ]
    import outlook_web

    saved = config.TRIGGER_SENDER_NAMES
    config.TRIGGER_SENDER_NAMES = ["Leasing Lead"]
    try:
        d = outlook_web.diagnose(mailsource.owa_messages(raw))
    finally:
        config.TRIGGER_SENDER_NAMES = saved
    assert d == {"threads": 3, "known_sender": 2, "lease_details": 2, "both": 1}


def test_unknown_lease_senders_names_the_original_sender_not_repliers():
    # Colleagues reply-all and quote the lease, so a reply carries the same
    # details. Only the sender of the thread's first lease email is listed, and
    # threads with a known sender are left out entirely.
    import outlook_web

    lease = ("This resident's lease has been generated.\nName: Resident Papa\n"
             "Email: p@test.invalid\nApartment Number: #910\nLease Start Date: 12/01/2026\n")
    quoted = "Done!\n\n-----Original-----\n" + lease
    raw = [
        # Known sender: not listed, even though a colleague quoted it.
        _raw(conv_id="A", sender_name="Leasing Lead", body=lease, received_text="10/14/2026 9:00 AM"),
        _raw(conv_id="A", sender_name="Team Mate", body=quoted, received_text="10/14/2026 9:30 AM"),
        # Unknown sender, two threads, each with a quoting reply.
        _raw(conv_id="B", sender="desk@leasing.invalid", sender_name="", body=lease.replace("910", "911"),
             received_text="10/14/2026 10:00 AM"),
        _raw(conv_id="B", sender_name="Team Mate", body=quoted, received_text="10/14/2026 10:30 AM"),
        _raw(conv_id="C", sender="desk@leasing.invalid", sender_name="", body=lease.replace("910", "912"),
             received_text="10/14/2026 11:00 AM"),
        # Another unknown sender, one thread.
        _raw(conv_id="D", sender_name="New Associate", body=lease.replace("910", "913"),
             received_text="10/14/2026 12:00 PM"),
        # No lease details at all: not listed.
        _raw(conv_id="E", sender_name="Somebody", body="Lunch?", received_text="10/14/2026 1:00 PM"),
    ]
    saved = config.TRIGGER_SENDER_NAMES
    config.TRIGGER_SENDER_NAMES = ["Leasing Lead"]
    try:
        listed = outlook_web.unknown_lease_senders(mailsource.owa_messages(raw))
    finally:
        config.TRIGGER_SENDER_NAMES = saved
    assert listed == [("desk@leasing.invalid", 2), ("New Associate", 1)]


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
