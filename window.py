"""
Native macOS window for the board.

Uses AppKit and WebKit directly through pyobjc rather than a cross-platform GUI
toolkit. The reason is specific: `rumps` (the menu bar layer) already runs an
NSApplication event loop, and most Python webview libraries insist on creating
their own. Two NSApplications in one process do not coexist. Driving AppKit
ourselves means the window lives inside the event loop rumps is already running,
which is the only arrangement that reliably works.

macOS only. Everything is imported lazily inside the functions so that importing
this module on another platform -- or during the test suite -- does not fail.
"""

from __future__ import annotations

import os

# Module-level reference to the live window.
#
# This is not laziness: AppKit does not retain Python-side references for us, so a
# window created in a local variable is garbage-collected the moment the function
# returns and vanishes from the screen. Holding it here keeps it alive.
_window = None
_delegate = None

# The board's buttons ("Run check", add or remove a sender) post messages to the
# app through WebKit. `_on_action(action, value)` receives them; set by
# show_board. The handler object is created once: an Objective-C class can only
# be defined once per process.
_on_action = None
_handler = None

# The only things a board page may ask for.
BOARD_ACTIONS = {"run", "add_sender", "remove_sender", "ack_missing",
                 "prepare", "confirm", "sheet_closed", "open_site", "guided_done", "guided_skip",
                 # The welcome page and Settings (welcome.py).
                 "open_settings", "setup_go", "setup_save_you", "setup_save_property", "setup_keep",
                 "setup_capture", "setup_export", "setup_import", "setup_demo", "setup_finish",
                 "setup_close", "guide_hide", "guide_show", "setup_logo", "setup_logo_remove"}

# Longest value each action may carry; anything longer is dropped, not cut.
# A sender or unit is short; a settings form is a few fields; a confirmed sheet
# (several residents' details as JSON) is longer, but never this long.
_VALUE_LIMITS = {"confirm": 20000, "setup_save_you": 2000, "setup_save_property": 2000,
                 "setup_keep": 2000, "setup_export": 200}

def _trace(action: str, outcome: str) -> None:
    """With MOVEIN_TRACE=1, print each button press the window receives and
    what came of it. The action name only, never its value: values can be
    senders' names or a resident's details."""
    if os.environ.get("MOVEIN_TRACE"):
        import time

        print(f"[{time.strftime('%H:%M:%S')}] button {action}: {outcome}", flush=True)


# The practice systems window: where the runner fills the practice pages, in
# view, so a test run can be watched. Separate from Outlook's window and from
# the board; its own throwaway website data.
_practice_window = None


def _make_delegate():
    """
    Build a window delegate that clears our reference when the user closes it.

    Without this, closing the window would leave `_window` pointing at a dead
    object, and the next "Open board" click would try to raise a window that is no
    longer on screen.
    """
    from AppKit import NSObject

    class WindowDelegate(NSObject):
        def windowWillClose_(self, notification):
            global _window
            _window = None

    return WindowDelegate.alloc().init()


def _message_handler():
    """The WKScriptMessageHandler behind window.webkit.messageHandlers.triage."""
    global _handler
    if _handler is not None:
        return _handler

    import objc
    from AppKit import NSObject

    class BoardMessages(NSObject, protocols=[objc.protocolNamed("WKScriptMessageHandler")]):
        def userContentController_didReceiveScriptMessage_(self, controller, message):
            # Only the board itself, a local file, may ask for anything.
            try:
                url = message.frameInfo().request().URL()
                if url is None or (url.scheme() or "").lower() != "file":
                    _trace("?", "refused: not the app's own page")
                    return
                body = message.body()
                action = str(body.get("action", "") or "")
                limit = _VALUE_LIMITS.get(action, 200)
                value = str(body.get("value", "") or "")
                if len(value) > limit:
                    _trace(action, f"refused: value too long ({len(value)} > {limit})")
                    return
            except Exception as exc:  # noqa: BLE001
                _trace("?", f"refused: unreadable message ({type(exc).__name__})")
                return
            if action not in BOARD_ACTIONS:
                _trace(action, "refused: not an action the app knows")
                return
            if _on_action is None:
                _trace(action, "refused: no handler connected")
                return
            try:
                _on_action(action, value)
                _trace(action, "handled")
            except Exception as exc:  # noqa: BLE001
                import traceback

                _trace(action, f"FAILED: {type(exc).__name__}: {exc}")
                traceback.print_exc()

    _handler = BoardMessages.alloc().init()
    return _handler


