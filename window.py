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
                 "prepare", "confirm", "sheet_closed", "open_site", "guided_done", "guided_skip"}

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
                    return
                body = message.body()
                action = str(body.get("action", "") or "")
                # Bounded: a sender or unit is short; a confirmed sheet (several
                # residents' details as JSON) is longer, but never this long.
                limit = 20000 if action == "confirm" else 200
                value = str(body.get("value", "") or "")
                if len(value) > limit:
                    return
            except Exception:
                return
            if action in BOARD_ACTIONS and _on_action is not None:
                try:
                    _on_action(action, value)
                except Exception:
                    import traceback

                    traceback.print_exc()

    _handler = BoardMessages.alloc().init()
    return _handler


def show_board(html_path: str, title: str = "Move-in triage", on_action=None) -> None:
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


def _reload(html_path: str) -> None:
    """Point the live window's webview at the current board file."""
    if _window is None:
        return

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
    Reload the window if it happens to be open; do nothing if it is not.

    Called after a background refresh so a window left open on a second monitor
    updates itself instead of showing stale data.
    """
    if _window is not None:
        _reload(os.path.abspath(html_path))


def is_open() -> bool:
    return _window is not None
