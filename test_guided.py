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
            if "outlook" in self.views and self.views["outlook"] is not None:
                self.js("outlook", "(() => { const s = document.getElementById('send'); "
                                   "if (s && s.offsetWidth) s.click(); return '1'; })()")
            else:
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


def test_xfinity_fills_details_unit_and_dates_and_you_press_save():
    # The unit search and the move-in date, as mapped on the live wizard, are
    # filled by the app; only each review's Save is yours.
    if _skip():
        return
    guided.LEARN_DIR = Path(tempfile.mkdtemp())
    views = {"xfinity": _view(PAGES / "xfinity_like.html")}
    user = _User(views, "0611")
    (result,) = _guide(views, user).run(_job(), ["xfinity"])
    assert result[1], result[2]
    saved = user.js("xfinity", "JSON.stringify(window.__mock.saved)")
    # APT 611, never the APT 1611 that the search "611" also returns.
    assert saved == [{"first": "Resident", "last": r["last"], "email": r["email"], "phone": r["phone"],
                      "unit": "APT 611", "movein": "11/01/2026"} for r in ROOMMATES]
    turns = [d for s, d in user.turns]
    assert len(turns) == 2 and all("press Save resident yourself" in d for d in turns)
    assert all(d.startswith("The review shows the email and unit.") for d in turns)
    # Unseen screens were recorded (structure only) for later build-out.
    learned = sorted(p.name for p in guided.LEARN_DIR.iterdir())
    assert "xfinity-screen-2.json" in learned
    text = (guided.LEARN_DIR / "xfinity-screen-2.json").read_text()
    assert "r-oscar@test.invalid" not in text and "201-555" not in text


def test_when_xfinity_refuses_the_save_the_step_stops_with_its_message():
    # Found live: "An unknown error occurred" after Save resident was taken for
    # a new screen, and the step looped until it gave up.
    if _skip():
        return
    guided.LEARN_DIR = Path(tempfile.mkdtemp())
    views = {"xfinity": _view(PAGES / "xfinity_like.html")}
    user = _User(views, "0611")
    user.js("xfinity", "(() => { window.__mock.failSave = true; return '1'; })()")
    (result,) = _guide(views, user).run(_job(residents=ROOMMATES[:1]), ["xfinity"])
    assert not result[1]
    assert "Xfinity did not save" in result[2] and "An unknown error occurred" in result[2]
    assert user.js("xfinity", "JSON.stringify(window.__mock.saved)") == []
    # One Save turn, and no looping through "new" screens afterwards.
    assert len(user.turns) == 1


def test_a_unit_xfinity_doesnt_have_is_handed_to_you_not_guessed():
    if _skip():
        return
    guided.LEARN_DIR = Path(tempfile.mkdtemp())
    views = {"xfinity": _view(PAGES / "xfinity_like.html")}
    user = _User(views, "0999", act=False)
    (result,) = _guide(views, user, turn_timeout=2).run(_job(unit="0999", residents=ROOMMATES[:1]), ["xfinity"])
    assert not result[1] and "timed out waiting for you" in result[2]
    (step, detail), = user.turns
    assert "choose unit 999" in detail
    assert user.js("xfinity", "JSON.stringify(window.__mock.saved)") == []


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


# -- auto: the app presses Save resident / Create New Resident when ticked ------


def _auto_setup(pages):
    guided.LEARN_DIR = Path(tempfile.mkdtemp())
    guided.ACTION_LOG = Path(tempfile.mkdtemp()) / "actions.log"
    return {site: _view(PAGES / page) for site, page in pages.items()}


def _log_lines():
    return guided.ACTION_LOG.read_text().splitlines() if guided.ACTION_LOG.exists() else []


class _Watcher(_User):
    """Records the countdown; optionally presses Skip step during it."""

    def __init__(self, *a, hold=False, **kw):
        super().__init__(*a, **kw)
        self.counts, self.hold, self._skip_once = [], hold, False

    def progress(self, step, state, detail):
        if state == "auto":
            self.counts.append((step, detail))
            if self.hold:
                self._skip_once = True
        super().progress(step, state, detail)

    def skipped(self):
        if self._skip_once:
            self._skip_once, self.hold = False, False
            return True
        return False


def _auto_guide(views, user, **kw):
    hooks = guided.Hooks(views=views, show_tab=lambda s: None, progress=user.progress,
                         skipped=user.skipped, user_done=user.user_done)
    return guided.Guided(hooks, countdown=0.3, confirm_timeout=5, **kw)


