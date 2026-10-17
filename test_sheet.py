"""
The confirmation sheet, and the whole click-and-done flow on test data.

Run with:  python -m pytest test_sheet.py -v
       or: python test_sheet.py

The last test is the full run: the real board in real WebKit with the real
message bridge, Prepare on a unit with missing phones, Confirm refused until
they are typed in, then the values cleaned and carried out on the practice
pages. All data is fictional. WebKit tests are skipped where it is missing.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

# Never the real welcome template: a scratch location with nothing in it.
os.environ.setdefault("MOVEIN_TEMPLATE_PATH", os.path.join(tempfile.mkdtemp(prefix="movein-test-"), "no-template.txt"))

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
config.TRIGGER_SENDER_NAMES = []
config.PROPERTY_NAME = "The Property"

import board  # noqa: E402
import confirm_sheet  # noqa: E402
import rules  # noqa: E402
import sample_data  # noqa: E402
import staging  # noqa: E402

ROOMMATES = [
    {"first": "Resident", "last": "Delta", "email": "resident-delta@test.invalid", "phone": "", "move_in": "09/08/2026"},
    {"first": "Resident", "last": "Echo", "email": "resident-echo@test.invalid", "phone": "", "move_in": "09/08/2026"},
]
GAPS = [(1, ["phone"]), (2, ["phone"])]


# ---------------------------------------------------------------------------
# building and checking the sheet
# ---------------------------------------------------------------------------

def test_sheet_shows_each_resident_and_highlights_what_is_missing():
    page = confirm_sheet.build("1408", ROOMMATES, GAPS, "4:31 PM", practice=True)
    assert "Prepare unit 1408" in page
    assert "Verified first: not handled by a teammate as of 4:31 PM" in page
    assert page.count('class="f need" data-field="phone"') == 2
    assert "Needed for Xfinity" in page
    assert 'value="resident-delta@test.invalid"' in page
    assert "Confirm and prepare" in page
    # The activation email is offered, and not ticked.
    assert "data-activate>" in page and "data-activate checked" not in page


def test_on_the_real_systems_the_sheet_offers_a_guided_run():
    # Live mode: the app fills, and stops at every final button for you.
    page = confirm_sheet.build("1408", ROOMMATES, GAPS, "4:31 PM", practice=False)
    assert "Confirm and prepare" not in page
    assert "Start guided run" in page
    assert "stops at every Send, Save and Confirm for you to press" in page


def test_your_turn_rows_offer_done_and_skip():
    rows = confirm_sheet.progress_html([("RiseIO", "your_turn", "Press Create New Resident.")])
    assert 'class="your_turn"' in rows and "Your turn" in rows
    assert 'data-sheet="guided_done"' in rows and 'data-sheet="guided_skip"' in rows


def test_a_guessed_name_split_must_be_confirmed():
    page = confirm_sheet.build("0737", ROOMMATES[:1], [(1, ["name check"])], "4:31 PM", practice=True)
    assert 'data-namecheck="1"' in page


def test_validate_cleans_values_and_refuses_gaps_without_echoing_them():
    form = {"unit": "1408", "steps": ["welcome", "xfinity", "riseio"], "activate": False,
            "residents": [dict(ROOMMATES[0], phone="(201) 555-0191", email=" <Resident-Delta@test.invalid>. "),
                          dict(ROOMMATES[1], phone="+1 201 555 0192")]}
    clean, problem = confirm_sheet.validate(form)
    assert problem == ""
    assert clean["residents"][0]["phone"] == "201-555-0191"
    assert clean["residents"][0]["email"] == "resident-delta@test.invalid"
    assert clean["residents"][1]["phone"] == "201-555-0192"

    form["residents"][1]["phone"] = ""
    clean, problem = confirm_sheet.validate(form)
    assert clean is None and problem == "The 2nd resident still needs: phone."
    # Xfinity unticked: the phone is no longer needed.
    form["steps"] = ["welcome", "riseio"]
    assert confirm_sheet.validate(form)[1] == ""
    form["residents"][0]["name_unconfirmed"] = True
    assert "confirm how the name splits" in confirm_sheet.validate(form)[1]
    assert "Delta" not in confirm_sheet.validate(form)[1]


def test_board_offers_prepare_only_on_units_that_need_outreach():
    with sample_data.fixture_config():
        results = rules.evaluate(sample_data.MESSAGES)
    page = board.render(results, senders={"addresses": [], "names": []})
    prepared = {u for u in ("0735", "1408", "1520", "0901", "1204") if f'data-prepare="{u}"' in page}
    assert prepared == {"0735", "1408"}
    done = board.render(results, senders={"addresses": [], "names": []}, done_units={"0735"})
    assert 'data-prepare="0735"' not in done and ">Done<" in done


def test_the_board_file_never_holds_resident_details():
    # The sheet is injected from memory; the page written to disk has only an
    # empty place for it.
    with sample_data.fixture_config():
        results = rules.evaluate(sample_data.MESSAGES)
    page = board.render(results, senders={"addresses": [], "names": []}).lower()
    for leak in ("@test.invalid", "alpha", "delta", "echo", "201-555"):
        assert leak not in page, leak
    assert '<div id="sheet-host"></div>' in page


# ---------------------------------------------------------------------------
# the full click-and-done run, in real WebKit
# ---------------------------------------------------------------------------

def _webkit() -> bool:
    try:
        import WebKit  # noqa: F401
    except ImportError:
        return False
    return True


def _skip():
    if not _webkit():
        try:
            import pytest
        except ImportError:
            return True
        pytest.skip("WebKit (pyobjc) not available")
    return False


def _board_view(page_html: str, on_action):
    """The board as the app shows it: a file, with the app's message handler."""
    from AppKit import NSApplication
    from Foundation import NSURL, NSMakeRect
    from WebKit import WKWebsiteDataStore, WKWebView, WKWebViewConfiguration

    import outlook_web
    import window

    NSApplication.sharedApplication()
    window._on_action = on_action
    conf = WKWebViewConfiguration.alloc().init()
    conf.setWebsiteDataStore_(WKWebsiteDataStore.nonPersistentDataStore())
    conf.userContentController().addScriptMessageHandler_name_(window._message_handler(), "triage")
    view = WKWebView.alloc().initWithFrame_configuration_(NSMakeRect(0, 0, 1100, 900), conf)
    path = Path(tempfile.mkdtemp()) / "board.html"
    path.write_text(page_html, encoding="utf-8")
    url = NSURL.fileURLWithPath_(str(path))
    view.loadFileURL_allowingReadAccessToURL_(url, url.URLByDeletingLastPathComponent())
    outlook_web.pause(0.3)
    for _ in range(200):
        if not view.isLoading():
            break
        outlook_web.pause(0.05)
    outlook_web.pause(0.3)
    return view