def show_board(html_path: str, title: str = "Housewarm", on_action=None) -> None:
    """
    Open (or re-focus) a native window displaying the board.

    `html_path` is a local file. WKWebView refuses to load file:// content through
    the ordinary request API for security reasons, so this uses
    loadFileURL:allowingReadAccessToURL:, which grants read access to the
    containing directory only.
    """
    global _window, _delegate, _on_action
    if on_action is not None:
        _on_action = on_action

    from AppKit import (
        NSApp,
        NSBackingStoreBuffered,
        NSViewHeightSizable,
        NSViewWidthSizable,
        NSWindow,
        NSWindowStyleMaskClosable,
        NSWindowStyleMaskMiniaturizable,
        NSWindowStyleMaskResizable,
        NSWindowStyleMaskTitled,
    )
    from Foundation import NSMakeRect
    from WebKit import WKWebView, WKWebViewConfiguration

    html_path = os.path.abspath(html_path)
    if not os.path.exists(html_path):
        raise FileNotFoundError(
            f"No board at {html_path}. Refresh first -- the board is generated, "
            "not shipped."
        )

    # Already open: bring it forward rather than stacking a duplicate.
    if _window is not None:
        _window.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)
        _reload(html_path)
        return

    style = (
        NSWindowStyleMaskTitled
        | NSWindowStyleMaskClosable
        | NSWindowStyleMaskMiniaturizable
        | NSWindowStyleMaskResizable
    )

    frame = NSMakeRect(0, 0, 760, 860)
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        frame, style, NSBackingStoreBuffered, False
    )
    window.setTitle_(title)
    window.center()

    # Remember size and position between launches, so the window comes back where
    # it was left rather than always centred.
    window.setFrameAutosaveName_("MoveInTriageBoard")

    config = WKWebViewConfiguration.alloc().init()
    config.userContentController().addScriptMessageHandler_name_(_message_handler(), "triage")
    webview = WKWebView.alloc().initWithFrame_configuration_(frame, config)
    # Grow with the window rather than staying a fixed 760x860 island in it.
    webview.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)

    window.setContentView_(webview)

    _delegate = _make_delegate()
    window.setDelegate_(_delegate)

    _window = window
    _reload(html_path)

    window.makeKeyAndOrderFront_(None)
    NSApp.activateIgnoringOtherApps_(True)


# Pages mark the part that changes between redraws: <div id="live"> ...
# <!--/live--></div>. Everything else (the shader stage, the scripts) is the
# shell. While the shell is unchanged a redraw swaps only the live part, so the
# page never visibly reloads: the background keeps moving, and the scroll
# position, focus and anything being typed are kept.
LIVE_OPEN = '<div id="live">'
LIVE_CLOSE = "<!--/live-->"
_loaded_shell = None
_loaded_live = None

# Runs in the page: replace the live part, then put back what the person was
# doing. %s is the new live HTML as a JSON string.
_SWAP_JS = r"""(function (html) {
  var live = document.getElementById('live');
  if (!live) { location.reload(); return; }
  var y = window.scrollY, typed = {}, focus = document.activeElement, sel = null;
  live.querySelectorAll('input[id], textarea[id]').forEach(function (el) {
    var edited = el.type === 'checkbox' ? el.checked !== el.defaultChecked : el.value !== el.defaultValue;
    if (edited) typed[el.id] = el.type === 'checkbox' ? el.checked : el.value;
  });
  var focusId = focus && live.contains(focus) && focus.id ? focus.id : null;
  if (focusId && focus.setSelectionRange) { try { sel = [focus.selectionStart, focus.selectionEnd]; } catch (e) {} }
  live.innerHTML = html;
  Object.keys(typed).forEach(function (id) {
    var el = document.getElementById(id);
    if (!el) return;
    if (el.type === 'checkbox') el.checked = typed[id]; else el.value = typed[id];
  });
  if (focusId) {
    var f = document.getElementById(focusId);
    if (f) { f.focus(); if (sel && f.setSelectionRange) { try { f.setSelectionRange(sel[0], sel[1]); } catch (e) {} } }
  }
  window.scrollTo(0, y);
  if (window.__afterLive) window.__afterLive();
})(%s)"""


