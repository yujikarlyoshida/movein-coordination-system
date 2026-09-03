"""
macOS menu bar app.

Shows a live count in the menu bar, notifies when a NEW exception appears, and
opens the board in a native window.

    python menubar.py            live data
    python menubar.py --demo     sample data, no sign-in

Why this talks to AppKit directly instead of using rumps
--------------------------------------------------------
The first version of this used `rumps`, the usual menu bar convenience wrapper.
On Python 3.14 it silently fails: the process starts, runs, and never creates a
status item -- no error, no traceback, nothing in the menu bar. rumps 0.4.0
predates this Python by years and its NSApplication delegate never fires.

pyobjc itself is fine on 3.14, and rumps is only a thin wrapper over NSStatusBar,
so this file uses NSStatusBar directly. Slightly more code, one less dependency,
and it does not depend on an unmaintained package keeping pace with Python
releases.

Threading model
---------------
AppKit demands that all UI work happen on the main thread, and a Graph fetch takes
a couple of seconds. So refreshes run on a background daemon thread which writes
its result into a lock-protected slot, and an NSTimer on the main thread drains
that slot and updates the UI. The menu never blocks on the network.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import threading
import traceback
from dataclasses import dataclass
from datetime import datetime

import objc
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSImage,
    NSImageLeft,
    NSMenu,
    NSMenuItem,
    NSObject,
    NSStatusBar,
)
from Foundation import NSTimer
from PyObjCTools import AppHelper

import app as pipeline
import board
import config
import window as native_window
from models import Result, Verdict

BOARD_FILENAME = config.OUTPUT_HTML

# The status item and its menu are held at module level rather than as attributes
# on the delegate.
#
# Storing an AppKit object on a pyobjc NSObject subclass causes it to be
# KVO-swizzled -- the object's class becomes NSKVONotifying_NSSceneStatusItem
# instead of NSSceneStatusItem. On macOS 26 a swizzled scene status item reports
# itself visible, with the right length, title and image, and draws nothing at
# all. Keeping the reference in a plain module global avoids the swizzle.
#
# These still need to be referenced somewhere permanent: an unreferenced status
# item is collected and vanishes from the menu bar.
_STATUS_ITEM = None
_MENU = None


def resolve_output_path() -> str:
    """
    Decide where to write board.html.

    A bundled .app may live in /Applications, which is not writable, so writing
    the board next to the code only works when running from source. When bundled,
    fall back to Application Support.
    """
    here = os.path.dirname(os.path.abspath(__file__))

    if ".app/Contents/" in here:
        support = os.path.expanduser("~/Library/Application Support/MoveInTriage")
        os.makedirs(support, exist_ok=True)
        return os.path.join(support, BOARD_FILENAME)

    return os.path.join(here, BOARD_FILENAME)


def notify(title: str, subtitle: str, message: str) -> None:
    """
    Post a macOS notification.

    Uses osascript rather than a notification framework on purpose. The modern
    API (UNUserNotificationCenter) refuses to deliver from a process whose bundle
    it cannot verify, and the old one (NSUserNotification) is deprecated and
    removed on recent systems. osascript works regardless of how the app was
    launched, which matters here because this runs both from source and from a
    hand-built bundle.
    """

    def esc(s: str) -> str:
        # AppleScript string literals: backslash and double quote need escaping.
        return s.replace("\\", "\\\\").replace('"', '\\"')

    script = (
        f'display notification "{esc(message)}" '
        f'with title "{esc(title)}" subtitle "{esc(subtitle)}"'
    )
    try:
        subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            timeout=10,
        )
    except Exception:
        traceback.print_exc()


@dataclass
class Snapshot:
    """One completed refresh. Handed from the worker thread to the UI thread."""

    results: list[Result]
    operator: str
    fetched_at: datetime
    error: str | None = None

    # Plain dataclass, not an NSObject subclass, so no pyobjc decorators here.
    @property
    def needs_outreach(self) -> list[Result]:
        """Triggered, with no sign anyone has picked it up. The only to-do list."""
        return [r for r in self.results if r.verdict is Verdict.NEEDS_OUTREACH]

    @property
    def unclear(self) -> list[Result]:
        """Qualifying mail that could not be read well enough to judge."""
        return [r for r in self.results if r.verdict is Verdict.UNCLEAR]

    @property
    def handled(self) -> list[Result]:
        return [r for r in self.results if r.verdict is Verdict.HANDLED]


class TriageDelegate(NSObject):
    """
    Owns the status item and the menu.

    Subclasses NSObject because AppKit needs a real Objective-C object to use as
    a menu item target. Note the pyobjc initialiser convention: `init` rather than
    `__init__`, returning self.
    """

    def initWithDemo_(self, demo):
        global _STATUS_ITEM, _MENU
        self = objc.super(TriageDelegate, self).init()
        if self is None:
            return None

        self.demo = bool(demo)
        self.board_path = resolve_output_path()

        self._lock = threading.Lock()
        self._pending = None
        self._wake = threading.Event()
        self._notified = set()

        # Status item. Retained on self so it is not collected -- an unreferenced
        # status item disappears from the menu bar.
        #
        # Explicit width rather than NSVariableStatusItemLength. On macOS 26 the
        # status item is an NSSceneStatusItem, and variable length leaves it zero
        # pixels wide: AppKit cheerfully reports isVisible=True and the correct
        # title while nothing whatsoever is drawn. That combination cost an
        # evening to track down. Give it a real width and it appears.
        _STATUS_ITEM = NSStatusBar.systemStatusBar().statusItemWithLength_(64.0)

        button = _STATUS_ITEM.button()
        button.setTitle_("…")

        # An image is NOT optional here, however much it looks like decoration.
        # On macOS 26 a scene status item showing only text does not render at
        # all -- no icon, no text, nothing, while AppKit still reports it visible.
        # Give it an image and the text appears alongside. Verified empirically on
        # 26.5.2; a title-only item was invisible and the same item with an image
        # was fine.
        #
        # Symbol availability varies by OS version, and a name that does not
        # resolve returns None, so try several and take the first that exists.
        # Ordered from most apt to most certainly present.
        icon = None
        for symbol in (
            "checklist",
            "list.bullet.clipboard",
            "list.bullet",
            "exclamationmark.triangle",  # confirmed present on 26.5.2
            "circle",
        ):
            icon = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                symbol, "Move-In Triage"
            )
            if icon is not None:
                break

        if icon is not None:
            icon.setTemplate_(True)  # let macOS tint for light/dark menu bars
            button.setImage_(icon)
            button.setImagePosition_(NSImageLeft)
        else:
            # Should not happen, but if every symbol were missing the item would
            # silently vanish, so make the failure visible rather than mysterious.
            print("warning: no status bar symbol available; item may not render")

        _MENU = NSMenu.alloc().init()
        _STATUS_ITEM.setMenu_(_MENU)
        self._render_menu(None)

        return self

    # -- menu construction --------------------------------------------------
    #
    # Everything below that is not an Objective-C entry point carries
    # @objc.python_method. pyobjc exposes every method on an NSObject subclass as
    # a selector, and a selector's argument count is derived from the underscores
    # in its name -- so a plain helper taking arguments fails at class-creation
    # time with BadPrototypeError. The decorator says "this is ordinary Python,
    # leave it alone". Only initWithDemo_, tick_, and the three action methods
    # need to be real selectors, because AppKit calls those.

    @objc.python_method
    def _item(self, label, action=None, indent=0):
        """Build one menu item. No action means a non-clickable informational row."""
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            label, action, ""
        )
        if action is not None:
            item.setTarget_(self)
        else:
            item.setEnabled_(False)
        if indent:
            item.setIndentationLevel_(indent)
        return item

    @objc.python_method
    def _render_menu(self, snap):
        _MENU.removeAllItems()

        if snap is None:
            _MENU.addItem_(self._item("Checking…"))
        elif snap.error:
            _MENU.addItem_(self._item(f"Error: {snap.error[:60]}"))
        else:
            needs = snap.needs_outreach
            unclear = snap.unclear

            if unclear:
                _MENU.addItem_(self._item(f"{len(unclear)} could not be read"))
                for r in unclear:
                    _MENU.addItem_(
                        self._item(f"#{r.unit} — {r.trigger_subject[:40]}",
                                   "openBoard:", indent=1)
                    )
                _MENU.addItem_(NSMenuItem.separatorItem())

            if needs:
                _MENU.addItem_(self._item(f"{len(needs)} need outreach"))
                for r in needs[:8]:
                    _MENU.addItem_(
                        self._item(f"#{r.unit} — {r.trigger_subject[:40]}",
                                   "openBoard:", indent=1)
                    )
                if len(needs) > 8:
                    _MENU.addItem_(self._item(f"…and {len(needs) - 8} more", indent=1))
                _MENU.addItem_(NSMenuItem.separatorItem())

            if not needs and not unclear:
                handled = len(snap.handled)
                label = (
                    f"Nothing outstanding — {handled} already handled"
                    if handled else "Nothing outstanding"
                )
                _MENU.addItem_(self._item(label))
                _MENU.addItem_(NSMenuItem.separatorItem())

            stamp = snap.fetched_at.strftime("%H:%M")
            suffix = " (demo)" if self.demo else ""
            _MENU.addItem_(self._item(f"Updated {stamp}{suffix}"))

        _MENU.addItem_(NSMenuItem.separatorItem())
        _MENU.addItem_(self._item("Open board", "openBoard:"))
        _MENU.addItem_(self._item("Refresh now", "refreshNow:"))
        _MENU.addItem_(NSMenuItem.separatorItem())
        _MENU.addItem_(self._item("Quit", "quitApp:"))

    # -- worker -------------------------------------------------------------

    def startWorker(self):
        threading.Thread(target=self._worker_loop, daemon=True).start()

    @objc.python_method
    def _worker_loop(self):
        while True:
            self._refresh_once()
            # Returns early as soon as "Refresh now" fires, so an on-demand
            # refresh does not wait out the interval.
            if self._wake.wait(timeout=config.REFRESH_INTERVAL_SECONDS):
                self._wake.clear()

    @objc.python_method
    def _refresh_once(self):
        try:
            results, operator = pipeline.collect(use_demo=self.demo)
            html = board.render(results, operator=operator)
            with open(self.board_path, "w", encoding="utf-8") as fh:
                fh.write(html)
            snap = Snapshot(results=results, operator=operator, fetched_at=datetime.now())
        except Exception as exc:
            traceback.print_exc()
            snap = Snapshot([], "", datetime.now(), error=str(exc))

        with self._lock:
            self._pending = snap

    # -- main-thread timer --------------------------------------------------

    def tick_(self, _timer):
        """Runs on the main thread. Applies a finished refresh, if one is waiting."""
        with self._lock:
            snap = self._pending
            self._pending = None

        if snap is None:
            return

        try:
            self._apply(snap)
        except Exception:
            traceback.print_exc()

    @objc.python_method
    def _set_status_title(self, title):
        """
        Set the menu bar text, resizing the item to fit.

        The width has to be maintained by hand because the item uses a fixed
        length -- see the note in initWithDemo_ about variable length rendering
        as zero pixels on macOS 26. Too small and the text is clipped ("BBBTES");
        too large and there is a conspicuous gap before the next icon.
        """
        button = _STATUS_ITEM.button()
        button.setTitle_(title)
        # Icon plus padding, plus roughly a character's width per character.
        _STATUS_ITEM.setLength_(34.0 + 9.0 * len(title))

    @objc.python_method
    def _apply(self, snap):
        # Worst state wins the glyph, so a glance is never falsely reassuring.
        if snap.error:
            title = "⚠︎"
        elif snap.unclear:
            # Unreadable mail ranks above ordinary work: a trigger nobody can
            # parse is the one most likely to be missed entirely.
            title = f"⚠ {len(snap.unclear)}"
        elif snap.needs_outreach:
            title = f"● {len(snap.needs_outreach)}"
        else:
            title = "✓"

        self._set_status_title(title)
        self._render_menu(snap)
        self._notify_new(snap)

        try:
            native_window.refresh_if_open(self.board_path)
        except Exception:
            traceback.print_exc()

    @objc.python_method
    def _notify_new(self, snap):
        """
        Notify only about units not already reported.

        Re-notifying every refresh about the same outstanding unit would train you
        to ignore the notification entirely, which defeats the point. A unit drops
        out of the reported set once it is handled, so if it somehow regresses it
        will notify again.
        """
        if snap.error:
            return

        current = {r.unit for r in snap.needs_outreach}
        new = current - self._notified

        if new:
            units = ", ".join(f"#{u}" for u in sorted(new))
            word = "unit" if len(new) == 1 else "units"
            notify(
                "Move-in triage",
                f"{len(new)} {word} need outreach",
                f"{units} — triggered, with no sign anyone has picked it up yet.",
            )

        self._notified = current

    # -- actions ------------------------------------------------------------

    def openBoard_(self, _sender):
        try:
            native_window.show_board(self.board_path)
        except Exception:
            traceback.print_exc()

    def refreshNow_(self, _sender):
        _STATUS_ITEM.button().setTitle_("…")
        self._wake.set()

    def quitApp_(self, _sender):
        NSApplication.sharedApplication().terminate_(None)


def main() -> None:
    parser = argparse.ArgumentParser(description="Move-in triage menu bar app")
    parser.add_argument("--demo", action="store_true", help="use sample data, no sign-in")
    args = parser.parse_args()

    nsapp = NSApplication.sharedApplication()

    # Accessory: lives in the menu bar with no Dock icon and no app switcher
    # entry. Set at runtime rather than relying on LSUIElement in Info.plist,
    # because the bundle is not always resolved when the interpreter is launched
    # from inside it.
    nsapp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    delegate = TriageDelegate.alloc().initWithDemo_(args.demo)
    nsapp.setDelegate_(delegate)

    delegate.startWorker()

    # Main-thread poll for finished refreshes. One second is responsive without
    # being busy. Retained so it is not collected mid-run.
    delegate.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        1.0, delegate, "tick:", None, True
    )

    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
