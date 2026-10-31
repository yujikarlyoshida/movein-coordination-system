"""
macOS menu bar app: the whole product, no terminal needed.

Watches the Move ins folder through the app's own Outlook window, shows a count
in the menu bar, notifies when a NEW unit needs outreach, and opens the board.

    python menubar.py            live: Outlook on the web, in the app's window
    python menubar.py --demo     sample data, no sign-in
    python menubar.py --board    also open the board window at launch
    python menubar.py --browser  also open the app's browser (RiseIO tab) to sign in
    python menubar.py --practice sample data plus the practice systems: Prepare
                                 a unit and watch it carried out end to end

What appears in the menu is decided in menu_model.py (plain data, tested);
this file only draws it and runs the worker.

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
AppKit demands that all UI work happen on the main thread, and a pass over the
folder takes seconds to minutes. So passes run on a background daemon thread
which writes a Snapshot into a lock-protected slot, and an NSTimer on the main
thread drains that slot and updates the UI. The menu never blocks on Outlook.
The reader's page scripts are themselves handed to the main thread
(outlook_web.run_js), since WebKit must only be driven from there.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import threading
import traceback
from dataclasses import replace
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

import board
import config
import menu_model
import window as native_window

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


# How often the Connections panel looks at each tab, and proves a session by reloading it.
CONNECTION_CHECK_SECONDS = 20
SESSION_CHECK_SECONDS = 5 * 60


def resolve_output_path() -> str:
    """
    Where board.html is written: Application Support, always.

    It used to sit next to the code when running from source. Now that the board
    lists colleagues' names and addresses in its sender panel, it belongs in the
    private app folder, outside the repository, readable by its owner only.
    """
    support = os.path.expanduser("~/Library/Application Support/MoveInTriage")
    os.makedirs(support, exist_ok=True)
    import hero

    hero.install(support)     # the shader bundle the board and welcome page load
    return os.path.join(support, BOARD_FILENAME)


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
        self.webview = None          # the Outlook window's WKWebView (live mode)

        self._lock = threading.Lock()
        self._pending = None
        self._wake = threading.Event()
        self._notified = set()
        self._shown_for_sign_in = False

        # What the board was last drawn from, so a sender change can redraw it
        # at once instead of waiting for the next pass.
        self._board_lock = threading.Lock()
        self._board_args = ([], "Starting", [])
        self._notice = ""

        # Missing-field keys (see board.missing_keys): those the user pressed
        # Got it on, and those already notified about.
        self._acked = set()
        self._missing_seen = set()

        # Practice mode, and what the confirmation sheet works from. Plans hold
        # resident details for the sheet; they live in memory only.
        self.practice = False
        self._units = {}
        self._plans = {}
        self._done = set()
        self._sheet_open = False
        self._running = False

        # Sign-in state of each tab in the app's browser, for the board.
        # Empty (not None) in live mode, so the board's Open browser button
        # shows from the start, before the first sign-in check.
        self._connections = None if self.demo else []

        # The welcome page / Settings, when showing in the board's window:
        # {"step", "edit", "notice", "problems", "detected_email",
        #  "folder_found", "draft"}. None while the board is showing.
        self._setup = None

        # A guided run's "your turn": set by the sheet's Done / Skip buttons.
        self._turn_done = threading.Event()
        self._turn_skip = threading.Event()

        # Explicit width rather than NSVariableStatusItemLength. On macOS 26 the
        # status item is an NSSceneStatusItem, and variable length leaves it zero
        # pixels wide: AppKit cheerfully reports isVisible=True and the correct
        # title while nothing whatsoever is drawn. That combination cost an
        # evening to track down. Give it a real width and it appears.
        _STATUS_ITEM = NSStatusBar.systemStatusBar().statusItemWithLength_(64.0)

        button = _STATUS_ITEM.button()

        # An image is NOT optional here, however much it looks like decoration.
        # On macOS 26 a scene status item showing only text does not render at
        # all -- no icon, no text, nothing, while AppKit still reports it visible.
        # Give it an image and the text appears alongside. Verified empirically on
        # 26.5.2; a title-only item was invisible and the same item with an image
        # was fine. (It is an image, not a glyph in a string: the title text
        # itself stays plain words.)
        #
        # Symbol availability varies by OS version, and a name that does not
        # resolve returns None, so try several and take the first that exists.
        icon = None
        for symbol in ("checklist", "list.bullet.clipboard", "list.bullet", "circle"):
            icon = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                symbol, "Housewarm"
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
        _MENU.setAutoenablesItems_(False)
        _STATUS_ITEM.setMenu_(_MENU)

        self.snapshot = menu_model.Snapshot(demo=self.demo)
        self._set_status_title(menu_model.status_title(self.snapshot))
        self._render_menu(self.snapshot)
        self._write_board([], "Starting", [])

        return self

    # -- menu construction --------------------------------------------------
    #
    # Everything below that is not an Objective-C entry point carries
    # @objc.python_method. pyobjc exposes every method on an NSObject subclass as
    # a selector, and a selector's argument count is derived from the underscores
    # in its name -- so a plain helper taking arguments fails at class-creation
    # time with BadPrototypeError. The decorator says "this is ordinary Python,
    # leave it alone".

    @objc.python_method
    def _render_menu(self, snap):
        _MENU.removeAllItems()
        for row in menu_model.menu_rows(snap):
            if row is None:
                _MENU.addItem_(NSMenuItem.separatorItem())
                continue
            label, action, indent = row
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(label, action, "")
            if action is not None:
                item.setTarget_(self)
            else:
                item.setEnabled_(False)
            if indent:
                item.setIndentationLevel_(indent)
            _MENU.addItem_(item)

    @objc.python_method
    def _set_status_title(self, title):
        """
        Set the menu bar text, resizing the item to fit.

        The width has to be maintained by hand because the item uses a fixed
        length -- see the note in initWithDemo_ about variable length rendering
        as zero pixels on macOS 26. Too small and the text is clipped; too large
        and there is a conspicuous gap before the next icon.
        """
        _STATUS_ITEM.button().setTitle_(title)
        _STATUS_ITEM.setLength_(30.0 + 7.5 * len(title))

    # -- worker -------------------------------------------------------------

    def startWorker(self):
        target = self._demo_loop if self.demo else self._live_loop
        threading.Thread(target=target, daemon=True).start()
        if not self.demo:
            threading.Thread(target=self._watch_connections, daemon=True).start()

    @objc.python_method
    def _watch_connections(self):
        """Keep the Connections panel true between mail checks: each tab looked
        at often, and RiseIO's and Xfinity's sessions proved by a reload now and
        then. Outlook's is left to the mail reader, which is using its tab."""
        import time

        import browser

        verified = time.monotonic()
        signed_out_for, alerted = {}, set()
        while True:
            time.sleep(CONNECTION_CHECK_SECONDS)
            verify = ()
            if not self._running and time.monotonic() - verified >= SESSION_CHECK_SECONDS:
                verify = ("riseio", "xfinity")
                verified = time.monotonic()
            try:
                changed = self._check_connections(self.snapshot.state if self.snapshot else "", verify)
            except Exception:  # noqa: BLE001 -- keep watching
                traceback.print_exc()
                continue
            # Not during first-time setup, when nothing is signed in yet.
            due = [] if self._setup is not None else menu_model.sign_in_alerts(
                self._connections or [], signed_out_for, alerted)
            if due:
                native_window._trace("connections", f"sign-in needed: {', '.join(due)}")
                notify("Housewarm", f"Sign in needed: {' and '.join(due)}",
                       "Open Housewarm and press Open under Connections.")
            if changed:
                native_window._trace("connections", ", ".join(f"{k} {s}" for k, _, s in self._connections))
                self._write_board(*self._board_args)
                if self._setup is not None and self._setup.get("step") in ("systems", "outlook"):
                    AppHelper.callAfter(self._redraw_setup, self._setup["step"])
                elif self._setup is None and not self._sheet_open:
                    AppHelper.callAfter(native_window.refresh_if_open, self.board_path)

    @objc.python_method
    def _publish(self, snap):
        with self._lock:
            self._pending = snap

    @objc.python_method
    def _write_board(self, results, status="", unknown=None):
        import senders

        with self._board_lock:
            self._board_args = (results, status, unknown or [])
            if self._setup is not None:
                # The welcome page is showing: redrawn only by its own buttons,
                # so a background check never wipes what is being typed.
                return
            page = board.render(results, status=status, senders=senders.load(),
                                unknown=unknown or [], notice=self._notice,
                                acknowledged=self._acked, done_units=self._done,
                                connections=self._connections, settings_button=not self.demo,
                                guide=self._guide_state(), logo=self._has_logo())
            fd = os.open(self.board_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(page)
            os.chmod(self.board_path, 0o600)

    @objc.python_method
    def _with_marks(self, results):
        import history
        import rules

        try:
            return rules.with_marks(results, history.marked_done())
        except Exception:  # noqa: BLE001 -- an unreadable log never hides units
            traceback.print_exc()
            return results

    @objc.python_method
    def _mark(self, unit, done):
        """Mark done / Undo from the board: recorded against this unit's lease
        email, then the board, menu and notifications follow at once."""
        import history

        result = self._units.get(unit)
        if result is None or not result.trigger_id:
            native_window._trace("mark_done", "did nothing: that unit is not on the latest check")
            return
        if done:
            history.mark_done(result.trigger_id, unit)
        else:
            history.unmark_done(result.trigger_id)
            self._done.discard(unit)
        results = self._with_marks(list(self._units.values()))
        _, status, unknown = self._board_args
        self._notice = f"Unit {unit} marked done." if done else f"Unit {unit} is back on the list."
        self._write_board(results, status, unknown)
        self._apply(replace(self.snapshot, results=results))
        native_window.refresh_if_open(self.board_path)

    @objc.python_method
    def _remember(self, results, plans):
        """Keep each unit's result and prepared plan for the confirmation sheet."""
        self._units = {r.unit: r for r in results}
        self._plans = {p.unit: p for p in plans}

    @objc.python_method
    def _check_connections(self, outlook_state, verify=()):
        """Each tab's sign-in state, proving the sessions named in `verify`.
        True when something changed. A tab still loading keeps its last answer."""
        import browser

        before = {k: s for k, _, s in (self._connections or [])}
        states = []
        for key in browser.ORDER:
            if key == "outlook" and outlook_state == "signed_out":
                state = "signed_out"
            else:
                try:
                    state = browser.verify_session(key) if key in verify else browser.sign_in_state(key)
                except Exception:  # noqa: BLE001 -- a slow tab is "checking"
                    state = "loading"
                if state == "loading":
                    state = before.get(key, state)
            states.append((key, browser.SITES[key][0], state))
        self._connections = states
        return {k: s for k, _, s in states} != before

    @objc.python_method
    def _demo_loop(self):
        import rules
        import sample_data
        import staging

        label = ("Practice mode: sample mailbox, practice systems" if self.practice
                 else "Demo mailbox, no sign-in")
        while True:
            try:
                with sample_data.fixture_config():
                    results = rules.evaluate(sample_data.MESSAGES)
                    self._remember(results, staging.collect(sample_data.MESSAGES))
                results = self._with_marks(results)
                self._write_board(results, status=label)
                snap = menu_model.Snapshot(results=results, fetched_at=datetime.now(),
                                           state="watching", demo=True)
            except Exception as exc:
                traceback.print_exc()
                snap = replace(self.snapshot, state="error", error=str(exc))
            self._publish(snap)
            if self._wake.wait(timeout=config.REFRESH_INTERVAL_SECONDS):
                self._wake.clear()

    @objc.python_method
    def _live_loop(self):
        """
        One pass over the folder, then wait. Waits are short while something is
        wrong (signed out, still loading) or the window is still being read, so
        a sign-in or a backlog is picked up at once rather than after the
        interval. Results from the last good pass survive every other state.
        """
        import mailsource
        import outlook_web
        import rules
        import staging
        import watcher

        import browser

        # Hands off Outlook while you are using it, or a guided run is.
        reader = outlook_web.OutlookWebReader(
            self.webview, hands_off=lambda: browser.outlook_in_use() or self._running)
        last = menu_model.Snapshot(demo=False, folder=reader.folder)
        while True:
            delay = 20
            # The folder from Settings, which can change while running.
            reader.folder = getattr(config, "MAIL_FOLDER", reader.folder)
            if watcher.is_paused():
                snap = replace(last, state="paused")
                delay = config.REFRESH_INTERVAL_SECONDS
            else:
                try:
                    records = reader.read_pass()
                except outlook_web.SignedOut as exc:
                    native_window._trace("check", f"stopped: {exc}")
                    snap = replace(last, state="signed_out")
                except outlook_web.InUse:
                    native_window._trace("check", "paused: a guided run is using Outlook" if self._running
                                         else "paused: you typed, clicked or scrolled in the Outlook tab in the last few seconds")
                    # Stopped before touching anything; the last results stay
                    # on the board. Look again shortly.
                    snap = replace(last, state="in_use")
                    delay = 5
                    results, _, unknown = self._board_args
                    self._board_args = (results, "Paused while you use Outlook. Picks up a few seconds after you stop.", unknown)
                except (outlook_web.NotReady, TimeoutError) as exc:
                    native_window._trace("check", f"waiting: Outlook not ready ({type(exc).__name__}: {exc})")
                    snap = replace(last, state="loading")
                except Exception as exc:  # noqa: BLE001 -- keep watching; say what broke
                    native_window._trace("check", f"FAILED: {type(exc).__name__}: {exc}")
                    traceback.print_exc()
                    snap = replace(last, state="error", error=f"{type(exc).__name__}: {exc}")
                else:
                    messages = mailsource.owa_messages(records, days=reader.lookback_days)
                    results = rules.evaluate(messages)
                    # Records the history log and prepares plans for the
                    # confirmation sheet. Prepares, never sends.
                    self._remember(results, staging.collect(messages))
                    # Units you marked done stay done (that lease email only).
                    results = self._with_marks(results)
                    unknown = outlook_web.unknown_lease_senders(messages)
                    stamp = datetime.now().strftime("%I:%M %p").lstrip("0")
                    native_window._trace("check", f"finished: {len(results)} unit(s), {reader.last_opened} thread(s) opened")
                    self._notice = self._account_warning()
                    self._write_board(results, f"Watching {reader.folder}, last check {stamp}", unknown)
                    snap = menu_model.Snapshot(
                        results=results, fetched_at=datetime.now(), state="watching",
                        more_to_read=bool(reader.backlog or reader.skipped), folder=reader.folder,
                        unknown_senders=unknown,
                    )
                    last = snap
                    delay = 60 if snap.more_to_read else config.REFRESH_INTERVAL_SECONDS
            self._check_connections(snap.state)
            if snap.state != "watching":
                # Keep the board's Connections panel current while not reading.
                self._write_board(*self._board_args)
            self._publish(snap)
            # "Check now", "Resume" and a fresh sign-in end the wait early.
            if self._wake.wait(timeout=delay):
                self._wake.clear()

    # -- main-thread timer --------------------------------------------------

    def tick_(self, _timer):
        """Runs on the main thread. Applies a finished pass, if one is waiting."""
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
    def _apply(self, snap):
        self.snapshot = snap
        self._set_status_title(menu_model.status_title(snap))
        self._render_menu(snap)
        self._notify_new(snap)

        # Signed out: bring the Outlook window forward once, so signing in again
        # is one step. Reset when a pass succeeds.
        if snap.state == "signed_out" and not self._shown_for_sign_in and self.webview is not None:
            self._shown_for_sign_in = True
            self._show_outlook()
        elif snap.state == "watching":
            self._shown_for_sign_in = False

        # Never reload the board under an open sheet (it would vanish mid-review)
        # or under the welcome page (it redraws itself).
        if not self._sheet_open and self._setup is None:
            try:
                native_window.refresh_if_open(self.board_path)
            except Exception:
                traceback.print_exc()

    @objc.python_method
    def _notify_new(self, snap):
        """
        Notify only about units not already reported.

        Re-notifying every pass about the same outstanding unit would train you
        to ignore the notification entirely, which defeats the point. A unit drops
        out of the reported set once it is handled, so if it somehow regresses it
        will notify again. Only fresh results count: a paused or signed-out
        snapshot carries old numbers.
        """
        if snap.state != "watching":
            return

        current = {r.unit for r in snap.needs_outreach}
        new = current - self._notified

        if new:
            units = ", ".join(f"#{u}" for u in sorted(new))
            word = "unit needs" if len(new) == 1 else "units need"
            notify(
                "Housewarm",
                f"{len(new)} {word} outreach",
                f"{units}: no sign a teammate has picked it up yet.",
            )

        self._notified = current
        self._notify_missing(snap)

    @objc.python_method
    def _notify_missing(self, snap):
        """
        A notification when a unit newly turns out to lack something a step
        needs -- say, no phone, so Xfinity cannot be added. Units, positions and
        field names only; it shows on a lock screen.
        """
        fresh = []
        for r in snap.missing_info:
            keys = board.missing_keys(r)
            if keys - self._missing_seen:
                fresh.append(r)
            self._missing_seen |= keys
        if not fresh:
            return
        import triggers

        first = fresh[0]
        position, fields = first.missing[0]
        blocks = triggers.blocked_steps(fields)
        detail = f"{triggers.ordinal(position)} resident: {triggers.gap_phrase(fields)}"
        if blocks:
            detail += f", so {' and '.join(blocks)} can't be done yet"
        more = f" and {len(fresh) - 1} more unit{'s' if len(fresh) > 2 else ''}" if len(fresh) > 1 else ""
        notify("Housewarm", f"#{first.unit}{more}: details missing", detail + ".")

    @objc.python_method
    def _show_outlook(self):
        import browser

        browser.open_tab("outlook")

    # -- actions ------------------------------------------------------------

    def openBoard_(self, _sender):
        try:
            native_window.show_board(self.board_path, on_action=self._on_board_action)
        except Exception:
            traceback.print_exc()

    @objc.python_method
    def _on_board_action(self, action, value):
        """
        The board's buttons, on the main thread. Run check wakes the worker;
        adding or removing a sender saves the private list, applies it, redraws
        the board with a one-line notice, and checks again.
        """
        import senders

        if action.startswith("setup_") or action == "open_settings" or (
                self._setup is not None and action in ("add_sender", "remove_sender", "open_site")):
            self._setup_action(action, value)
            return
        if action == "run":
            self.checkNow_(None)
            return
        if action in ("guide_hide", "guide_show"):
            import settings

            if not self.demo:
                settings.set_guide_hidden(action == "guide_hide")
            else:
                self._demo_guide_hidden = action == "guide_hide"
            self._write_board(*self._board_args)
            native_window.refresh_if_open(self.board_path)
            return
        if action in ("mark_done", "unmark_done"):
            self._mark(value, action == "mark_done")
            return
        if action == "ack_missing":
            self._acked |= {k for k in value.split(",") if k}
            self._write_board(*self._board_args)
            return
        if action == "prepare":
            self._open_sheet(value)
            return
        if action == "guided_done":
            self._turn_done.set()
            return
        if action == "guided_skip":
            self._turn_skip.set()
            return
        if action == "open_site":
            import browser

            if value in browser.SITES:
                browser.open_tab(value)
                self._wake.set()     # check sign-in states again soon
            else:
                native_window._trace(action, "did nothing: unknown site")
            return
        if action == "confirm":
            self._confirm(value)
            return
        if action == "sheet_closed":
            self._sheet_open = False
            self._write_board(*self._board_args)
            native_window.refresh_if_open(self.board_path)
            return
        if action == "add_sender":
            changed, self._notice = senders.add(value)
        elif action == "remove_sender":
            changed, self._notice = senders.remove(value)
        else:
            return
        if self.demo and changed:
            self._notice = "Demo mode: the sample mailbox keeps its own senders, so this change isn't used."
        self._write_board(*self._board_args)
        if not self._sheet_open:
            native_window.refresh_if_open(self.board_path)
        if changed:
            self._wake.set()

    # -- the welcome page and Settings ---------------------------------------

    @objc.python_method
    def _guide_state(self) -> bool:
        """True to show the board's Getting started guide, False for a Help
        button. Demo runs keep it in memory, never in the real settings."""
        if self.demo:
            return not getattr(self, "_demo_guide_hidden", False)
        import settings

        return not settings.load()["guide_hidden"]

    @objc.python_method
    def _account_warning(self) -> str:
        """A board notice when Outlook is signed in as someone other than the
        email in Settings. Empty when they match or either is unknown."""
        import settings

        mine = settings.load()["you"]["email"]
        seen = self._outlook_account()
        if mine and seen and seen != mine:
            return ("Outlook is signed in as a different account than the one in Settings. "
                    "Check you are reading the right mailbox.")
        return ""

    @objc.python_method
    def _outlook_account(self) -> str:
        """The signed-in address as Outlook shows it, or "" (read-only)."""
        import outlook_web
        import welcome

        if self.webview is None:
            return ""
        try:
            return (outlook_web.run_js(self.webview, welcome.ACCOUNT_JS, timeout=5) or {}).get("email", "")
        except Exception:  # noqa: BLE001 -- not readable is the same as unknown
            return ""

    @objc.python_method
    def _has_logo(self) -> str:
        """The logo's version tag for the pages ("" when there is none)."""
        if self.demo:
            return ""
        import settings

        return str(int(settings.logo_path().stat().st_mtime)) if settings.has_logo() else ""

    @objc.python_method
    def _outlook_sign_in(self, state: str) -> str:
        """Signed in or not, from the Outlook tab's address as the background
        check last saw it (the reader pauses while you have Outlook open, so
        its own state can't say). Never asks the page from here."""
        for key, _label, tab_state in self._connections or []:
            if key == "outlook" and tab_state in ("signed_in", "signed_out"):
                return tab_state
        return {"signed_out": "signed_out", "watching": "signed_in"}.get(state, "")

    @objc.python_method
    def _write_setup(self):
        import senders
        import settings
        import welcome
        import welcome_template

        st = self._setup
        data = settings.load()
        for section, values in (st.get("draft") or {}).items():
            data[section].update({k: v for k, v in values.items() if k in data[section]})
        state = self.snapshot.state if self.snapshot else ""
        ctx = {
            "settings": data, "senders": senders.load(), "edit": st["edit"],
            "notice": st.get("notice", ""), "problems": st.get("problems", []),
            "outlook_state": self._outlook_sign_in(state),
            "detected_email": st.get("detected_email", ""), "folder_found": st.get("folder_found"),
            "connections": self._connections or [], "template_captured": welcome_template.available(),
            "logo": self._has_logo(),
        }
        page = welcome.render(st["step"], ctx)
        with self._board_lock:
            fd = os.open(self.board_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(page)
            os.chmod(self.board_path, 0o600)
        native_window.refresh_if_open(self.board_path)

    @objc.python_method
    def _setup_go(self, step):
        """Show a step, gathering what it displays first (read-only)."""
        import json

        import outlook_web
        import settings
        import welcome

        st = self._setup
        st.update(step=step, problems=[])
        if step == "property":
            st["folder_found"] = None
        # Draw the step at once with what is known; anything that has to ask
        # Outlook is looked up off the main thread and the step redrawn when it
        # answers. Waiting on a web view from inside a button's handler is what
        # froze the windows.
        self._write_setup()

        def look_up():
            if step == "you":
                st["detected_email"] = self._outlook_account()
            elif step == "property" and self.webview is not None:
                folder = settings.load()["property"]["folder"]
                try:
                    st["folder_found"] = bool((outlook_web.run_js(
                        self.webview, welcome.FOLDER_JS % json.dumps(folder), timeout=5) or {}).get("found"))
                except Exception:  # noqa: BLE001 -- unknown, not "missing"
                    pass
            elif step in ("systems", "outlook"):
                self._check_connections(self.snapshot.state if self.snapshot else "")
            else:
                return
            AppHelper.callAfter(self._redraw_setup, step)

        threading.Thread(target=look_up, daemon=True).start()

    @objc.python_method
    def _redraw_setup(self, step):
        """Main thread: redraw if that step is still the one showing."""
        if self._setup is not None and self._setup.get("step") == step:
            self._write_setup()

    @objc.python_method
    def _setup_action(self, action, value):
        """The welcome page's buttons, on the main thread."""
        import json

        import senders
        import settings
        import welcome
        import welcome_template

        if action == "open_settings":
            self._setup = {"step": "you", "edit": True}
            self._setup_go("you")
            return
        if self._setup is None:
            native_window._trace(action, "did nothing: Settings and the welcome page are not open")
            return
        st = self._setup
        st["notice"] = ""

        if action == "setup_go":
            if value in welcome.STEPS:
                self._setup_go(value)
            return
        if action == "open_site":
            import browser

            if value in browser.SITES:
                browser.open_tab(value)
                self._wake.set()
            return
        if action == "setup_keep":
            # Typed on the property step but not saved yet, kept across the
            # reload an Add sender causes. Memory only.
            try:
                st["draft"] = {"property": {k: str(v) for k, v in json.loads(value).items()}}
            except (ValueError, AttributeError):
                pass
            return
        if action in ("add_sender", "remove_sender"):
            changed, st["notice"] = (senders.add if action == "add_sender" else senders.remove)(value)
            if changed:
                self._wake.set()
            self._write_setup()
            return
        if action in ("setup_save_you", "setup_save_property"):
            try:
                values = {k: str(v) for k, v in json.loads(value).items()}
            except (ValueError, AttributeError):
                return
            section = "you" if action == "setup_save_you" else "property"
            _, st["problems"] = settings.update(section, values)
            st.pop("draft", None)
            if st["problems"]:
                self._write_setup()
                return
            if section == "property":
                self._wake.set()      # the folder may have changed
            if st["edit"]:
                self._setup_go(st["step"])
                st["notice"] = "Saved."
                self._write_setup()
            else:
                self._setup_go("property" if section == "you" else "systems")
            return
        if action == "setup_capture":
            import outlook_web

            def capture():
                try:
                    got = outlook_web.run_js(self.webview, welcome.CAPTURE_JS, timeout=10) or {}
                except Exception:  # noqa: BLE001
                    got = {}
                body = got.get("html", "") if got.get("ok") else ""
                if not body:
                    st["notice"] = "No message is open in Outlook. Start a new message and insert your template first."
                elif welcome_template.PLACEHOLDER not in body:
                    st["notice"] = ("That message has no (Resident Name) in it. Insert your Welcome template "
                                    "without changing the placeholder, then capture again.")
                else:
                    welcome_template.save(body)
                    st["notice"] = "Template captured. You can discard that message in Outlook now."
                AppHelper.callAfter(self._redraw_setup, "template")

            threading.Thread(target=capture, daemon=True).start()
            return
        if action == "setup_logo":
            source = _open_panel(["png"])
            if source:
                try:
                    with open(source, "rb") as fh:
                        data = fh.read(2_000_001)
                except OSError:
                    data = b""
                _, st["notice"] = settings.save_logo(data)
            self._write_setup()
            return
        if action == "setup_logo_remove":
            settings.remove_logo()
            st["notice"] = "Logo removed."
            self._write_setup()
            return
        if action == "setup_export":
            include = value == "1"
            if not include:
                try:
                    include = bool(json.loads(value).get("template"))
                except (ValueError, AttributeError):
                    pass
            target = _save_panel("housewarm-profile.json")
            if target:
                settings.write_profile(target, include_template=include)
                st["notice"] = "Profile saved. Send that file to teammates; it holds no personal details."
            self._write_setup()
            return
        if action == "setup_import":
            source = _open_panel()
            if not source:
                return
            try:
                with open(source, encoding="utf-8") as fh:
                    profile = json.load(fh)
            except (OSError, ValueError):
                profile = None
            summary = profile_summary(profile)
            if not summary:
                st["notice"] = "That file isn't a Housewarm profile."
            elif _confirm_alert("Import this profile?", summary, "Import"):
                ok, st["notice"] = settings.import_profile(profile)
                if ok:
                    self._wake.set()
                    if not st["edit"]:
                        notice = st["notice"]
                        self._setup_go("outlook")
                        st["notice"] = notice
            self._write_setup()
            return
        if action == "setup_demo":
            import sys

            subprocess.Popen([sys.executable, os.path.abspath(__file__), "--demo", "--board"])
            return
        if action in ("setup_finish", "setup_close"):
            if action == "setup_finish":
                settings.mark_complete()
            self._setup = None
            self._write_board(*self._board_args)
            native_window.refresh_if_open(self.board_path)
            return

    # -- the confirmation sheet ---------------------------------------------

    @objc.python_method
    def _sheet_js(self, call, *args):
        import json

        native_window.run_board_js(f"window.__sheet.{call}({', '.join(json.dumps(a) for a in args)})")

    @objc.python_method
    def _open_sheet(self, unit):
        import confirm_sheet

        result, plan = self._units.get(unit), self._plans.get(unit)
        if result is None or plan is None:
            native_window._trace("prepare", "did nothing: that unit has no prepared plan (no check has finished, or it is no longer listed)")
            return
        portal = next((a.payload for a in plan.actions if a.kind == "internet_portal"), {})
        residents = [{"first": r["first_name"], "last": r["last_name"], "email": r["email"],
                      "phone": r["phone"], "move_in": r["move_in"]} for r in portal.get("residents", [])]
        when = self.snapshot.fetched_at
        checked = when.strftime("%I:%M %p").lstrip("0") if when else "the last check"
        html = confirm_sheet.build(unit, residents, result.missing, checked, self.practice)
        self._sheet_open = True
        self._sheet_js("open", html)

    @objc.python_method
    def _confirm(self, value):
        """The one click. Check the sheet's values again, then carry out every
        ticked step on the practice systems, reporting each step live."""
        import types

        import autofill
        import confirm_sheet
        import staging

        if self._running:
            return
        clean, problem = confirm_sheet.validate(confirm_sheet.form_from_json(value))
        if clean is None:
            self._sheet_js("error", problem)
            return

        unit = clean["unit"]
        people = [types.SimpleNamespace(first_name=r["first"], email=r["email"]) for r in clean["residents"]]
        subject, body, recipients, _ = staging.compose_welcome(unit, people)
        import welcome_template

        job = autofill.Job(unit=unit, residents=clean["residents"], steps=clean["steps"],
                           welcome={"to": recipients, "cc": getattr(config, "WELCOME_CC", ""),
                                    "subject": subject, "body": body,
                                    # Your captured template, filled from the names as
                                    # confirmed on the sheet.
                                    "html": staging.welcome_html(people),
                                    "names": welcome_template.greeting_names([p.first_name for p in people])},
                           send_activation_email=clean["send_activation_email"],
                           # Only for guided runs, and only what was ticked for this unit.
                           auto_final=[] if self.practice else clean["auto_final"])
        rows = {s: [autofill.STEP_LABELS[s], "waiting", ""] for s in job.steps}

        def show(finished=False):
            import confirm_sheet as cs

            html = cs.progress_html([tuple(rows[s]) for s in job.steps])
            AppHelper.callAfter(lambda: self._sheet_js("progress", html, finished))

        def progress(step, state, detail):
            rows[step][1:] = [state, detail]
            show()
            if not self.practice:
                import browser

                # The turn's instruction and Done / Skip go where the person is
                # working: the browser window's toolbar (the sheet is behind it).
                if state == "your_turn":
                    AppHelper.callAfter(browser.show_turn, detail,
                                        self._turn_done.set, self._turn_skip.set)
                elif state == "auto":
                    # The countdown before the app presses: Skip step holds it.
                    AppHelper.callAfter(browser.show_turn, detail,
                                        self._turn_done.set, self._turn_skip.set, "")
                else:
                    AppHelper.callAfter(browser.hide_turn)

        self._running = True
        self._turn_done.clear()
        self._turn_skip.clear()
        show()

        if self.practice:
            webview = native_window.practice_webview()

            def carry_out():
                # At a watchable pace: practice mode is for showing people.
                runner = autofill.Runner(webview, pace=autofill.DEMO_PACE)
                return [(r.step, r.ok, r.detail) for r in runner.run(job, progress=progress)]
        else:
            import browser
            import guided

            views = {site: browser.view(site) for site in browser.ORDER}

            def take(event):
                if event.is_set():
                    event.clear()
                    return True
                return False

            hooks = guided.Hooks(
                views=views,
                show_tab=lambda site: AppHelper.callAfter(browser.open_tab, site),
                progress=progress,
                skipped=lambda: take(self._turn_skip),
                user_done=lambda: take(self._turn_done),
            )

            def carry_out():
                return guided.Guided(hooks).run(job, job.steps)

        def work():
            try:
                results = carry_out()
                if all(ok for _, ok, _ in results):
                    self._done.add(unit)
                    # A finished live run stays done after a restart too.
                    lease = self._units.get(unit)
                    if not self.practice and lease is not None and lease.trigger_id:
                        import history

                        history.mark_done(lease.trigger_id, unit)
                    self._notice = (f"Unit {unit} done in practice: every step finished." if self.practice
                                    else f"Unit {unit} done: every step finished, with you pressing the final buttons."
                                    if not job.auto_final
                                    else f"Unit {unit} done: every step finished. The app's own presses are in actions.log.")
                else:
                    failed = [autofill.STEP_LABELS[step] for step, ok, _ in results if not ok]
                    self._notice = f"Unit {unit}: {', '.join(failed)} stopped. See the sheet for why."
            finally:
                self._running = False
                if not self.practice:
                    import browser

                    AppHelper.callAfter(browser.hide_turn)
                show(finished=True)

        threading.Thread(target=work, daemon=True).start()

    def checkNow_(self, _sender):
        self._set_status_title("Move-ins…")
        self._wake.set()

    def togglePause_(self, _sender):
        import watcher

        if watcher.is_paused():
            watcher.resume()
        else:
            watcher.pause()
        # Show the change at once, then let the worker confirm it.
        state = "starting" if self.snapshot.state == "paused" else "paused"
        self._apply(replace(self.snapshot, state=state))
        self._wake.set()

    def openOutlook_(self, _sender):
        if self.webview is not None:
            self._show_outlook()
            # A sign-in just happened, or is about to: check soon after.
            self._wake.set()

    def quitApp_(self, _sender):
        NSApplication.sharedApplication().terminate_(None)


def _save_panel(name: str) -> str:
    """macOS's own Save dialog; the chosen path, or "" if cancelled."""
    from AppKit import NSModalResponseOK, NSSavePanel

    panel = NSSavePanel.savePanel()
    panel.setNameFieldStringValue_(name)
    panel.setAllowedFileTypes_(["json"])
    return str(panel.URL().path()) if panel.runModal() == NSModalResponseOK else ""


def _open_panel(types=("json",)) -> str:
    """macOS's own Open dialog; the chosen path, or "" if cancelled."""
    from AppKit import NSModalResponseOK, NSOpenPanel

    panel = NSOpenPanel.openPanel()
    panel.setAllowedFileTypes_(list(types))
    panel.setAllowsMultipleSelection_(False)
    return str(panel.URLs()[0].path()) if panel.runModal() == NSModalResponseOK else ""


def profile_summary(profile) -> str:
    """What a profile holds, in plain words, before anything is applied."""
    import settings

    if not isinstance(profile, dict) or profile.get("kind") not in {settings.PROFILE_KIND, *settings._OLD_PROFILE_KINDS}:
        return ""
    prop = profile.get("property") if isinstance(profile.get("property"), dict) else {}
    roster = profile.get("senders") if isinstance(profile.get("senders"), dict) else {}
    n = sum(len(v) for v in (roster.get("addresses"), roster.get("names")) if isinstance(v, list))
    return (f"Property: {str(prop.get('name') or 'unnamed')[:80]}. "
            f"{n} lease sender{'s' if n != 1 else ''}. "
            f"Welcome template {'included' if profile.get('welcome_template_html') else 'not included'}. "
            "This replaces the property settings on this Mac. Your name and email stay as they are.")


def _confirm_alert(title: str, text: str, ok: str) -> bool:
    from AppKit import NSAlert, NSAlertFirstButtonReturn

    alert = NSAlert.alloc().init()
    alert.setMessageText_(title)
    alert.setInformativeText_(text)
    alert.addButtonWithTitle_(ok)
    alert.addButtonWithTitle_("Cancel")
    return alert.runModal() == NSAlertFirstButtonReturn


def main() -> None:
    parser = argparse.ArgumentParser(description="Housewarm menu bar app")
    parser.add_argument("--demo", action="store_true", help="use sample data, no sign-in")
    parser.add_argument("--board", action="store_true", help="also open the board window at launch")
    parser.add_argument("--browser", metavar="TAB", nargs="?", const="riseio",
                        help="also open the app's browser at launch, on TAB (outlook, riseio, xfinity)")
    parser.add_argument("--probe", metavar="DIR",
                        help="development: evaluate structure-only JS files dropped into DIR/<site>/")
    parser.add_argument("--practice", action="store_true",
                        help="sample data and practice systems: a full click-and-done test run")
    parser.add_argument("--welcome", action="store_true",
                        help="open the welcome page even when already set up (to walk through it)")
    args = parser.parse_args()
    if args.practice:
        args.demo = True
        args.board = True

    import senders
    import settings

    if args.demo:
        import history
        import sample_data

        history.use_throwaway_log()
        senders.use_throwaway()
        senders.save({"addresses": list(sample_data.FIXTURE_SENDERS), "names": []})
        settings.use_throwaway()
    else:
        # Your private list and settings (seeded from config_local.py the
        # first time).
        senders.apply(senders.load())
        settings.apply(settings.load())

    nsapp = NSApplication.sharedApplication()

    # Accessory: lives in the menu bar with no Dock icon and no app switcher
    # entry. Set at runtime rather than relying on LSUIElement in Info.plist,
    # because the bundle is not always resolved when the interpreter is launched
    # from inside it.
    nsapp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    delegate = TriageDelegate.alloc().initWithDemo_(args.demo)
    delegate.practice = bool(args.practice)
    nsapp.setDelegate_(delegate)

    if not args.demo:
        import outlook_web

        # Created hidden; Outlook loads in the background. It comes forward by
        # itself only when a sign-in is needed.
        delegate.webview = outlook_web.open_window(show=False)

        if args.probe:
            import browser

            # The reader's own hidden Outlook window too, as "reader".
            for name, view in [(n, browser.view(n)) for n in browser.ORDER] + [("reader", delegate.webview)]:
                probe = outlook_web._Probe(view, os.path.join(args.probe, name))
                outlook_web._delegates.append(probe)
                NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.5, True, lambda _t, p=probe: p.tick())

    delegate.startWorker()

    if args.probe:
        import outlook_web

        # The board window, and in practice mode the practice systems window
        # (development only), whichever page each shows.
        for name, window_of in (("board", lambda: native_window._window),
                                ("practice", lambda: native_window._practice_window)):
            win_probe = outlook_web._Probe(None, os.path.join(args.probe, name))
            outlook_web._delegates.append(win_probe)

            def win_tick(_t, p=win_probe, window_of=window_of):
                if window_of() is not None:
                    p.webview = window_of().contentView()
                    p.tick()

            NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.5, True, win_tick)

    # First launch: the welcome page, in the board's window.
    if not args.demo and (args.welcome or settings.needs_setup()):
        delegate._setup = {"step": "start", "edit": False}
        delegate._write_setup()
        args.board = True

    if args.board:
        delegate.openBoard_(None)

    if args.browser and not args.demo:
        import browser

        browser.open_tab(args.browser if args.browser in browser.SITES else "riseio")

    # Main-thread poll for finished passes. One second is responsive without
    # being busy. Retained so it is not collected mid-run.
    delegate.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        1.0, delegate, "tick:", None, True
    )

    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
