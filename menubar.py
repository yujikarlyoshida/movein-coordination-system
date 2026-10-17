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


def resolve_output_path() -> str:
    """
    Where board.html is written: Application Support, always.

    It used to sit next to the code when running from source. Now that the board
    lists colleagues' names and addresses in its sender panel, it belongs in the
    private app folder, outside the repository, readable by its owner only.
    """
    support = os.path.expanduser("~/Library/Application Support/MoveInTriage")
    os.makedirs(support, exist_ok=True)
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
        self._connections = None

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
                symbol, "Move-in triage"
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

    @objc.python_method
    def _publish(self, snap):
        with self._lock:
            self._pending = snap

    @objc.python_method
    def _write_board(self, results, status="", unknown=None):
        import senders

        with self._board_lock:
            self._board_args = (results, status, unknown or [])
            page = board.render(results, status=status, senders=senders.load(),
                                unknown=unknown or [], notice=self._notice,
                                acknowledged=self._acked, done_units=self._done,
                                connections=self._connections)
            fd = os.open(self.board_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(page)
            os.chmod(self.board_path, 0o600)

    @objc.python_method
    def _remember(self, results, plans):
        """Keep each unit's result and prepared plan for the confirmation sheet."""
        self._units = {r.unit: r for r in results}
        self._plans = {p.unit: p for p in plans}

    @objc.python_method
    def _check_connections(self, outlook_state):
        """Each tab's sign-in state, from the page address only."""
        import browser

        states = []
        for key in browser.ORDER:
            if key == "outlook" and outlook_state == "signed_out":
                state = "signed_out"
            else:
                try:
                    state = browser.sign_in_state(key)
                except Exception:  # noqa: BLE001 -- a slow tab is "checking"
                    state = "loading"
            states.append((key, browser.SITES[key][0], state))
        self._connections = states

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

        reader = outlook_web.OutlookWebReader(self.webview)
        last = menu_model.Snapshot(demo=False, folder=reader.folder)
        while True:
            delay = 20
            if watcher.is_paused():
                snap = replace(last, state="paused")
                delay = config.REFRESH_INTERVAL_SECONDS
            else:
                try:
                    records = reader.read_pass()
                except outlook_web.SignedOut:
                    snap = replace(last, state="signed_out")
                except (outlook_web.NotReady, TimeoutError):
                    snap = replace(last, state="loading")
                except Exception as exc:  # noqa: BLE001 -- keep watching; say what broke
                    traceback.print_exc()
                    snap = replace(last, state="error", error=f"{type(exc).__name__}: {exc}")
                else:
                    messages = mailsource.owa_messages(records, days=reader.lookback_days)
                    results = rules.evaluate(messages)
                    # Records the history log and prepares plans for the
                    # confirmation sheet. Prepares, never sends.
                    self._remember(results, staging.collect(messages))
                    unknown = outlook_web.unknown_lease_senders(messages)
                    stamp = datetime.now().strftime("%I:%M %p").lstrip("0")
                    self._notice = ""
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

        # Never reload the board under an open sheet: it would vanish mid-review.
        if not self._sheet_open:
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
                "Move-in triage",
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
        notify("Move-in triage", f"#{first.unit}{more}: details missing", detail + ".")

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

        if action == "run":
            self.checkNow_(None)
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
        job = autofill.Job(unit=unit, residents=clean["residents"], steps=clean["steps"],
                           welcome={"to": recipients, "cc": getattr(config, "WELCOME_CC", ""),
                                    "subject": subject, "body": body},
                           send_activation_email=clean["send_activation_email"])
        rows = {s: [autofill.STEP_LABELS[s], "waiting", ""] for s in job.steps}

        def show(finished=False):
            import confirm_sheet as cs

            html = cs.progress_html([tuple(rows[s]) for s in job.steps])
            AppHelper.callAfter(lambda: self._sheet_js("progress", html, finished))

        def progress(step, state, detail):
            rows[step][1:] = [state, detail]
            show()

        self._running = True
        self._turn_done.clear()
        self._turn_skip.clear()
        show()

        if self.practice:
            webview = native_window.practice_webview()

            def carry_out():
                return [(r.step, r.ok, r.detail) for r in autofill.Runner(webview).run(job, progress=progress)]
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
                    self._notice = (f"Unit {unit} done in practice: every step finished." if self.practice
                                    else f"Unit {unit} done: every step finished, with you pressing the final buttons.")
                else:
                    failed = [autofill.STEP_LABELS[step] for step, ok, _ in results if not ok]
                    self._notice = f"Unit {unit}: {', '.join(failed)} stopped. See the sheet for why."
            finally:
                self._running = False
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Move-in triage menu bar app")
    parser.add_argument("--demo", action="store_true", help="use sample data, no sign-in")
    parser.add_argument("--board", action="store_true", help="also open the board window at launch")
    parser.add_argument("--browser", metavar="TAB", nargs="?", const="riseio",
                        help="also open the app's browser at launch, on TAB (outlook, riseio, xfinity)")
    parser.add_argument("--probe", metavar="DIR",
                        help="development: evaluate structure-only JS files dropped into DIR/<site>/")
    parser.add_argument("--practice", action="store_true",
                        help="sample data and practice systems: a full click-and-done test run")
    args = parser.parse_args()
    if args.practice:
        args.demo = True
        args.board = True

    import senders

    if args.demo:
        import history
        import sample_data

        history.use_throwaway_log()
        senders.use_throwaway()
        senders.save({"addresses": list(sample_data.FIXTURE_SENDERS), "names": []})
    else:
        # Your private list (seeded from config_local.py the first time).
        senders.apply(senders.load())

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

            for name in browser.ORDER:
                probe = outlook_web._Probe(browser.view(name), os.path.join(args.probe, name))
                outlook_web._delegates.append(probe)
                NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.5, True, lambda _t, p=probe: p.tick())

    delegate.startWorker()

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
