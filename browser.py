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
    "outlook": ("login.microsoftonline", "login.live", "login.microsoft", "rippling.com", "okta.com",
                "/sso", "identity-verification"),
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

# When you last typed, clicked or scrolled in the Outlook tab (time.monotonic()).
# The Move ins reader keeps its hands off Outlook while you are using it, so
# its clicks never fight yours. Having the tab open on screen is not using it.
_outlook_touched_at = 0.0
# The event monitor that sees those keystrokes and clicks. Kept, or it is freed.
_monitor = None

# During a guided run's "your turn": the instruction and Done / Skip, in this
# window's toolbar, where the person is working (the run's sheet is in the
# board window, usually behind this one). Set by show_turn, cleared by hide_turn.
_turn = {"label": None, "done": None, "skip": None, "on_done": None, "on_skip": None}
HANDS_OFF_GRACE_SECONDS = 15.0


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

        def turnDone_(self, _sender):
            fn = _turn.get("on_done")
            hide_turn()
            if fn:
                fn()

        def turnSkip_(self, _sender):
            fn = _turn.get("on_skip")
            hide_turn()
            if fn:
                fn()

    _controller = BrowserToolbar.alloc().init()
    return _controller


def counts_as_outlook_use(in_this_window: bool, tab: str, inside_outlook: bool) -> bool:
    """A keystroke, click or scroll counts only when it lands in the Outlook
    page itself: not the toolbar (Done, Skip step), not another tab."""
    return in_this_window and tab == "outlook" and inside_outlook


def note_outlook_use() -> None:
    global _outlook_touched_at
    import time

    _outlook_touched_at = time.monotonic()


def _watch_outlook_use(event):
    """Main thread, for each key press, click or scroll in this app. Passes
    every event on untouched; only notes the time when it was Outlook use."""
    try:
        if _window is not None and event.window() is _window:
            page = _views.get("outlook")
            from AppKit import NSEventTypeKeyDown

            if event.type() == NSEventTypeKeyDown:    # typing: where it goes
                target = _window.firstResponder()
            else:                                     # a click or scroll: what is under it
                target = _window.contentView().hitTest_(event.locationInWindow())
            inside = (page is not None and target is not None and hasattr(target, "isDescendantOf_")
                      and bool(target.isDescendantOf_(page)))
            if counts_as_outlook_use(True, _current_name(), inside):
                note_outlook_use()
    except Exception:  # noqa: BLE001 -- never drop your keystroke over this
        pass
    return event


def outlook_in_use() -> bool:
    """True while you are typing, clicking or scrolling in the Outlook tab, and
    for a few seconds after, so a pause mid-sentence does not hand control
    back. The tab merely being open is not use. Safe from any thread: it only
    reads a timestamp."""
    import time

    return _outlook_touched_at > 0 and time.monotonic() - _outlook_touched_at < HANDS_OFF_GRACE_SECONDS


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

    # The guided run's turn: instruction, then Done and Skip, hidden until needed.
    from AppKit import NSFont, NSViewMinXMargin

    turn_label = NSTextField.alloc().initWithFrame_(NSMakeRect(x + 10, 10, W - x - 240, 20))
    turn_label.setEditable_(False)
    turn_label.setBordered_(False)
    turn_label.setDrawsBackground_(False)
    turn_label.setFont_(NSFont.boldSystemFontOfSize_(13))
    turn_label.setAutoresizingMask_(NSViewWidthSizable)
    turn_label.cell().setLineBreakMode_(4)   # truncate the tail
    turn_label.setHidden_(True)
    bar.addSubview_(turn_label)
    done = NSButton.alloc().initWithFrame_(NSMakeRect(W - 214, 6, 96, 28))
    done.setTitle_("Done")
    done.setBezelStyle_(1)
    done.setKeyEquivalent_("\r")            # the default button
    done.setTarget_(ctl)
    done.setAction_("turnDone:")
    done.setAutoresizingMask_(NSViewMinXMargin)
    done.setHidden_(True)
    bar.addSubview_(done)
    skip = NSButton.alloc().initWithFrame_(NSMakeRect(W - 112, 6, 102, 28))
    skip.setTitle_("Skip step")
    skip.setBezelStyle_(1)
    skip.setTarget_(ctl)
    skip.setAction_("turnSkip:")
    skip.setAutoresizingMask_(NSViewMinXMargin)
    skip.setHidden_(True)
    bar.addSubview_(skip)
    _turn.update(label=turn_label, done=done, skip=skip)
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

    # Your keystrokes, clicks and scrolls in this app's windows, to tell when
    # you are using Outlook. Seen only to note the time; never kept or changed.
    global _monitor
    from AppKit import (NSEvent, NSEventMaskKeyDown, NSEventMaskLeftMouseDown,
                        NSEventMaskRightMouseDown, NSEventMaskScrollWheel)

    _monitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
        NSEventMaskKeyDown | NSEventMaskLeftMouseDown | NSEventMaskRightMouseDown | NSEventMaskScrollWheel,
        _watch_outlook_use)


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
    NSApp.activateIgnoringOtherApps_(True)
    _update_status()
    return v


