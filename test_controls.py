"""
Every control on every page reaches the app, before and after a live update.

Run with:  python -m pytest test_controls.py -v
       or: python test_controls.py

Loads the real board and welcome pages (fictional data) in real WebKit with the
app's real message bridge, clicks every button and submits every form, and
checks each one arrives as an action the app handles. Then swaps the page's
live part in place, as a background check does, and does it all again: a
handler tied to an element rather than the document dies on that swap, which
is how "Add sender" went dead.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="movein-test-"))
os.environ.setdefault("MOVEIN_HISTORY_PATH", str(_TMP / "history.sqlite3"))
os.environ.setdefault("MOVEIN_TEMPLATE_PATH", str(_TMP / "no-template.html"))

import window  # noqa: E402

_received: list[tuple[str, str]] = []


def _skip() -> bool:
    try:
        import WebKit  # noqa: F401
    except ImportError:
        try:
            import pytest
        except ImportError:
            return True
        pytest.skip("WebKit (pyobjc) not available")
    return False


def _load(html: str, name: str):
    from AppKit import NSApplication
    from Foundation import NSURL, NSMakeRect
    from WebKit import WKWebsiteDataStore, WKWebView, WKWebViewConfiguration

    import outlook_web

    page = _TMP / name
    page.write_text(html)
    NSApplication.sharedApplication()
    conf = WKWebViewConfiguration.alloc().init()
    conf.setWebsiteDataStore_(WKWebsiteDataStore.nonPersistentDataStore())
    conf.userContentController().addScriptMessageHandler_name_(window._message_handler(), "triage")
    view = WKWebView.alloc().initWithFrame_configuration_(NSMakeRect(0, 0, 1280, 900), conf)
    url = NSURL.fileURLWithPath_(str(page))
    view.loadFileURL_allowingReadAccessToURL_(url, url.URLByDeletingLastPathComponent())
    for _ in range(200):
        outlook_web.pause(0.05)
        if not view.isLoading():
            break
    outlook_web.pause(0.3)
    return view


def _js(view, src):
    import outlook_web

    return outlook_web.run_js(view, src)


def _swap(view, html: str):
    """What a background check does: replace the live part in place."""
    import outlook_web

    live, _ = window._split(html)
    view.evaluateJavaScript_completionHandler_(window._SWAP_JS % json.dumps(live), lambda *_: None)
    outlook_web.pause(0.3)


# Click every [data-action] (and [data-prepare]) control inside #live, one at a
# time, and submit every form there. Returns how many were exercised.
_CLICK_ALL = r"""(() => {
  const live = document.getElementById('live') || document.body;
  const els = [...live.querySelectorAll('[data-action], [data-prepare]')];
  els.forEach(el => { el.disabled = false; el.click(); });
  const forms = [...live.querySelectorAll('form')];
  window.__navigated = false;
  window.addEventListener('beforeunload', () => { window.__navigated = true; });
  forms.forEach(f => { const i = f.querySelector('input'); if (i) i.value = 'Leasing Lead'; f.requestSubmit(); });
  return JSON.stringify({ controls: els.length, forms: forms.length,
    expected: els.map(el => el.dataset.action || 'prepare') });
})()"""


def _exercise(view):
    import outlook_web

    _received.clear()
    info = _js(view, _CLICK_ALL)
    outlook_web.pause(0.4)
    return info, [a for a, _ in _received]


def _check(view, label):
    info, got = _exercise(view)
    from collections import Counter

    expected = Counter(list(info["expected"]) + ["add_sender"] * info["forms"])
    missing = expected - Counter(got)
    assert not missing, f"{label}: these controls did nothing: {dict(missing)}"
    return info


def _board_page():
    import board
    import rules
    import sample_data

    with sample_data.fixture_config():
        results = rules.evaluate(sample_data.MESSAGES)
    return board.render(
        results, status="Watching Move ins, last check 4:31 PM",
        senders={"addresses": ["leasing.desk@example.com"], "names": ["Leasing Lead"]},
        unknown=[("Someone New", 2)],
        connections=[("outlook", "Email", "signed_in"), ("riseio", "RiseIO", "signed_out"),
                     ("xfinity", "Xfinity", "signed_in")],
        settings_button=True, guide=True, logo="1")


def _setup():
    window._on_action = lambda action, value: _received.append((action, value))


def test_every_board_control_works_before_and_after_a_live_update():
    if _skip():
        return
    _setup()
    page = _board_page()
    view = _load(page, "board.html")
    first = _check(view, "board, first load")
    assert first["controls"] >= 10 and first["forms"] == 1
    _swap(view, page.replace("last check 4:31 PM", "last check 4:46 PM"))
    _check(view, "board, after a live update")


def _welcome_ctx(**kw):
    ctx = {"settings": {"you": {"name": "Jordan Lee", "email": "jordan@example.com"},
                        "property": {"name": "Example Commons", "signature": "Team", "welcome_cc": "",
                                     "folder": "Move ins"}, "setup_complete": False},
           "senders": {"addresses": ["leasing.desk@example.com"], "names": []},
           "connections": [("outlook", "Email", "signed_in"), ("riseio", "RiseIO", "signed_in"),
                           ("xfinity", "Xfinity", "signed_in")], "logo": "1"}
    ctx.update(kw)
    return ctx


def test_every_welcome_and_settings_control_works_before_and_after_a_live_update():
    if _skip():
        return
    import welcome

    _setup()
    for step in welcome.STEPS:
        for edit in ([False, True] if step in welcome.EDIT_STEPS else [False]):
            page = welcome.render(step, _welcome_ctx(edit=edit))
            view = _load(page, f"w-{step}-{edit}.html")
            _check(view, f"welcome {step}{' (settings)' if edit else ''}")
            if window.LIVE_OPEN in page:
                _swap(view, page)
                _check(view, f"welcome {step}{' (settings)' if edit else ''}, after a live update")


def test_every_action_a_page_sends_is_one_the_app_handles():
    import re
    import welcome

    pages = [_board_page()] + [welcome.render(s, _welcome_ctx(edit=e)) for s in welcome.STEPS for e in (False, True)]
    sent = set()
    for page in pages:
        sent |= set(re.findall(r'data-action="([^"]+)"', page))
    unknown = sent - window.BOARD_ACTIONS
    assert not unknown, unknown
    # And every allowed action has a handler in the app.
    src = (Path(__file__).parent / "menubar.py").read_text()
    for action in sent:
        assert f'"{action}"' in src or action.startswith("setup_"), action


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
