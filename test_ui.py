"""
Tests for what the app shows: the menu bar model (menu_model.py).

Run with:  python -m pytest test_ui.py -v
       or: python test_ui.py

No AppKit needed; the menu is plain data until menubar.py draws it.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime

# Never the real welcome template: a scratch location with nothing in it.
os.environ.setdefault("MOVEIN_TEMPLATE_PATH", os.path.join(tempfile.mkdtemp(prefix="movein-test-"), "no-template.txt"))

os.environ["MOVEIN_SENDERS_PATH"] = os.path.join(tempfile.mkdtemp(prefix="movein-test-"), "senders.json")
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

import menu_model as mm  # noqa: E402
import rules  # noqa: E402
import sample_data  # noqa: E402

NOW = datetime(2026, 10, 16, 16, 31)


def _snap(**kw) -> mm.Snapshot:
    base = dict(results=rules.evaluate(sample_data.MESSAGES), fetched_at=NOW, state="watching")
    base.update(kw)
    return mm.Snapshot(**base)


def _labels(snap) -> list[str]:
    return [r[0] for r in mm.menu_rows(snap) if r is not None]


# ---------------------------------------------------------------------------
# the menu bar title
# ---------------------------------------------------------------------------

def test_title_counts_units_that_need_a_person():
    # Two need outreach in the sample mailbox; none are unreadable.
    assert mm.status_title(_snap()) == "Move-ins 2"


def test_title_has_no_count_when_nothing_needs_anyone():
    assert mm.status_title(_snap(results=[])) == "Move-ins"


def test_states_that_need_the_user_win_over_counts():
    # A signed-out app showing old numbers would be falsely reassuring.
    assert mm.status_title(_snap(state="signed_out")) == "Sign in"
    assert mm.status_title(_snap(state="paused")) == "Paused"
    assert mm.status_title(_snap(state="error")) == "Move-ins: check"
    assert mm.status_title(mm.Snapshot()) == "Move-ins…"


def test_title_is_plain_text():
    # The handoff rules out icon glyphs and emoji in UI strings.
    for state in ("starting", "watching", "paused", "signed_out", "loading", "error"):
        title = mm.status_title(_snap(state=state))
        assert all(ord(c) < 0x2000 or c == "…" for c in title), title


# ---------------------------------------------------------------------------
# the dropdown
# ---------------------------------------------------------------------------

def test_watching_menu_shows_state_counts_and_actions():
    labels = _labels(_snap())
    assert labels[0] == "Watching Move ins · signed in"
    assert labels[1] == "Last check 4:31 PM"
    assert "2 need outreach" in labels
    assert "#0735" in labels and "#1408" in labels
    assert "2 handled by a teammate" in labels
    assert "1 skipped, corporate" in labels
    for action in ("Open board", "Check now", "Pause watching", "Open browser", "Quit"):
        assert action in labels


def test_paused_menu_offers_resume_and_flags_stale_counts():
    labels = _labels(_snap(state="paused"))
    assert labels[0] == "Paused. Nothing is being checked"
    assert "Resume watching" in labels and "Pause watching" not in labels
    assert labels[1].endswith("counts may be out of date")


def test_signed_out_menu_leads_with_sign_in():
    rows = [r for r in mm.menu_rows(_snap(state="signed_out")) if r is not None]
    assert rows[0][0] == "Signed out of Outlook"
    assert ("Sign in to Outlook", mm.OPEN_OUTLOOK, 0) in rows


def test_a_capped_pass_says_it_is_still_reading():
    assert "still reading older threads" in _labels(_snap(more_to_read=True))[1]


def test_long_unit_lists_are_summarised():
    many = [r for r in rules.evaluate(sample_data.MESSAGES) if r.unit == "0735"] * 11
    labels = _labels(_snap(results=many))
    assert "11 need outreach" in labels
    assert "and 3 more" in labels


def test_unit_rows_open_the_board():
    rows = [r for r in mm.menu_rows(_snap()) if r is not None]
    assert ("#0735", mm.OPEN_BOARD, 1) in rows


def test_demo_menu_has_no_outlook_or_pause_controls():
    labels = _labels(_snap(demo=True))
    assert labels[0] == "Watching Move ins (demo)"
    assert "Open browser" not in labels and "Pause watching" not in labels


def test_menu_text_is_sentence_case_units_only_and_symbol_free():
    # Every row, in every state: no resident data, no icon glyphs, sentence case.
    resident_words = ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "@test.invalid")
    for state in ("starting", "watching", "paused", "signed_out", "loading", "error"):
        snap = _snap(state=state, error="Outlook did not answer")
        for label in _labels(snap):
            low = label.lower()
            assert not any(w in low for w in resident_words), label
            assert all(ord(c) < 0x2000 or c == "…" for c in label), label
            words = [w for w in label.split() if w.isalpha()]
            # Sentence case: no word after the first is capitalised, except
            # the product names Outlook and Move.
            for w in words[1:]:
                assert not w[0].isupper() or w in ("Outlook", "Move", "PM", "AM"), label


# ---------------------------------------------------------------------------
# the board
# ---------------------------------------------------------------------------

import re  # noqa: E402

import board  # noqa: E402


def _board(results=None, **kw):
    if results is None:
        results = rules.evaluate(sample_data.MESSAGES)
    return board.render(results, **kw)


def _text(page: str) -> str:
    """Visible text only: tags, the style block and entities removed."""
    page = re.sub(r"(?s)<style>.*?</style>", " ", page)
    return " ".join(re.sub(r"<[^>]+>", " ", page).replace("&#x27;", "'").split())


def test_board_headline_counts_and_order():
    page = _board(status="Watching Move ins, last check 4:31 PM")
    text = _text(page)
    assert "2 units need you" in text
    assert "Watching Move ins, last check 4:31 PM" in text
    # Count cards: need outreach, handled, skipped, check.
    for card in ("Need outreach 2", "Handled 2", "Skipped, corporate 1", "Check 0"):
        assert card in text, card
    # Rows: what needs you first, handled last.
    units = re.findall(r'<td class="unit"><a [^>]*>(\d{4})</a>', page)
    assert units[:2] == ["0735", "1408"] and units[2] == "1520" and set(units[3:]) == {"0901", "1204"}


def test_board_table_shows_move_in_checks_and_status():
    text = _text(_board())
    assert "0735 Sep 20 No teammate reply, no welcome sent Needs outreach" in text
    assert "1204 Sep 12 Teammate replied Handled" in text
    assert "0901 Sep 3 Welcome email already sent Handled" in text
    assert "1520 Sep 15 POC label" in text and "Skipped, corporate" in text


def test_board_says_so_when_nothing_needs_you():
    assert "Nothing needs you right now" in _text(_board([]))
    assert "The board fills in after the first check" in _text(_board([]))


def test_board_carries_no_resident_data_and_no_symbols():
    page = _board()
    text = _text(page).lower()
    for leak in ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot",
                 "@test.invalid", "555-01", "housing@example-quarters"):
        assert leak not in text, leak
    # No emoji or icon glyphs anywhere in the page's text.
    assert all(ord(c) < 0x2000 for c in _text(page)), [c for c in _text(page) if ord(c) >= 0x2000]


def test_units_link_to_an_outlook_search():
    assert 'href="https://outlook.office.com/mail/?q=0735"' in _board()


def test_move_in_dates_read_as_month_and_day():
    assert board._move_in("09/20/2026", datetime(2026, 10, 1)) == "Sep 20"
    assert board._move_in("01/05/2027", datetime(2026, 10, 1)) == "Jan 5, 2027"
    assert board._move_in("", datetime(2026, 10, 1)) == ""
    assert board._move_in("TBD", datetime(2026, 10, 1)) == "TBD"


# ---------------------------------------------------------------------------
# the editable sender list
# ---------------------------------------------------------------------------

import senders  # noqa: E402


def _fresh_senders(local_config=True):
    """A new scratch list seeded from the pinned fixture config; config restored after."""
    p = senders.path()
    if p.exists():
        p.unlink()
    saved = getattr(config, "HAS_LOCAL_CONFIG", True)
    config.HAS_LOCAL_CONFIG = local_config
    try:
        return senders.load()
    finally:
        config.HAS_LOCAL_CONFIG = saved


def _restore_config():
    config.TRIGGER_SENDERS = list(sample_data.FIXTURE_SENDERS)
    config.TRIGGER_SENDER_NAMES = []


def test_the_list_is_seeded_from_config_the_first_time():
    try:
        data = _fresh_senders()
        assert data == {"addresses": list(sample_data.FIXTURE_SENDERS), "names": []}
        assert senders.path().stat().st_mode & 0o777 == 0o600
    finally:
        _restore_config()


def test_a_fresh_install_starts_with_no_senders():
    # No config_local.py: the public example addresses are placeholders, not
    # people, and must not appear as senders the user never added.
    try:
        assert _fresh_senders(local_config=False) == {"addresses": [], "names": []}
    finally:
        _restore_config()


def test_adding_an_address_or_a_name_updates_what_the_app_reads():
    try:
        _fresh_senders()
        ok, msg = senders.add("  New.Leasing@Example.com ")
        assert ok and msg.startswith("Added the address")
        assert "new.leasing@example.com" in config.TRIGGER_SENDERS
        ok, msg = senders.add("Jordan Lee")
        assert ok and msg.startswith("Added the name")
        assert "Jordan Lee" in config.TRIGGER_SENDER_NAMES
        assert senders.load()["names"] == ["Jordan Lee"]
    finally:
        _restore_config()


def test_duplicates_and_junk_are_refused_without_echoing_them():
    try:
        _fresh_senders()
        assert senders.add("leasing.manager@example.com") == (False, "That address is already on the list.")
        ok, msg = senders.add("<script>")
        assert not ok and "<script>" not in msg
        assert not senders.add("")[0]
        assert not senders.add("Jordan")[0]          # a single word is not a name
    finally:
        _restore_config()


def test_removing_takes_it_off_the_list_and_out_of_use():
    try:
        _fresh_senders()
        ok, msg = senders.remove("LEASING.MANAGER@example.com")
        assert ok and msg.startswith("Removed the address")
        assert "leasing.manager@example.com" not in config.TRIGGER_SENDERS
        assert senders.remove("nobody@example.com") == (False, "That sender wasn't on the list.")
    finally:
        _restore_config()


# ---------------------------------------------------------------------------
# the interactive board
# ---------------------------------------------------------------------------

def test_in_app_board_has_run_button_sender_panel_and_bridge():
    page = _board(senders={"addresses": ["leasing.manager@example.com"], "names": ["Leasing Lead"]},
                  unknown=[("New Associate", 3)], notice="Added the name. Checking again now.")
    assert 'data-action="run"' in page and "Run check" in page
    assert "Lease email senders" in page
    assert 'data-action="remove_sender" data-value="leasing.manager@example.com"' in page
    assert 'data-action="add_sender" data-value="New Associate"' in page
    assert "New Associate sent 3 lease emails" in _text(page)
    assert "3 lease emails not read yet" in _text(page)
    assert "Added the name. Checking again now." in _text(page)
    assert "window.webkit.messageHandlers.triage" in page


def test_the_board_never_says_nothing_while_leases_go_unread():
    # Found live: "Nothing needs you" while 16 lease emails from senders not
    # on the list had never been looked at.
    import board

    assert board._hero_lines(0, 0, 0) == ("Nothing", "needs you", "right now")
    assert board._hero_lines(0, 0, 16) == ("16 lease emails", "not read yet", "add their senders")
    assert board._hero_lines(2, 0, 16)[1] == "need you"     # real work still comes first
    page = _board(results=[], senders={"addresses": [], "names": []}, unknown=[("New Associate", 3)])
    assert "Nothing needs you" not in _text(page)


def test_plain_board_has_no_controls():
    # The CLI writes a board for a browser; nothing could receive the clicks.
    page = _board()
    assert 'data-action="run"' not in page and "Lease email senders" not in page
    assert "messageHandlers" not in page


def test_sender_values_are_escaped():
    page = _board(senders={"addresses": [], "names": ['A "Quote" <b>Bold</b>']}, unknown=[])
    assert "<b>Bold</b>" not in page and "&lt;b&gt;Bold&lt;/b&gt;" in page


# ---------------------------------------------------------------------------
# missing details: pop-up, row pill, menu line
# ---------------------------------------------------------------------------

import triggers  # noqa: E402


def _lease(body_extra="", phone="201-555-0190", email="r-hotel@test.invalid", name="Resident Hotel"):
    return {"id": "gap", "sender": "leasing.manager@example.com",
            "subject": "New Resident Move In - 0611", "received": "2026-10-01T12:00:00Z",
            "body": f"Lease generated\nName: {name}\nPhone: {phone}\nEmail: {email}\n"
                    f"Apartment Number: 0611\nLease Start Date: 11/01/2026\n{body_extra}"}


def test_missing_fields_are_named_by_field_never_by_value():
    (r,) = rules.evaluate([_lease(phone="")])
    assert r.missing == [(1, ["phone"])]
    (r,) = rules.evaluate([_lease(phone="N/A")])
    # N/A is a corporate signal too; a skipped unit is nobody's to chase.
    assert r.missing == []
    (r,) = rules.evaluate([_lease(email="")])
    assert r.missing == [(1, ["email"])]


def test_each_gap_maps_to_the_steps_it_blocks():
    assert triggers.blocked_steps(["phone"]) == ["Xfinity"]
    assert triggers.blocked_steps(["email"]) == ["Welcome email", "Xfinity", "RiseIO"]
    assert triggers.blocked_steps(["last name"]) == ["Xfinity", "RiseIO"]
    assert triggers.blocked_steps([]) == []


def test_handled_units_carry_no_gaps():
    for r in rules.evaluate(sample_data.MESSAGES):
        if r.verdict is not rules.Verdict.NEEDS_OUTREACH:
            assert r.missing == []


def _interactive(**kw):
    return _board(senders={"addresses": [], "names": []}, unknown=[], **kw)


def test_popup_opens_for_new_gaps_and_lists_them_plainly():
    page = _interactive()
    assert 'class="veil open" id="missing"' in page
    text = _text(page)
    assert "Some details are missing" in text
    assert "1408 1st resident: no usable phone. Blocks Xfinity" in text
    assert "Got it" in text


def test_popup_stays_closed_once_acknowledged_but_can_be_reopened():
    results = rules.evaluate(sample_data.MESSAGES)
    keys = set().union(*(board.missing_keys(r) for r in results))
    page = board.render(results, senders={"addresses": [], "names": []}, acknowledged=keys)
    assert 'class="veil" id="missing"' in page            # rendered, not open
    assert 'data-open-missing="1"' in page                # the row pill reopens it
    assert "Missing info" in _text(page)


def test_gap_notes_replace_the_parsers_wording_on_the_row():
    text = _text(_interactive())
    assert "no phone number given" not in text
    assert "2nd resident: no usable phone. Blocks Xfinity" in text


def test_popup_and_pill_carry_no_resident_data():
    text = _text(_interactive()).lower()
    for leak in ("delta", "echo", "@test.invalid", "555-01"):
        assert leak not in text


def test_plain_board_has_no_popup():
    assert 'id="missing"' not in _board()


def test_menu_counts_units_missing_info():
    assert "1 missing info" in _labels(_snap())


# ---------------------------------------------------------------------------
# the in-app browser: sign-in state and the Connections panel
# ---------------------------------------------------------------------------

import browser  # noqa: E402


def test_sign_in_state_is_read_from_the_page_address():
    c = browser.classify
    assert c("outlook", "https://login.microsoftonline.com/common/oauth2", True) == "signed_out"
    assert c("outlook", "https://outlook.cloud.microsoft/mail/", True) == "signed_in"
    assert c("riseio", "https://building.riseio.com/page/login", True) == "signed_out"
    assert c("riseio", "https://building.riseio.com/app/home", True) == "signed_in"
    assert c("xfinity", "https://concierge.xc3.xfinity.com/properties", True) == "signed_in"
    assert c("xfinity", "https://xc3-auth.auth.us-east-1.amazoncognito.com/login", True) == "signed_out"
    assert c("riseio", "https://building.riseio.com/app/home", False) == "loading"
    assert c("riseio", "about:blank", True) == "loading"
    # Found live: an expired session can keep its address, showing a sign-in box.
    assert c("xfinity", "https://concierge.xc3.xfinity.com/properties", True, password=True) == "signed_out"


def _session_check(page, after):
    """verify_session with its page, reload and timing stood in for."""
    import outlook_web
    from PyObjCTools import AppHelper

    reloads = []
    pages = [page, page, dict(page, ready=True)]

    class View:
        def reload(self):
            reloads.append(1)

    saved = browser._window, browser._views.get("xfinity"), browser._page_state, AppHelper.callAfter, outlook_web.pause
    browser._window, browser._views["xfinity"] = object(), View()
    browser._page_state = lambda name: pages.pop(0) if len(pages) > 1 else after
    AppHelper.callAfter, outlook_web.pause = (lambda fn, *a: fn(*a)), (lambda s: None)
    try:
        return browser.verify_session("xfinity"), len(reloads)
    finally:
        browser._window, view, browser._page_state, AppHelper.callAfter, outlook_web.pause = saved
        browser._views["xfinity"] = view


XF_PAGE = {"url": "https://concierge.xc3.xfinity.com/properties/P-1/residents", "ready": True,
           "password": False, "onscreen": False, "busy": False}


def test_a_session_that_died_behind_a_good_address_is_found_by_a_reload():
    login = dict(XF_PAGE, url="https://xc3-auth.auth.us-east-1.amazoncognito.com/login")
    assert _session_check(XF_PAGE, login) == ("signed_out", 1)
    assert _session_check(XF_PAGE, XF_PAGE) == ("signed_in", 1)


def test_a_page_in_use_is_never_reloaded():
    assert _session_check(dict(XF_PAGE, onscreen=True), XF_PAGE) == ("signed_in", 0)
    assert _session_check(dict(XF_PAGE, busy=True), XF_PAGE) == ("signed_in", 0)


def test_every_site_has_a_tab_in_a_fixed_order():
    assert browser.ORDER == ("outlook", "riseio", "xfinity")
    assert set(browser.SITES) == set(browser.ORDER)
    assert all(url.startswith("https://") for _, url in browser.SITES.values())


def test_connections_panel_shows_each_system_with_an_open_button():
    page = _board(senders={"addresses": [], "names": []}, unknown=[],
                  connections=[("outlook", "Outlook", "signed_in"), ("riseio", "RiseIO", "signed_out"),
                               ("xfinity", "Xfinity", "loading")])
    text = _text(page)
    assert "Connections" in text
    assert "Outlook Signed in" in text and "RiseIO Sign in needed" in text and "Xfinity Checking" in text
    for key in ("outlook", "riseio", "xfinity"):
        assert f'data-action="open_site" data-value="{key}"' in page


def test_board_top_bar_opens_the_browser_in_live_mode():
    live = _board(senders={"addresses": [], "names": []}, unknown=[],
                  connections=[("outlook", "Outlook", "signed_in")])
    for key, label in (("outlook", "Email"), ("riseio", "RiseIO"), ("xfinity", "Xfinity")):
        assert f'data-action="open_site" data-value="{key}">{label}</button>' in live
    demo = _board(senders={"addresses": [], "names": []}, unknown=[])
    assert 'class="sites' not in demo


def test_no_connections_panel_in_demo_or_the_plain_board():
    assert "Connections" not in _text(_board(senders={"addresses": [], "names": []}, unknown=[]))
    assert "Connections" not in _text(_board())


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


def test_a_sign_in_alert_comes_once_after_two_readings():
    out = [("riseio", "RiseIO", "signed_out")]
    good = [("riseio", "RiseIO", "signed_in")]
    readings, alerted = {}, set()
    # One reading is not enough: RiseIO opens on its login page at every launch.
    assert mm.sign_in_alerts(out, readings, alerted) == []
    assert mm.sign_in_alerts(good, readings, alerted) == []
    assert mm.sign_in_alerts(out, readings, alerted) == []
    assert mm.sign_in_alerts(out, readings, alerted) == ["RiseIO"]
    # Once per outage, and again after you have signed back in.
    assert mm.sign_in_alerts(out, readings, alerted) == []
    assert mm.sign_in_alerts(good, readings, alerted) == []
    assert mm.sign_in_alerts(out, readings, alerted) == []
    assert mm.sign_in_alerts(out, readings, alerted) == ["RiseIO"]