def test_auto_xfinity_presses_save_once_per_resident_after_an_exact_readback():
    if _skip():
        return
    views = _auto_setup({"xfinity": "xfinity_like.html"})
    user = _Watcher(views, "0611")
    (result,) = _auto_guide(views, user).run(_job(auto_final=["xfinity"]), ["xfinity"])
    assert result[1], result[2]
    assert "the app pressed Save resident" in result[2]
    assert [r["email"] for r in user.js("xfinity", "JSON.stringify(window.__mock.saved)")] == [r["email"] for r in ROOMMATES]
    assert user.js("xfinity", "window.__mock.presses") == 2
    # No turn was needed; each press was announced with a way to stop it.
    assert user.turns == []
    assert len(user.counts) >= 2 and all("Press Skip step to stop" in d for _, d in user.counts)
    # The log: units and outcomes, never names, emails or phones.
    lines = _log_lines()
    assert [ln.split("\t")[1:] for ln in lines] == [["unit 0611", "xfinity", "pressed Save resident"],
                                                     ["unit 0611", "xfinity", "saved"]] * 2
    text = guided.ACTION_LOG.read_text()
    assert "Oscar" not in text and "test.invalid" not in text and "555" not in text
    assert oct(guided.ACTION_LOG.stat().st_mode & 0o777) == "0o600"


def test_auto_xfinity_hands_you_a_review_that_does_not_read_back_exactly():
    if _skip():
        return
    views = _auto_setup({"xfinity": "xfinity_like.html"})
    user = _Watcher(views, "0611")
    user.js("xfinity", "(() => { window.__mock.reviewOmits = 'phone'; return '1'; })()")
    (result,) = _auto_guide(views, user).run(_job(residents=ROOMMATES[:1], auto_final=["xfinity"]), ["xfinity"])
    assert result[1], result[2]
    (step, detail), = user.turns
    assert detail.startswith("The review did not read back exactly (phone), so this one is yours.")
    assert "you pressed Save" in result[2]
    assert user.counts == [] and _log_lines() == []


def test_auto_xfinity_refused_stops_after_one_press():
    if _skip():
        return
    views = _auto_setup({"xfinity": "xfinity_like.html"})
    user = _Watcher(views, "0611")
    user.js("xfinity", "(() => { window.__mock.failSave = true; return '1'; })()")
    (result,) = _auto_guide(views, user).run(_job(residents=ROOMMATES[:1], auto_final=["xfinity"]), ["xfinity"])
    assert not result[1]
    assert "An unknown error occurred" in result[2] and "pressed once and not again" in result[2]
    assert user.js("xfinity", "window.__mock.presses") == 1
    assert user.js("xfinity", "JSON.stringify(window.__mock.saved)") == []
    assert [ln.split("\t")[3] for ln in _log_lines()] == ["pressed Save resident", "refused"]


def test_skip_during_the_countdown_holds_the_press_and_the_button_is_yours():
    if _skip():
        return
    views = _auto_setup({"xfinity": "xfinity_like.html"})
    user = _Watcher(views, "0611", hold=True)
    (result,) = _auto_guide(views, user).run(_job(residents=ROOMMATES[:1], auto_final=["xfinity"]), ["xfinity"])
    assert result[1], result[2]
    # Held: the app pressed nothing; the person did, on their turn.
    assert _log_lines() == []
    assert len(user.turns) == 1 and "press Save resident yourself" in user.turns[0][1]
    assert user.js("xfinity", "window.__mock.presses") == 1


def test_auto_riseio_creates_and_leaves_confirm_move_in_to_you():
    if _skip():
        return
    views = _auto_setup({"riseio": "riseio_like.html"})
    user = _Watcher(views, "0611")
    (result,) = _auto_guide(views, user).run(_job(auto_final=["riseio"]), ["riseio"])
    assert result[1], result[2]
    assert "the app pressed Create New Resident, you confirmed each move-in" in result[2]
    created = user.js("riseio", "JSON.stringify(window.__mock.created)")
    assert [c["email"] for c in created] == [r["email"] for r in ROOMMATES]
    assert all(c["activation"] is False for c in created)
    # Only Confirm Move-In was handed over, once per resident.
    assert len(user.turns) == 2 and all("Confirm Move-In" in d for _, d in user.turns)
    assert [ln.split("\t")[3] for ln in _log_lines()] == ["pressed Create New Resident", "created"] * 2


