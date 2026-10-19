"""
The welcome page and Settings.

Run with:  python -m pytest test_welcome.py -v
       or: python test_welcome.py

Pages are rendered from fictional settings. The scripts that read Outlook (the
signed-in address, whether a folder exists, the message being written) run in
real WebKit against a mock page shaped like what the read-only probe found.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="movein-test-"))
os.environ.setdefault("MOVEIN_HISTORY_PATH", str(_TMP / "history.sqlite3"))
os.environ.setdefault("MOVEIN_TEMPLATE_PATH", str(_TMP / "no-template.html"))

import welcome  # noqa: E402
import window  # noqa: E402

SETTINGS = {
    "you": {"name": "", "email": ""},
    "property": {"name": "Example Commons", "signature": "The Commons Team",
                 "welcome_cc": "team@example.com", "folder": "Move ins"},
    "setup_complete": False,
}
SENDERS = {"addresses": ["leasing.desk@example.com"], "names": ["Leasing Lead"]}


def _ctx(**kw):
    base = {"settings": json.loads(json.dumps(SETTINGS)), "senders": SENDERS,
            "connections": [("outlook", "Email", "signed_in"), ("riseio", "RiseIO", "signed_out"),
                            ("xfinity", "Xfinity", "signed_in")]}
    base.update(kw)
    return base


def _text(page):
    page = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", page, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", page).split())


def test_every_step_renders_with_known_actions_only():
    for step in welcome.STEPS:
        page = welcome.render(step, _ctx())
        assert "<h1" in page, step
        actions = set(re.findall(r'data-action="([^"]+)"', page))
        assert actions <= window.BOARD_ACTIONS, (step, actions - window.BOARD_ACTIONS)


def test_no_symbols_or_emoji_in_any_step():
    glyphs = re.compile("[-\U0001f000-\U0001ffff☀-➿]")
    for step in welcome.STEPS:
        assert not glyphs.search(welcome.render(step, _ctx(edit=step in welcome.EDIT_STEPS))), step


def test_the_start_page_offers_set_up_join_and_demo():
    text = _text(welcome.render("start", _ctx()))
    for words in ("Set up my property", "Join with a teammate's profile", "Try it with demo data first",
                  "It only reads your mail", "You press every final button", "Nothing leaves this Mac"):
        assert words in text, words


def test_your_email_comes_from_outlook_and_a_name_is_suggested():
    page = welcome.render("you", _ctx(detected_email="jordan.lee@example.com"))
    assert 'value="jordan.lee@example.com"' in page and 'value="Jordan Lee"' in page
    assert "Read from Outlook" in page and "suggested from your email address" in page


def test_a_different_outlook_account_is_pointed_out():
    ctx = _ctx(detected_email="someone.else@example.com")
    ctx["settings"]["you"] = {"name": "Jordan Lee", "email": "jordan.lee@example.com"}
    page = welcome.render("you", ctx)
    assert 'value="jordan.lee@example.com"' in page
    assert "signed in as a different address" in page


def test_without_outlook_you_type_your_email():
    page = welcome.render("you", _ctx())
    assert "Couldn't read it from Outlook" in page and 'name="email" type="email" value=""' in page


def test_names_are_only_suggested_from_two_part_addresses():
    assert welcome.name_from_email("jordan.lee@example.com") == "Jordan Lee"
    assert welcome.name_from_email("mary_jo.bravo@example.com") == "Mary Jo Bravo"
    assert welcome.name_from_email("jlee@example.com") == ""
    assert welcome.name_from_email("") == ""


def test_the_property_step_says_whether_the_folder_exists():
    assert "Found in Outlook." in welcome.render("property", _ctx(folder_found=True))
    assert "Not found in Outlook yet" in welcome.render("property", _ctx(folder_found=False))
    page = welcome.render("property", _ctx())
    assert 'value="Example Commons"' in page and "leasing.desk@example.com" in page


def test_values_are_escaped():
    ctx = _ctx()
    ctx["settings"]["property"]["name"] = '"><script>x()</script>'
    page = welcome.render("property", ctx)
    assert "<script>x()" not in page and "&quot;&gt;&lt;script&gt;" in page


def test_systems_lists_riseio_and_xfinity_with_their_state():
    text = _text(welcome.render("systems", _ctx()))
    assert "RiseIO Sign in needed" in text and "Xfinity Signed in" in text
    assert "Email" not in text.split("RiseIO and Xfinity", 1)[1].split("Back")[0]


def test_the_template_step_and_the_share_box_follow_the_capture():
    assert "Not captured" in _text(welcome.render("template", _ctx()))
    assert "Captured" in _text(welcome.render("template", _ctx(template_captured=True)))
    assert "not captured yet" in welcome.render("done", _ctx())
    assert 'name="template" checked' in welcome.render("done", _ctx(template_captured=True))


def test_settings_mode_has_tabs_and_a_way_back_instead_of_steps():
    page = welcome.render("property", _ctx(edit=True))
    assert "Back to board" in page and 'class="steps-dots"' not in page
    for label in ("About you", "Your property", "RiseIO and Xfinity", "Welcome email",
                  "Export profile...", "Import profile..."):
        assert label in page, label


def test_problems_and_notices_are_shown():
    page = _text(welcome.render("you", _ctx(problems=["email is not an email address"], notice="Saved.")))
    assert "Email is not an email address." in page and "Saved." in page


def test_the_bridge_takes_a_settings_form_but_not_an_oversized_one():
    assert window._VALUE_LIMITS["setup_save_property"] >= 1000
    assert window._VALUE_LIMITS.get("setup_go", 200) == 200


def test_a_profile_is_summarised_before_import():
    try:
        import menubar
    except ImportError:
        return
    summary = menubar.profile_summary({"kind": "housewarm-profile", "version": 1,
                                       "property": {"name": "Example Commons"},
                                       "senders": {"addresses": ["a@example.com"], "names": ["Leasing Lead"]},
                                       "welcome_template_html": "<p>Hi</p>"})
    assert "Example Commons" in summary and "2 lease senders" in summary and "template included" in summary
    assert "Your name and email stay as they are" in summary
    assert menubar.profile_summary({"kind": "other"}) == ""
    assert menubar.profile_summary("nonsense") == ""


def test_every_setup_step_says_what_to_do():
    for step in ("outlook", "you", "property", "systems", "template"):
        page = welcome.render(step, _ctx())
        assert 'class="howto-steps"' in page, step
        assert "What to do" in page, step
    assert "Why this is needed" in welcome.render("outlook", _ctx())
    assert "How a move-in works from here" in welcome.render("done", _ctx())


def test_the_start_page_explains_each_choice():
    text = _text(welcome.render("start", _ctx()))
    assert "share them with teammates as a profile file" in text
    assert "sent you a profile file" in text
    assert "Nothing is signed in to and nothing real is changed" in text


def _board(guide):
    import board
    import sample_data
    import rules

    with sample_data.fixture_config():
        results = rules.evaluate(sample_data.MESSAGES)
    return board.render(results, senders=SENDERS, connections=[], settings_button=True, guide=guide)


def test_the_board_shows_getting_started_until_hidden():
    page = _board(True)
    text = _text(page)
    assert "Getting started" in text and "Got it, hide this" in text
    for words in ("What the statuses mean", "Doing a move-in", "Reply On it", "Start guided run",
                  "Added to all platforms"):
        assert words in text, words
    assert set(re.findall(r'data-action="([^"]+)"', page)) <= window.BOARD_ACTIONS
    hidden = _board(False)
    assert "Getting started" not in _text(hidden) and 'data-action="guide_show"' in hidden
    assert 'data-action="guide_show"' not in _board(None)


def test_hiding_the_guide_is_remembered():
    import settings

    os.environ["MOVEIN_SETTINGS_PATH"] = str(_TMP / "settings-guide.json")
    try:
        assert settings.load()["guide_hidden"] is False
        settings.set_guide_hidden(True)
        assert settings.load()["guide_hidden"] is True
        settings.set_guide_hidden(False)
        assert settings.load()["guide_hidden"] is False
    finally:
        os.environ.pop("MOVEIN_SETTINGS_PATH", None)


def test_motion_switches_off_for_reduce_motion_and_never_hides_the_page():
    page = _board(True)
    assert "prefers-reduced-motion: reduce" in page
    # The entrance is a class that a timer always removes, so a paused
    # animation can never leave the board invisible.
    assert "classList.remove('intro')" in page and "2800" in page
    # The count-up always lands on the real number.
    assert "el.textContent = String(end)" in page
    step = welcome.render("you", _ctx())
    assert 'class="intro"' in step and "classList.remove('intro')" in step


def test_scroll_reveal_only_hides_while_its_script_runs():
    import board

    page = _board(True)
    assert "getBoundingClientRect" in page and "root.classList.add('scrolly')" in page
    # Hidden states are scoped to .scrolly, which only the script adds.
    for rule in re.findall(r"[^{}]*opacity: 0[^}]*}", board.CSS):
        if ".reveal" in rule:
            assert ".scrolly" in rule
    assert board.SCROLL_JS in welcome.render("property", _ctx())


# ---------------------------------------------------------------------------
# the scripts that read Outlook, in real WebKit
# ---------------------------------------------------------------------------

OUTLOOK_LIKE = """<!doctype html><html><body>
<div role="tree">
  <div role="treeitem" id="primaryMailboxRoot_FAKE@ffff" title="jordan.lee@example.com"><span>jordan.lee@example.com</span></div>
  <div role="treeitem" title="Inbox">Inbox</div>
  <div role="treeitem" title="Move ins">Move ins</div>
