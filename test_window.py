"""
Redraws without reloads: the window swaps only the part of a page that changed.

Run with:  python -m pytest test_window.py -v
       or: python test_window.py

Found live: every background check reloaded the whole page, so the screen
"randomly refreshed", the background restarted and typing was lost. These pin
the fix: nothing changed means nothing happens; a changed live part is swapped
in place, keeping what was typed; only a different page loads afresh.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import window

_TMP = Path(tempfile.mkdtemp(prefix="movein-test-"))


def _page(live: str, shell_note: str = "stage") -> str:
    return (f'<!doctype html><html><body><div class="backdrop">{shell_note}</div>'
            f'{window.LIVE_OPEN}{live}{window.LIVE_CLOSE}</div><script>/* page script */</script></body></html>')


def test_a_page_splits_into_live_part_and_shell():
    live, shell = window._split(_page("<p>Two units</p>"))
    assert live == "<p>Two units</p>"
    assert "Two units" not in shell and "backdrop" in shell
    assert window._split("<p>no live part</p>") == (None, "<p>no live part</p>")


class _Calls:
    def __init__(self, monkey):
        self.swaps, self.loads = [], []
        monkey["run_board_js"] = lambda src: self.swaps.append(src)
        monkey["_reload"] = lambda path: (self.loads.append(path), window._remember(path))


def _with_open_window(fn):
    saved = {k: getattr(window, k) for k in ("_window", "run_board_js", "_reload", "_loaded_shell", "_loaded_live")}
    patch = {}
    try:
        window._window = object()
        calls = _Calls(patch)
        for k, v in patch.items():
            setattr(window, k, v)
        fn(calls)
    finally:
        for k, v in saved.items():
            setattr(window, k, v)


def test_nothing_changed_means_nothing_happens():
    path = _TMP / "a.html"

    def run(calls):
        path.write_text(_page("<p>Two units</p>"))
        window._remember(str(path))
        window.refresh_if_open(str(path))
        assert calls.swaps == [] and calls.loads == []

    _with_open_window(run)


def test_a_changed_live_part_is_swapped_in_place():
    path = _TMP / "b.html"

    def run(calls):
        path.write_text(_page("<p>Two units</p>"))
        window._remember(str(path))
        path.write_text(_page("<p>Three units</p>"))
        window.refresh_if_open(str(path))
        assert calls.loads == [] and len(calls.swaps) == 1
        assert json.dumps("<p>Three units</p>") in calls.swaps[0]
        # Swapped once; the same content again does nothing more.
        window.refresh_if_open(str(path))
        assert len(calls.swaps) == 1

    _with_open_window(run)


def test_a_different_page_loads_afresh():
    path = _TMP / "c.html"

    def run(calls):
        path.write_text(_page("<p>Welcome</p>", shell_note="hero"))
        window._remember(str(path))
        path.write_text(_page("<p>Board</p>", shell_note="stage"))
        window.refresh_if_open(str(path))
        assert calls.swaps == [] and len(calls.loads) == 1

    _with_open_window(run)


def test_the_swap_keeps_what_was_typed_and_the_focus():
    try:
        import test_guided
    except ImportError:
        return
    if test_guided._skip():
        return
    import outlook_web

    page = _TMP / "swap.html"
    page.write_text(_page('<h2>Step</h2><input id="f-name" value=""><input id="f-email" value="a@example.com">'))
    view = test_guided._view(page)
    js = lambda src: outlook_web.run_js(view, src)
    js("(() => { const n = document.getElementById('f-name'); n.value = 'Jordan Lee'; n.focus(); return '1'; })()")
    view.evaluateJavaScript_completionHandler_(
        window._SWAP_JS % json.dumps('<h2>Step, updated</h2><input id="f-name" value=""><input id="f-email" value="b@example.com">'),
        lambda *_: None)
    outlook_web.pause(0.3)
    state = js("JSON.stringify({h: document.querySelector('h2').textContent, name: document.getElementById('f-name').value,"
               " email: document.getElementById('f-email').value, focus: document.activeElement.id})")
    assert state == {"h": "Step, updated", "name": "Jordan Lee", "email": "b@example.com", "focus": "f-name"}


class _Widget:
    def __init__(self):
        self.hidden, self.text, self.tip = True, "", ""

    def setHidden_(self, v):
        self.hidden = bool(v)

    def setStringValue_(self, v):
        self.text = v

    def setToolTip_(self, v):
        self.tip = v


def test_your_turn_shows_in_the_browser_toolbar_with_done_and_skip():
    # Found live: the welcome step waited for Send, but Done / Skip were on the
    # sheet in the board window, behind the browser, so there was no way on.
    try:
        import browser

        ctl = browser._controller_class()
    except ImportError:
        return
    saved = (browser._window, browser._status, dict(browser._turn))
    try:
        browser._window = object()
        browser._status = _Widget()
        browser._status.hidden = False
        browser._turn.update(label=_Widget(), done=_Widget(), skip=_Widget())
        calls = []
        browser.show_turn("Check the message, then press Send yourself.",
                          lambda: calls.append("done"), lambda: calls.append("skip"))
        assert browser._turn["label"].text == "Your turn: Check the message, then press Send yourself."
        assert not browser._turn["done"].hidden and not browser._turn["skip"].hidden
        assert browser._status.hidden
        ctl.turnDone_(None)
        assert calls == ["done"] and browser._turn["done"].hidden and not browser._status.hidden
        browser.show_turn("Press Save yourself.", lambda: calls.append("done"), lambda: calls.append("skip"))
        ctl.turnSkip_(None)
        assert calls == ["done", "skip"] and browser._turn["skip"].hidden
        ctl.turnDone_(None)              # nothing pending: does nothing
        assert calls == ["done", "skip"]
    finally:
        browser._window, browser._status = saved[0], saved[1]
        browser._turn.clear()
        browser._turn.update(saved[2])


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
