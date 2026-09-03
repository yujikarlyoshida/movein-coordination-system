"""
The background watcher.

Runs from login and keeps running: poll the mailbox, stage anything new, notify,
wait. It never sends, saves or submits -- staging stops at the confirmation, so
a watcher left running for a month changes nothing on its own.

PAUSING IS A FILE, NOT A VARIABLE. The pause state lives on disk, so it survives
a restart, a crash and a reboot. A pause that quietly expired overnight would be
the worst possible behaviour: you would believe the tool was quiet while it had
resumed polling without telling you.

WHY POLLING RATHER THAN A WEBHOOK. Graph can push change notifications, which
would be faster and cheaper. It also requires a public HTTPS endpoint for Graph
to call, which a laptop behind a home router does not have without tunnelling
traffic in from the internet. Polling every fifteen minutes needs no inbound
connection at all. Given the audit -- where colleagues closed move-ins in as
little as 23 minutes -- arriving a few minutes late costs nothing, because the
tool's job is to notice what is still open, not to race anyone to it.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import config

APP_SUPPORT = Path.home() / "Library" / "Application Support" / "MoveInTriage"
PAUSE_FILE = APP_SUPPORT / getattr(config, "PAUSE_STATE_FILE", "paused")


# ---------------------------------------------------------------------------
# Pause state
# ---------------------------------------------------------------------------

def is_paused() -> bool:
    return PAUSE_FILE.exists()


def pause() -> None:
    APP_SUPPORT.mkdir(parents=True, exist_ok=True)
    PAUSE_FILE.write_text(f"paused at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")


def resume() -> None:
    PAUSE_FILE.unlink(missing_ok=True)


def toggle() -> bool:
    """Flip the state. Returns True if now paused."""
    if is_paused():
        resume()
        return False
    pause()
    return True


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------

def notify(title: str, message: str) -> None:
    """
    A macOS notification. Unit numbers and counts only -- never a resident name,
    because notifications persist in Notification Center and appear on a lock
    screen, which is a display surface with no access control at all.
    """
    if sys.platform != "darwin":
        return

    script = (
        f'display notification {_applescript_quote(message)} '
        f'with title {_applescript_quote(title)}'
    )
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


def _applescript_quote(text: str) -> str:
    """AppleScript strings are double-quoted; embedded quotes need escaping."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

class Watcher:
    """
    Polls on a background thread.

    Holds the most recent staged plans so the menu bar and the window can render
    without re-running the pipeline. Plans are inert; holding them commits
    nothing.
    """

    def __init__(self, interval: int | None = None) -> None:
        self.interval = interval or getattr(config, "REFRESH_INTERVAL_SECONDS", 900)
        self.plans: list = []
        self.last_run: float | None = None
        self.last_error: str | None = None

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        # Units already notified about, so a unit that stays open for three days
        # produces one notification rather than 288.
        self._announced: set[str] = set()

    # -- one pass -----------------------------------------------------------

    def run_once(self) -> list:
        """
        Poll, stage, notify. Returns the staged plans.

        Errors are captured rather than raised: a background thread that dies on
        a transient network failure leaves a menu bar icon that looks fine and
        has silently stopped working.
        """
        import mailsource
        import staging

        try:
            source = mailsource.get_source()
            messages = source.list_recent_messages(
                days=getattr(config, "LOOKBACK_DAYS", 30)
            )
            self.plans = staging.collect(messages)
            self.last_error = None
        except Exception as exc:                       # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            return self.plans
        finally:
            self.last_run = time.time()

        self._announce_new()
        return self.plans

    def _announce_new(self) -> None:
        current = {plan.unit for plan in self.plans}

        fresh = current - self._announced
        if fresh:
            units = ", ".join(f"#{u}" for u in sorted(fresh))
            plural = "move-in needs" if len(fresh) == 1 else "move-ins need"
            notify("Move-in triage", f"{len(fresh)} {plural} you: {units}")

        # Forget units that have closed, so if one reopens it announces again.
        self._announced = current

    # -- the loop ------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            if not is_paused():
                self.run_once()

            # Wake early if asked to stop, rather than sleeping out the full
            # fifteen minutes and delaying shutdown.
            self._stop.wait(self.interval)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        # Daemon thread: if the UI exits, this goes with it rather than keeping
        # a headless process alive with no way to reach it.
        self._thread = threading.Thread(target=self._loop, daemon=True, name="watcher")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    # -- status --------------------------------------------------------------

    def status_line(self) -> str:
        """One line for the menu bar title. Counts and units only."""
        if is_paused():
            return "paused"
        if self.last_error:
            return "error"
        if not self.plans:
            return "clear"

        blocked = sum(1 for p in self.plans if p.blocked_on)
        if blocked:
            return f"{len(self.plans)} open, {blocked} blocked"
        return f"{len(self.plans)} open"


def main() -> int:
    """Run headless. Used by the LaunchAgent when there is no UI."""
    import store

    store.migrate()

    watcher = Watcher()
    print(f"watching every {watcher.interval}s; paused={is_paused()}", flush=True)

    try:
        while True:
            if not is_paused():
                watcher.run_once()
                print(f"[{time.strftime('%H:%M:%S')}] {watcher.status_line()}", flush=True)
            time.sleep(watcher.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