def test_auto_riseio_refused_stops_with_riseios_words():
    if _skip():
        return
    views = _auto_setup({"riseio": "riseio_like.html"})
    user = _Watcher(views, "0611")
    user.js("riseio", "(() => { window.__mock.failCreate = true; return '1'; })()")
    (result,) = _auto_guide(views, user).run(_job(residents=ROOMMATES[:1], auto_final=["riseio"]), ["riseio"])
    assert not result[1]
    assert "already in use" in result[2] and "pressed once and not again" in result[2]
    assert user.js("riseio", "window.__mock.presses") == 1
    assert user.js("riseio", "JSON.stringify(window.__mock.created)") == []


def test_auto_is_only_for_the_systems_ticked():
    # Xfinity ticked, RiseIO not: RiseIO's Create stays a turn.
    if _skip():
        return
    views = _auto_setup({"riseio": "riseio_like.html"})
    user = _Watcher(views, "0611", act=False)
    (result,) = _auto_guide(views, user, turn_timeout=1).run(
        _job(residents=ROOMMATES[:1], auto_final=["xfinity"]), ["riseio"])
    assert not result[1] and "timed out waiting for you" in result[2]
    assert user.js("riseio", "window.__mock.presses") == 0 and user.counts == []


TEMPLATE = ('<table style="width:600px"><tr><td><img src="https://images.example.invalid/logo.png"></td></tr></table>'
            '<h2>Hello Resident Oscar and Resident Papa,</h2><p>Welcome home.</p><p>With gratitude,</p>')


def _welcome_job(html=TEMPLATE, **kw):
    return _job(steps=["welcome"], welcome=dict({
        "to": ["r-oscar@test.invalid", "r-papa@test.invalid"], "cc": "team@example.invalid",
        "subject": "The Property | Welcome Home #0611", "body": "plain",
        "html": html, "names": "Resident Oscar and Resident Papa"}, **kw))


def test_welcome_fills_everything_and_you_press_send():
    if _skip():
        return
    views = {"outlook": _view(PAGES / "outlook_like.html")}
    user = _User(views, "0611")
    (result,) = _guide(views, user).run(_welcome_job(), ["welcome"])
    assert result[1], result[2]
    (sent,) = user.js("outlook", "JSON.stringify(window.__mock.sent)")
    assert "r-oscar@test.invalid" in sent["to"] and "r-papa@test.invalid" in sent["to"]
    assert "team@example.invalid" in sent["cc"]
    assert sent["subject"] == "The Property | Welcome Home #0611"
    # Your template, intact, above the automatic signature.
    assert sent["body"].index("Hello Resident Oscar and Resident Papa,") < sent["body"].index("Sample Signature")
    assert '<table style="width:600px">' in sent["body"]
    (step, detail), = user.turns
    assert detail.startswith("Everything checks out") and "press Send yourself" in detail


def test_welcome_is_never_sent_by_the_app():
    if _skip():
        return
    views = {"outlook": _view(PAGES / "outlook_like.html")}
    user = _User(views, "0611", act=False)
    (result,) = _guide(views, user, turn_timeout=2).run(_welcome_job(), ["welcome"])
    assert not result[1] and "timed out waiting for you" in result[2]
    assert user.js("outlook", "JSON.stringify(window.__mock.sent)") == []


def test_a_message_already_in_progress_is_left_alone():
    if _skip():
        return
    views = {"outlook": _view(PAGES / "outlook_like.html")}
    user = _User(views, "0611")
    user.js("outlook", "(() => { document.getElementById('new').click(); "
                       "document.getElementById('body').innerText = 'A reply I was in the middle of writing to someone else.'; "
                       "return '1'; })()")
    (result,) = _guide(views, user).run(_welcome_job(), ["welcome"])
    assert not result[1] and "already open" in result[2]
    assert user.js("outlook", "document.getElementById('body').innerText") == \
        "A reply I was in the middle of writing to someone else."
    assert user.js("outlook", "document.getElementById('to').innerText") == ""


def test_without_a_captured_template_the_body_is_handed_to_you():
    if _skip():
        return
    views = {"outlook": _view(PAGES / "outlook_like.html")}
    user = _User(views, "0611")
    (result,) = _guide(views, user).run(_welcome_job(html=""), ["welcome"])
    assert result[1]
    (step, detail), = user.turns
    assert "Insert your Welcome template" in detail
    (sent,) = user.js("outlook", "JSON.stringify(window.__mock.sent)")
    assert sent["subject"] == "The Property | Welcome Home #0611"


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
