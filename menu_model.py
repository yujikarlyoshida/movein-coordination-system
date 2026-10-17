"""
What the menu bar shows, as plain data.

No AppKit here: menubar.py turns these rows into NSMenuItems, and the tests read
them directly. Keeping the decisions in one pure place is what makes the menu
testable at all, and keeps the wording rules (sentence case, units only, no
resident names, no symbols) checkable.

States, from the worker's point of view:

    starting     no pass has finished yet
    watching     the last pass read the folder
    paused       the user paused watching (a persisted file; see watcher.py)
    signed_out   Outlook's session ended; the sign-in window is needed
    loading      Outlook is still loading, or the folder is not there yet
    error        the last pass failed for another reason

Results from the last good pass are kept through every non-watching state, so
the board never goes blank -- and the state line says the numbers may be stale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from models import Result, Verdict

# A menu row is (label, action, indent). None is a separator. An action of None
# is an informational, non-clickable row. Actions name delegate selectors.
Row = "tuple[str, str | None, int] | None"

OPEN_BOARD = "openBoard:"
CHECK_NOW = "checkNow:"
TOGGLE_PAUSE = "togglePause:"
OPEN_OUTLOOK = "openOutlook:"
QUIT = "quitApp:"

# How many units to list under a heading before summarising the rest.
MAX_LISTED = 8


@dataclass
class Snapshot:
    """One state of the app, handed from the worker thread to the UI thread."""

    results: list[Result] = field(default_factory=list)
    fetched_at: datetime | None = None
    state: str = "starting"
    error: str = ""
    more_to_read: bool = False
    demo: bool = False
    folder: str = "Move ins"
    # [(sender, threads)] seen sending lease emails but not on the list. Shown
    # on the board with an Add button; never in the menu.
    unknown_senders: list = field(default_factory=list)

    def _by(self, verdict: Verdict) -> list[Result]:
        return [r for r in self.results if r.verdict is verdict]

    @property
    def needs_outreach(self) -> list[Result]:
        return self._by(Verdict.NEEDS_OUTREACH)

    @property
    def unclear(self) -> list[Result]:
        return self._by(Verdict.UNCLEAR)

    @property
    def handled(self) -> list[Result]:
        return self._by(Verdict.HANDLED)

    @property
    def skipped(self) -> list[Result]:
        return self._by(Verdict.SKIPPED_CORPORATE)

    @property
    def missing_info(self) -> list[Result]:
        """Units needing outreach that lack details some step requires."""
        return [r for r in self.needs_outreach if r.missing]


def status_title(snap: Snapshot) -> str:
    """
    The menu bar text. Plain words and a number, no symbols.

    The state that needs the user wins over counts, so a glance is never falsely
    reassuring: a signed-out app showing an old "Move-ins 0" would be lying.
    """
    if snap.state == "signed_out":
        return "Sign in"
    if snap.state == "paused":
        return "Paused"
    if snap.state == "error":
        return "Move-ins: check"
    if snap.state == "starting" or snap.fetched_at is None:
        return "Move-ins…"
    # Units that need a person: outreach, plus mail too unclear to judge.
    attention = len(snap.needs_outreach) + len(snap.unclear)
    return f"Move-ins {attention}" if attention else "Move-ins"


def _state_line(snap: Snapshot) -> str:
    return {
        "starting": "Starting…",
        "watching": f"Watching {snap.folder}" + (" (demo)" if snap.demo else " · signed in"),
        "paused": "Paused. Nothing is being checked",
        "signed_out": "Signed out of Outlook",
        "loading": "Waiting for Outlook to load",
        "error": "Couldn't check the folder",
    }.get(snap.state, snap.state)


def _when(snap: Snapshot) -> str:
    if snap.fetched_at is None:
        return ""
    stamp = snap.fetched_at.strftime("%I:%M %p").lstrip("0")
    line = f"Last check {stamp}"
    if snap.more_to_read:
        line += " · still reading older threads"
    elif snap.state != "watching":
        line += " · counts may be out of date"
    return line


def _units(rows: list, results: list[Result]) -> None:
    for r in results[:MAX_LISTED]:
        rows.append((f"#{r.unit}", OPEN_BOARD, 1))
    if len(results) > MAX_LISTED:
        rows.append((f"and {len(results) - MAX_LISTED} more", OPEN_BOARD, 1))


def menu_rows(snap: Snapshot) -> list:
    """Every row of the dropdown, top to bottom."""
    rows: list = [(_state_line(snap), None, 0)]
    when = _when(snap)
    if when:
        rows.append((when, None, 0))
    if snap.state == "error" and snap.error:
        rows.append((snap.error[:70], None, 0))
    rows.append(None)

    if snap.state == "signed_out":
        rows.append(("Sign in to Outlook", OPEN_OUTLOOK, 0))
        rows.append(None)

    if snap.fetched_at is not None:
        needs, unclear = snap.needs_outreach, snap.unclear
        if needs:
            rows.append((f"{len(needs)} need outreach", OPEN_BOARD, 0))
            _units(rows, needs)
            if snap.missing_info:
                rows.append((f"{len(snap.missing_info)} missing info", OPEN_BOARD, 0))
        else:
            rows.append(("Nothing needs outreach", None, 0))
        if unclear:
            rows.append((f"{len(unclear)} can't be read", OPEN_BOARD, 0))
            _units(rows, unclear)
        rows.append((f"{len(snap.handled)} handled by a teammate", None, 0))
        if snap.skipped:
            rows.append((f"{len(snap.skipped)} skipped, corporate", OPEN_BOARD, 0))
        rows.append(None)

    rows.append(("Open board", OPEN_BOARD, 0))
    if not snap.demo:
        rows.append(("Check now", CHECK_NOW, 0))
        rows.append(("Resume watching" if snap.state == "paused" else "Pause watching", TOGGLE_PAUSE, 0))
        rows.append(("Outlook window", OPEN_OUTLOOK, 0))
    else:
        rows.append(("Check now", CHECK_NOW, 0))
    rows.append(None)
    rows.append(("Quit", QUIT, 0))
    return rows