def test_click_prepare_fill_the_gaps_confirm_and_every_step_is_done():
    if _skip():
        return
    import autofill
    import outlook_web
    import types

    with sample_data.fixture_config():
        results = rules.evaluate(sample_data.MESSAGES)
        plans = {p.unit: p for p in staging.collect(sample_data.MESSAGES)}
    sent = []
    view = _board_view(board.render(results, senders={"addresses": [], "names": []}),
                       lambda action, value: sent.append((action, value)))
    js = lambda src: outlook_web.run_js(view, src)  # noqa: E731

    # 1. Click Prepare on 1408: the board asks the app for that unit's sheet.
    js("document.querySelector('[data-prepare=\"1408\"]').click(); 'ok'")
    outlook_web.pause(0.3)
    assert ("prepare", "1408") in sent

    # 2. The app answers with the sheet (built from the prepared plan).
    portal = next(a.payload for a in plans["1408"].actions if a.kind == "internet_portal")
    residents = [{"first": r["first_name"], "last": r["last_name"], "email": r["email"],
                  "phone": r["phone"], "move_in": r["move_in"]} for r in portal["residents"]]
    result = next(r for r in results if r.unit == "1408")
    js(f"window.__sheet.open({json.dumps(confirm_sheet.build('1408', residents, result.missing, '4:31 PM', True))}); 'ok'")

    # 3. Confirm with the phones still empty: refused, fields marked, nothing sent.
    js("document.querySelector('[data-sheet=\"confirm\"]').click(); 'ok'")
    outlook_web.pause(0.2)
    assert not any(a == "confirm" for a, _ in sent)
    assert js("String(document.querySelectorAll('.sheet .f.err').length)") == 2
    assert js("document.getElementById('formerr').textContent") == "Fix the highlighted fields first."

    # 4. Type the phones the leasing team gave you, then confirm.
    js("document.getElementById('r1_phone').value = '(201) 555-0193';"
       "document.getElementById('r2_phone').value = '201.555.0194';"
       "document.querySelector('[data-sheet=\"confirm\"]').click(); 'ok'")
    outlook_web.pause(0.3)
    confirms = [v for a, v in sent if a == "confirm"]
    assert len(confirms) == 1
    form = confirm_sheet.form_from_json(confirms[0])
    assert form["steps"] == ["welcome", "xfinity", "riseio"] and form["activate"] is False

    # 5. The app checks it again and carries it out on the practice systems.
    clean, problem = confirm_sheet.validate(form)
    assert problem == "", problem
    people = [types.SimpleNamespace(first_name=r["first"], email=r["email"]) for r in clean["residents"]]
    subject, body, to, _ = staging.compose_welcome("1408", people)
    job = autofill.Job(unit="1408", residents=clean["residents"], steps=clean["steps"],
                       welcome={"to": to, "cc": "", "subject": subject, "body": body})
    from test_autofill import _view
    outcome = autofill.Runner(_view()).run(job)
    assert [(r.step, r.ok) for r in outcome] == [("welcome", True), ("xfinity", True), ("riseio", True)], \
        [r.detail for r in outcome]
    assert outcome[1].detail == "added 2 resident(s)"
    assert outcome[2].detail == "added 2 resident(s), moved into 1408, no activation email"

    # 6. The sheet shows the finished progress.
    js(f"window.__sheet.progress({json.dumps(confirm_sheet.progress_html([(autofill.STEP_LABELS[r.step], 'done', r.detail) for r in outcome]))}, true); 'ok'")
    assert js("String(document.querySelectorAll('.progress .st.done').length)") == 3


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