def show_turn(text: str, on_done, on_skip, prefix: str = "Your turn: ") -> None:
    """Main thread. Show "Your turn: ..." with Done and Skip in the toolbar.
    Done tells the run you have pressed the final button; Skip moves past the
    step without it. The run also moves on by itself when it sees the button
    pressed, which hides the bar."""
    if _window is None:
        _build()
    _turn.update(on_done=on_done, on_skip=on_skip)
    _turn["label"].setStringValue_(prefix + " ".join(str(text).split()))
    _turn["label"].setToolTip_(str(text))
    for key in ("label", "done", "skip"):
        _turn[key].setHidden_(False)
    if _status is not None:
        _status.setHidden_(True)


def hide_turn() -> None:
    """Main thread. Back to the plain toolbar."""
    _turn.update(on_done=None, on_skip=None)
    for key in ("label", "done", "skip"):
        if _turn.get(key) is not None:
            _turn[key].setHidden_(True)
    if _status is not None:
        _status.setHidden_(False)


def classify(name: str, url: str, ready: bool, password: bool = False) -> str:
    """'signed_in', 'signed_out' or 'loading', from the page address and
    whether it is asking for a password."""
    if not url or url == "about:blank" or not ready:
        return "loading"
    low = url.lower()
    if password or any(marker in low for marker in _SIGNED_OUT[name]):
        return "signed_out"
    return "signed_in"


# What a tab shows, in structure only. "busy" is a form someone may be part way
# through, which a reload would wipe.
_PAGE_STATE_JS = r"""(() => {
  const inputs = (root, out = []) => {
    out.push(...root.querySelectorAll('input[type=password]'));
    for (const e of root.querySelectorAll('*')) if (e.shadowRoot) inputs(e.shadowRoot, out);
    return out;
  };
  return JSON.stringify({ url: location.href, ready: document.readyState === 'complete',
    password: inputs(document).some(e => e.offsetWidth || e.offsetHeight || e.getClientRects().length),
    onscreen: document.visibilityState === 'visible',
    busy: /\/(create|edit)(\/|$)/.test(location.pathname) });
})()"""


def _page_state(name: str) -> dict | None:
    import outlook_web

    state = outlook_web.run_js(_views[name], _PAGE_STATE_JS)
    return state if isinstance(state, dict) else None


def sign_in_state(name: str) -> str:
    """From any thread. What the tab shows now; never touches the page."""
    if _window is None:
        return "not_open"
    state = _page_state(name)
    if state is None:
        return "loading"
    return classify(name, state.get("url", ""), bool(state.get("ready")), bool(state.get("password")))


def verify_session(name: str) -> str:
    """From a worker thread. Prove a session is alive by reloading its tab.

    Found live: an expired session stays on the same address until the page
    reloads, so "Signed in" could be false for as long as the tab sat there.
    Never reloads a page on screen, or a form that may be part way through.
    """
    import outlook_web
    from PyObjCTools import AppHelper

    state = sign_in_state(name)
    page = _page_state(name) if state == "signed_in" else None
    if page is None or page.get("onscreen") or page.get("busy"):
        return state
    AppHelper.callAfter(_views[name].reload)
    outlook_web.pause(2)
    for _ in range(30):
        page = _page_state(name)
        if page and page.get("ready"):
            break
        outlook_web.pause(1)
    outlook_web.pause(3)    # a sign-in redirect can follow the load
    return sign_in_state(name)


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
