"""
The app's own browser: one window, a tab each for Outlook, RiseIO and Xfinity.

You sign in to each yourself, MFA included; the app never sees a password. The
sessions live in WebKit's cookie store and survive restarts until each site's
own policy ends them. Every tab identifies itself honestly (WebKit's user agent
plus "MoveInTriage/1.0"); if a site refuses that, the refusal stands. Nothing
here reads tokens out of a page or calls a site's API with them.

The Outlook tab is the one the Move ins reader works in (outlook_web.py).
RiseIO and Xfinity are only signed in to, for now: the app reads nothing from
them and fills nothing in until their adapters exist and are switched on.

    import browser
    browser.open_tab("riseio")            show the window on that tab
    browser.view("outlook")               the tab's WKWebView (window hidden)
    browser.sign_in_state("xfinity")      "signed_in", "signed_out", "loading"

    python browser.py                     open the browser to sign in
    python browser.py --probe DIR         development: also evaluate JS files
                                          dropped into DIR/<site>/ (structure only)
"""

from __future__ import annotations

import os
import sys

SITES = {
    "outlook": ("Outlook", "https://outlook.office.com/mail/"),
    "riseio": ("RiseIO", "https://building.riseio.com/page/login"),
    "xfinity": ("Xfinity", "https://concierge.xc3.xfinity.com/properties"),
}
ORDER = ("outlook", "riseio", "xfinity")

# What a signed-out page looks like, per site: hosts and paths of sign-in pages.
_SIGNED_OUT = {
    "outlook": ("login.microsoftonline", "login.live", "login.microsoft"),
    "riseio": ("/page/login", "/login"),
    "xfinity": ("login", "signin", "sign-in", "auth", "cognito"),
}

# Held at module level: AppKit does not retain Python-side objects for us.
_window = None
_tabs = None
_views: dict = {}
_status = None
_controller = None
_timer = None

# When the window last showed the Outlook tab on screen (time.monotonic()).
# The Move ins reader keeps its hands off Outlook while you are using it: it
# clicks through threads in this same tab, which would fight your typing.
_outlook_seen_at = 0.0
HANDS_OFF_GRACE_SECONDS = 10.0


def _controller_class():
    """The toolbar's target. Defined once: an Objective-C class can only be
    registered once per process."""
    global _controller
    if _controller is not None:
        return _controller

    from AppKit import NSObject

    class BrowserToolbar(NSObject):
        def back_(self, _sender):
            v = _current()
            if v is not None and v.canGoBack():
                v.goBack()

        def reload_(self, _sender):
            v = _current()
            if v is not None:
                v.reload()

        def home_(self, _sender):
            name = _current_name()
            if name:
                _load(name)

        def tick_(self, _timer):
            _update_status()
            _note_outlook_on_screen()

    _controller = BrowserToolbar.alloc().init()
    return _controller


def _note_outlook_on_screen() -> None:
    """Main thread, every second: remember if Outlook is showing to you."""
    global _outlook_seen_at
    import time

    if _window is None or _current_name() != "outlook":
        return
    if _window.isVisible() and not _window.isMiniaturized():
        _outlook_seen_at = time.monotonic()


def outlook_in_use() -> bool:
    """True while the Outlook tab is on screen, and for a few seconds after,
    so switching windows briefly does not hand control back mid-sentence.
    Safe from any thread: it only reads a timestamp."""
    import time

    return _outlook_seen_at > 0 and time.monotonic() - _outlook_seen_at < HANDS_OFF_GRACE_SECONDS


def _current_name() -> str:
    if _tabs is None:
        return ""
    item = _tabs.selectedTabViewItem()
    return str(item.identifier()) if item is not None else ""


def _current():
    return _views.get(_current_name())


def _load(name: str) -> None:
    from Foundation import NSURL, NSURLRequest

    _views[name].loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(SITES[name][1])))


def _update_status() -> None:
    v = _current()
    if _status is None or v is None:
        return
    url = v.URL()
    host = str(url.host()) if url is not None and url.host() else "loading"
    _status.setStringValue_(f"{SITES[_current_name()][0]}: {host}" + ("  (loading)" if v.isLoading() else ""))


