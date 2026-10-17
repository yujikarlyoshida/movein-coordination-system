"""
Guided runs: the app fills, the user presses every final button.

Run with:  python -m pytest test_guided.py -v
       or: python test_guided.py

On mock pages shaped like what the read-only probes found (testpages/), in
real WebKit. A simulated user presses the final buttons when the sheet says
it is their turn. All data fictional. Skipped where WebKit is missing.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

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

import autofill  # noqa: E402
import guided  # noqa: E402

PAGES = Path(__file__).resolve().parent / "testpages"
ROOMMATES = [
    {"first": "Resident", "last": "Oscar", "email": "r-oscar@test.invalid",
     "phone": "201-555-0195", "move_in": "11/01/2026"},
    {"first": "Resident", "last": "Papa", "email": "r-papa@test.invalid",
     "phone": "201-555-0196", "move_in": "11/01/2026"},
]


def _skip():
    try:
        import WebKit  # noqa: F401
    except ImportError:
        try:
            import pytest
        except ImportError:
            return True
        pytest.skip("WebKit (pyobjc) not available")
    return False


def _view(page: Path):
    from AppKit import NSApplication
    from Foundation import NSURL, NSMakeRect
    from WebKit import WKWebsiteDataStore, WKWebView, WKWebViewConfiguration

    import outlook_web

    NSApplication.sharedApplication()
    conf = WKWebViewConfiguration.alloc().init()
    conf.setWebsiteDataStore_(WKWebsiteDataStore.nonPersistentDataStore())
    view = WKWebView.alloc().initWithFrame_configuration_(NSMakeRect(0, 0, 1000, 800), conf)
    url = NSURL.fileURLWithPath_(str(page))
    view.loadFileURL_allowingReadAccessToURL_(url, url.URLByDeletingLastPathComponent())
    outlook_web.pause(0.3)
    for _ in range(200):
        if not view.isLoading():
            break
        outlook_web.pause(0.05)
    outlook_web.pause(0.3)
    return view


def _job(**kw):
    base = dict(unit="0611", residents=[dict(r) for r in ROOMMATES], steps=["xfinity", "riseio"],
                welcome={"to": [r["email"] for r in ROOMMATES], "cc": "", "subject": "s", "body": "b"})
    base.update(kw)
    return autofill.Job(**base)


class _User:
    """Presses the final buttons when it is their turn, as a person would."""

    def __init__(self, views, unit, act=True):
        self.views, self.unit, self.act = views, unit, act
        self.turns, self.done_flag = [], False

    def js(self, site, src):
        import outlook_web

        return outlook_web.run_js(self.views[site], src)

    def progress(self, step, state, detail):
        if state != "your_turn":
            return
        self.turns.append((step, detail))
        if not self.act:
            return
        if step == "riseio":
            self.js("riseio", """(() => {
              if (document.getElementById('form').style.display === 'block') document.getElementById('create').click();
              else if (document.getElementById('dlg').classList.contains('on')) {
                document.getElementById('unit').value = %r; document.getElementById('confirm').click(); }
              return '1'; })()""" % self.unit)
        elif step == "xfinity":
            self.js("xfinity", """(() => {
              const all = [...document.querySelectorAll('prism-button')];
              const sel = document.querySelector('prism-select');
              if (sel) { const s = sel.shadowRoot.querySelector('select'); s.value = %r; }
              const go = all.find(b => b.getAttribute('data-testid') === 'resident-create-footer-continue-button');
              go.shadowRoot.querySelector('button').click();
              return '1'; })()""" % self.unit)
        elif step == "welcome":
            self.done_flag = True

    def user_done(self):
        if self.done_flag:
            self.done_flag = False
            return True
        return False


def _guide(views, user, **kw):
    hooks = guided.Hooks(views=views, show_tab=lambda s: None, progress=user.progress,
                         skipped=lambda: False, user_done=user.user_done)
    return guided.Guided(hooks, **kw)


def test_xfinity_fills_step_one_and_hands_every_later_screen_to_you():
    if _skip():
        return
    guided.LEARN_DIR = Path(tempfile.mkdtemp())
    views = {"xfinity": _view(PAGES / "xfinity_like.html")}
    user = _User(views, "0611")
    (result,) = _guide(views, user).run(_job(), ["xfinity"])
    assert result[1], result[2]
    saved = user.js("xfinity", "JSON.stringify(window.__mock.saved)")
    assert saved == [{"first": "Resident", "last": r["last"], "email": r["email"], "phone": r["phone"],
                      "unit": "0611", "movein": "11/01/2026"} for r in ROOMMATES]
    # Every screen after the first was handed over, ending with Save.
    turns = [d for s, d in user.turns]
    assert sum("press Save yourself" in d for d in turns) == 2
    assert any("move-in date is filled in" in d for d in turns)
    # Unseen screens were recorded (structure only) for later build-out.
    learned = sorted(p.name for p in guided.LEARN_DIR.iterdir())
    assert "xfinity-screen-2.json" in learned
    text = (guided.LEARN_DIR / "xfinity-screen-2.json").read_text()
    assert "r-oscar@test.invalid" not in text and "201-555" not in text


def test_riseio_fills_the_form_you_create_and_confirm_the_move_in():
    if _skip():
        return
    guided.LEARN_DIR = Path(tempfile.mkdtemp())
    views = {"riseio": _view(PAGES / "riseio_like.html")}
    user = _User(views, "0611")
    (result,) = _guide(views, user).run(_job(), ["riseio"])
    assert result[1], result[2]
    created = user.js("riseio", "JSON.stringify(window.__mock.created)")
    assert [c["email"] for c in created] == [r["email"] for r in ROOMMATES]
    assert all(c["birthday"] == ["1", "January", "1923"] for c in created)
    # Ticked by default on the form; unticked because it wasn't chosen.
    assert all(c["activation"] is False for c in created)
    moved = user.js("riseio", "JSON.stringify(window.__mock.moved)")
    assert moved == [{"unit": "0611", "date": "11/01/2026"}] * 2
    assert any("Account activation is unticked" in d for _, d in user.turns)


def test_the_activation_email_box_follows_your_choice():
    if _skip():
        return
    guided.LEARN_DIR = Path(tempfile.mkdtemp())
    views = {"riseio": _view(PAGES / "riseio_like.html")}
    user = _User(views, "0611")
    _guide(views, user).run(_job(residents=ROOMMATES[:1], send_activation_email=True), ["riseio"])
    created = user.js("riseio", "JSON.stringify(window.__mock.created)")
    assert created[0]["activation"] is True


def test_the_app_never_presses_a_final_button_itself():
    # Nobody acts on "your turn": nothing may be created or saved.
    if _skip():
        return
    guided.LEARN_DIR = Path(tempfile.mkdtemp())
    views = {"riseio": _view(PAGES / "riseio_like.html"), "xfinity": _view(PAGES / "xfinity_like.html")}
    user = _User(views, "0611", act=False)
    results = _guide(views, user, turn_timeout=2).run(_job(residents=ROOMMATES[:1]), ["xfinity", "riseio"])
    assert [ok for _, ok, _ in results] == [False, False]
    assert all("timed out waiting for you" in d for _, _, d in results)
    assert user.js("riseio", "JSON.stringify(window.__mock.created)") == []
    assert user.js("xfinity", "JSON.stringify(window.__mock.saved)") == []


def test_welcome_is_yours_with_the_exact_recipients_and_subject():
    if _skip():
        return
    views = {"outlook": None}
    user = _User(views, "0611")
    job = _job(steps=["welcome"], welcome={"to": ["r-oscar@test.invalid", "r-papa@test.invalid"],
                                           "cc": "team@example.com", "subject": "The Property | Welcome Home #0611",
                                           "body": "built-in"})
    # The welcome step only hands over; nothing runs in the page.
    g = _guide({"outlook": _view(PAGES / "riseio_like.html")}, user)
    (result,) = g.run(job, ["welcome"])
    assert result[1]
    (step, detail), = user.turns
    assert "To: r-oscar@test.invalid; r-papa@test.invalid." in detail
    assert "Cc: team@example.com." in detail and "Subject: The Property | Welcome Home #0611." in detail
    assert "your Welcome template" in detail


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