</div>
<div role="main">
  <div contenteditable="true" aria-label="Message body" style="width:400px;height:100px">
    <p>Hello (Resident Name),</p><p>Welcome home.</p></div>
</div></body></html>"""


def _webview():
    try:
        import test_guided
    except ImportError:
        return None
    if test_guided._skip():
        return None
    page = _TMP / "outlook_like_account.html"
    page.write_text(OUTLOOK_LIKE)
    return test_guided._view(page)


def test_the_outlook_scripts_read_account_folder_and_message():
    view = _webview()
    if view is None:
        return
    import outlook_web

    assert outlook_web.run_js(view, welcome.ACCOUNT_JS) == {"email": "jordan.lee@example.com"}
    assert outlook_web.run_js(view, welcome.FOLDER_JS % json.dumps("Move ins")) == {"found": True}
    assert outlook_web.run_js(view, welcome.FOLDER_JS % json.dumps("move ins ")) == {"found": True}
    assert outlook_web.run_js(view, welcome.FOLDER_JS % json.dumps("New leases")) == {"found": False}
    got = outlook_web.run_js(view, welcome.CAPTURE_JS)
    assert got["ok"] and "(Resident Name)" in got["html"]
    # No account showing: nothing, rather than a guess.
    outlook_web.run_js(view, "(() => { document.querySelector('[id^=primaryMailboxRoot_]').remove(); return '1'; })()")
    assert outlook_web.run_js(view, welcome.ACCOUNT_JS) == {"email": ""}


if __name__ == "__main__":
    import traceback

    tests = [(n, o) for n, o in sorted(globals().items()) if n.startswith("test_") and callable(o)]
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