def _split(page: str):
    """(live part, shell) of a page; live is None when the page has none."""
    i = page.find(LIVE_OPEN)
    j = page.find(LIVE_CLOSE, i + 1) if i >= 0 else -1
    if i < 0 or j < 0:
        return None, page
    return page[i + len(LIVE_OPEN):j], page[:i] + page[j:]


def _remember(html_path: str) -> None:
    global _loaded_shell, _loaded_live
    try:
        with open(html_path, encoding="utf-8") as fh:
            _loaded_live, _loaded_shell = _split(fh.read())
    except OSError:
        _loaded_live = _loaded_shell = None


def _reload(html_path: str) -> None:
    """Point the live window's webview at the current board file."""
    if _window is None:
        return
    _remember(html_path)

    from Foundation import NSURL

    file_url = NSURL.fileURLWithPath_(html_path)
    directory_url = NSURL.fileURLWithPath_(os.path.dirname(html_path))

    webview = _window.contentView()
    webview.loadFileURL_allowingReadAccessToURL_(file_url, directory_url)


def run_board_js(source: str) -> None:
    """Run a script in the open board (main thread). Used to show the sheet and
    its progress without writing resident details into the board file."""
    if _window is not None:
        _window.contentView().evaluateJavaScript_completionHandler_(source, lambda *_: None)


def practice_webview():
    """Open (or re-focus) the practice systems window; returns its WKWebView."""
    global _practice_window

    from AppKit import (
        NSApp,
        NSBackingStoreBuffered,
        NSMakeRect,
        NSViewHeightSizable,
        NSViewWidthSizable,
        NSWindow,
        NSWindowStyleMaskClosable,
        NSWindowStyleMaskMiniaturizable,
        NSWindowStyleMaskResizable,
        NSWindowStyleMaskTitled,
    )
    from WebKit import WKWebsiteDataStore, WKWebView, WKWebViewConfiguration

    if _practice_window is None:
        frame = NSMakeRect(0, 0, 900, 760)
        conf = WKWebViewConfiguration.alloc().init()
        conf.setWebsiteDataStore_(WKWebsiteDataStore.nonPersistentDataStore())
        view = WKWebView.alloc().initWithFrame_configuration_(frame, conf)
        view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
                 | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable)
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, style, NSBackingStoreBuffered, False)
        win.setTitle_("Practice systems. Nothing here is saved or sent")
        win.setReleasedWhenClosed_(False)
        win.setContentView_(view)
        win.setFrameAutosaveName_("MoveInTriagePractice")
        _practice_window = win
    _practice_window.makeKeyAndOrderFront_(None)
    NSApp.activateIgnoringOtherApps_(True)
    return _practice_window.contentView()


def refresh_if_open(html_path: str) -> None:
    """
    Bring the open window up to date with the file; do nothing if it is closed.

    Called after a background refresh so a window left open on a second monitor
    updates itself instead of showing stale data. Nothing changed: nothing
    happens. Only the live part changed: it is swapped in place. The page's
    shell changed (another page altogether): a full load.
    """
    global _loaded_live
    if _window is None:
        return
    html_path = os.path.abspath(html_path)
    try:
        with open(html_path, encoding="utf-8") as fh:
            live, shell = _split(fh.read())
    except OSError:
        return
    if live is not None and shell == _loaded_shell and _loaded_live is not None:
        if live != _loaded_live:
            import json

            run_board_js(_SWAP_JS % json.dumps(live))
            _loaded_live = live
        return
    _reload(html_path)


def reload_if_open(html_path: str) -> None:
    """Load the page afresh (an image beside it changed; the HTML may not)."""
    if _window is not None:
        _reload(os.path.abspath(html_path))


def is_open() -> bool:
    return _window is not None