def _build() -> None:
    """The window: a toolbar row (Back, Reload, Home, status) over the tabs."""
    global _window, _tabs, _status, _timer

    from AppKit import (
        NSBackingStoreBuffered,
        NSButton,
        NSMakeRect,
        NSTabView,
        NSTabViewItem,
        NSTextField,
        NSView,
        NSViewHeightSizable,
        NSViewMinYMargin,
        NSViewWidthSizable,
        NSWindow,
        NSWindowStyleMaskClosable,
        NSWindowStyleMaskMiniaturizable,
        NSWindowStyleMaskResizable,
        NSWindowStyleMaskTitled,
    )
    from Foundation import NSTimer
    from WebKit import WKWebsiteDataStore, WKWebView, WKWebViewConfiguration

    import outlook_web

    W, H, BAR = 1240.0, 880.0, 40.0
    style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
             | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable)
    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, W, H), style, NSBackingStoreBuffered, False)
    win.setTitle_("Housewarm browser")
    # Closing only hides it: the sessions and the reader keep running.
    win.setReleasedWhenClosed_(False)
    win.setFrameAutosaveName_("MoveInTriageBrowser")

    root = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
    ctl = _controller_class()

    bar = NSView.alloc().initWithFrame_(NSMakeRect(0, H - BAR, W, BAR))
    bar.setAutoresizingMask_(NSViewWidthSizable | NSViewMinYMargin)
    x = 10.0
    for label, action in (("Back", "back:"), ("Reload", "reload:"), ("Home", "home:")):
        b = NSButton.alloc().initWithFrame_(NSMakeRect(x, 6, 76, 28))
        b.setTitle_(label)
        b.setBezelStyle_(1)  # rounded
        b.setTarget_(ctl)
        b.setAction_(action)
        bar.addSubview_(b)
        x += 82
    status = NSTextField.alloc().initWithFrame_(NSMakeRect(x + 10, 10, W - x - 20, 20))
    status.setEditable_(False)
    status.setBordered_(False)
    status.setDrawsBackground_(False)
    status.setAutoresizingMask_(NSViewWidthSizable)
    bar.addSubview_(status)
    root.addSubview_(bar)

    tabs = NSTabView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H - BAR))
    tabs.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
    ui = outlook_web._make_ui_delegate()
    for name in ORDER:
        conf = WKWebViewConfiguration.alloc().init()
        # One persistent store for all three: sign-ins survive restarts.
        conf.setWebsiteDataStore_(WKWebsiteDataStore.defaultDataStore())
        conf.setApplicationNameForUserAgent_(outlook_web.APP_UA_SUFFIX)
        view = WKWebView.alloc().initWithFrame_configuration_(NSMakeRect(0, 0, W, H - BAR - 30), conf)
        view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        view.setUIDelegate_(ui)
        item = NSTabViewItem.alloc().initWithIdentifier_(name)
        item.setLabel_(SITES[name][0])
        item.setView_(view)
        tabs.addTabViewItem_(item)
        _views[name] = view
    root.addSubview_(tabs)

    win.setContentView_(root)
    win.center()
    _window, _tabs, _status = win, tabs, status
    for name in ORDER:
        _load(name)
    _timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        1.0, ctl, "tick:", None, True)


def view(name: str):
    """The WKWebView for one site, building the (hidden) window if needed."""
    if _window is None:
        _build()
    return _views[name]


def open_tab(name: str):
    """Bring the browser forward on one site's tab. Returns that tab's view."""
    from AppKit import NSApp

    v = view(name)
    _tabs.selectTabViewItemWithIdentifier_(name)
    _window.makeKeyAndOrderFront_(None)
    _note_outlook_on_screen()
    NSApp.activateIgnoringOtherApps_(True)
    _update_status()
    return v


def classify(name: str, url: str, ready: bool) -> str:
    """'signed_in', 'signed_out' or 'loading', from the page address alone."""
    if not url or url == "about:blank" or not ready:
        return "loading"
    low = url.lower()
    if any(marker in low for marker in _SIGNED_OUT[name]):
        return "signed_out"
    return "signed_in"


def sign_in_state(name: str) -> str:
    """From any thread. Reads only the page's address and load state."""
    import outlook_web

    if _window is None:
        return "not_open"
    state = outlook_web.run_js(
        _views[name], "JSON.stringify({ url: location.href, ready: document.readyState === 'complete' })")
    if not isinstance(state, dict):
        return "loading"
    return classify(name, state.get("url", ""), bool(state.get("ready")))


def main() -> int:
    from AppKit import NSApplication, NSApplicationActivationPolicyRegular
    from Foundation import NSTimer

    import outlook_web

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    open_tab("outlook")
    if "--probe" in sys.argv:
        root = sys.argv[sys.argv.index("--probe") + 1]
        for name in ORDER:
            probe = outlook_web._Probe(_views[name], os.path.join(root, name))
            outlook_web._delegates.append(probe)
            NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.5, True, lambda _t, p=probe: p.tick())
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
