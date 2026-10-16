"""
Outlook on the web, inside the app's own browser window.

You sign in yourself, MFA included; the app never sees or stores a password.
The session lives in WebKit's own cookie store, like any browser's. While it is
alive the app reads mail from the page as rendered -- the message list, then the
reading pane of likely triggers -- and nothing else.

The window identifies itself honestly: WebKit's own user agent with
"MoveInTriage/1.0" appended. If Outlook or the tenant's sign-in policy refuses
that client, the refusal stands. This app does not disguise itself to get past
an access rule.

What this deliberately does NOT do:

  * read Outlook's access token out of the page to call Microsoft's APIs. That
    would use a first-party Microsoft client to get around the tenant's
    app-consent process, which is the step IT has not approved.
  * click anything that changes mail: send, reply, delete, move, flag, mark.
    Opening a message is the one interaction, and only with Outlook's
    "Don't automatically mark items as read" option on -- see the guard in the
    mail source.

macOS only; AppKit and WebKit are imported lazily.

    python outlook_web.py                     open the window and sign in
    python outlook_web.py --probe DIR         development: also evaluate JS files
                                              dropped into DIR (see _Probe)
"""

from __future__ import annotations

import json
import os
import sys

OUTLOOK_URL = "https://outlook.office.com/mail/"

# Appended to WebKit's own user agent, so the client is identifiable as what it is.
APP_UA_SUFFIX = "MoveInTriage/1.0"

# Held at module level: AppKit does not retain Python-side objects for us.
_window = None
_webview = None
_delegates: list = []


def _make_ui_delegate():
    """
    Sign-in pages sometimes open a new window; keep those in this one.

    Outlook's own pop-outs (a thread opened in a new window) start as a blank
    page and are filled in by script. Loading that blank page here would replace
    Outlook itself, so pop-outs are refused: everything stays in the main view.
    """
    from AppKit import NSObject

    class UIDelegate(NSObject):
        def webView_createWebViewWithConfiguration_forNavigationAction_windowFeatures_(
            self, webview, configuration, action, features
        ):
            url = action.request().URL()
            scheme = (url.scheme() or "").lower() if url is not None else ""
            if scheme in ("http", "https"):
                webview.loadRequest_(action.request())
            return None

    return UIDelegate.alloc().init()


def open_window(url: str = OUTLOOK_URL):
    """Open (or re-focus) the sign-in window. Returns the WKWebView."""
    global _window, _webview

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
    from Foundation import NSURL, NSURLRequest
    from WebKit import WKWebsiteDataStore, WKWebView, WKWebViewConfiguration

    if _window is not None:
        _window.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)
        return _webview

    config = WKWebViewConfiguration.alloc().init()
    # Persistent store: the sign-in survives closing the window and restarting
    # the app, until Outlook's own session policy ends it.
    config.setWebsiteDataStore_(WKWebsiteDataStore.defaultDataStore())
    config.setApplicationNameForUserAgent_(APP_UA_SUFFIX)

    frame = NSMakeRect(0, 0, 1200, 820)
    webview = WKWebView.alloc().initWithFrame_configuration_(frame, config)
    webview.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)

    ui = _make_ui_delegate()
    _delegates.append(ui)
    webview.setUIDelegate_(ui)

    style = (
        NSWindowStyleMaskTitled
        | NSWindowStyleMaskClosable
        | NSWindowStyleMaskMiniaturizable
        | NSWindowStyleMaskResizable
    )
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        frame, style, NSBackingStoreBuffered, False
    )
    window.setTitle_("Sign in to Outlook")
    window.setReleasedWhenClosed_(False)
    window.setContentView_(webview)
    window.center()
    window.makeKeyAndOrderFront_(None)
    NSApp.activateIgnoringOtherApps_(True)

    webview.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(url)))

    _window, _webview = window, webview
    return webview


class _Probe:
    """
    Development only. Every half second, if DIR/cmd.js exists, evaluate it in the
    page and write the JSON result to DIR/out.json.

    The probe scripts used while building this return structure only -- counts,
    attribute names, booleans -- never message text, so a development session
    does not copy anyone's mail into a log.
    """

    def __init__(self, webview, directory: str):
        self.webview = webview
        self.dir = directory
        os.makedirs(directory, exist_ok=True)

    def tick(self) -> None:
        cmd = os.path.join(self.dir, "cmd.js")
        if not os.path.exists(cmd):
            return
        with open(cmd, encoding="utf-8") as fh:
            source = fh.read()
        os.remove(cmd)

        def done(result, error):
            out = {"result": result, "error": str(error) if error else None}
            with open(os.path.join(self.dir, "out.json"), "w", encoding="utf-8") as fh:
                json.dump(out, fh, default=str)

        self.webview.evaluateJavaScript_completionHandler_(source, done)


def main() -> int:
    from AppKit import NSApplication, NSApplicationActivationPolicyRegular
    from Foundation import NSTimer

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)

    webview = open_window()

    if "--probe" in sys.argv:
        directory = sys.argv[sys.argv.index("--probe") + 1]
        probe = _Probe(webview, directory)
        _delegates.append(probe)
        NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.5, True, lambda _t: probe.tick())

    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
