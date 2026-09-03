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


def show_board(html_path: str, title: str = "Move-In Triage") -> None:
    """
    Open (or re-focus) a native window displaying the board.

    `html_path` is a local file. WKWebView refuses to load file:// content through
    the ordinary request API for security reasons, so this uses
    loadFileURL:allowingReadAccessToURL:, which grants read access to the
    containing directory only.
    """
    global _window, _delegate

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
