"""
A local log of which units triggered and what the tool concluded. Nothing else.

Every poll re-derives the board from the mailbox, which is the source of truth;
this log only remembers what earlier polls saw, so `--history` can show when a
unit went from needing outreach to handled.

WHAT IT HOLDS: trigger message id, unit, status, and three timestamps. No
names, emails, phone numbers, subjects or senders. Subjects are left out on
purpose: a trigger subject can name a company or a resident, and a colleague's
address is not this log's business either. With nothing personal in it, the log
needs no encryption, no key and no Keychain.

WHERE IT LIVES: ~/Library/Application Support/MoveInTriage/history.sqlite3,
mode 0600, outside the repository. MOVEIN_HISTORY_PATH overrides it for tests,
and demo runs use ":memory:" so they never touch the real file.

Standard library only (sqlite3).
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

APP_SUPPORT = Path.home() / "Library" / "Application Support" / "MoveInTriage"
LOG_PATH = APP_SUPPORT / "history.sqlite3"

# The encrypted resident database earlier versions kept. Nothing reads it now;
# `app.py --db` mentions it so it can be removed by hand.
LEGACY_DB_PATH = APP_SUPPORT / "residents.sqlite3"

_ENV_VAR = "MOVEIN_HISTORY_PATH"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS triggers (
    message_id        TEXT PRIMARY KEY,
    unit              TEXT NOT NULL,
    status            TEXT NOT NULL,
    trigger_received  TEXT NOT NULL DEFAULT '',
    first_seen        TEXT NOT NULL,
    status_changed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS triggers_status_unit ON triggers (status, unit);
"""

# An in-memory database lives only as long as its connection, so demo runs keep
# one open for the life of the process.
_memory: sqlite3.Connection | None = None


@dataclass(frozen=True)
class Entry:
    """One trigger as the log remembers it. No field can hold personal data."""

    message_id: str
    unit: str
    status: str
    trigger_received: str
    first_seen: str
    status_changed_at: str


def path() -> str:
    return os.environ.get(_ENV_VAR, str(LOG_PATH))


def use_throwaway_log() -> None:
    """
    Demo runs only: keep the log in memory for this process.

    The sample mailbox goes through the same staging path as real mail, which
    records triggers; without this a demo run would write sample units into the
    real log. An explicit non-default path (a test's scratch file) is kept.
    """
    if path() == str(LOG_PATH):
        os.environ[_ENV_VAR] = ":memory:"


@contextmanager
def _db():
    global _memory

    target = path()
    if target == ":memory:":
        if _memory is None:
            _memory = sqlite3.connect(":memory:")
            _memory.executescript(_SCHEMA)
        yield _memory
        _memory.commit()
        return

    Path(target).parent.mkdir(parents=True, exist_ok=True)
    # Create the file 0600 before sqlite opens it, so it is never readable by
    # anyone else, even briefly. Unit numbers and timestamps are not secret, but
    # they are nobody else's business.
    os.close(os.open(target, os.O_CREAT | os.O_RDWR, 0o600))
    os.chmod(target, 0o600)

    # Wait rather than fail if the menu bar poll and a CLI run collide.
    conn = sqlite3.connect(target, timeout=20)
    try:
        conn.executescript(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record(*, message_id: str, unit: str, status: str, trigger_received: str = "") -> None:
    """
    Remember a trigger, or update its status if it has been seen before.

    Keyed on the trigger's message id, so the watcher can call this every
    fifteen minutes forever without duplicating anything. `status_changed_at`
    moves only when the status actually changes -- that transition, a unit going
    from needing outreach to handled, is what the history is for.
    """
    if not message_id:
        # Nothing to key on, so it cannot be recorded idempotently. The board
        # still shows it; it just has no history.
        return

    now = _now()
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO triggers
                (message_id, unit, status, trigger_received, first_seen, status_changed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (message_id) DO UPDATE SET
                unit = excluded.unit,
                trigger_received = excluded.trigger_received,
                status_changed_at = CASE
                    WHEN triggers.status != excluded.status THEN excluded.status_changed_at
                    ELSE triggers.status_changed_at
                END,
                status = excluded.status
            """,
            (message_id, unit, status, trigger_received or "", now, now),
        )


def history(unit: str | None = None, limit: int = 100) -> list[Entry]:
    """Recorded triggers, newest first, optionally for one unit."""
    query = "SELECT message_id, unit, status, trigger_received, first_seen, status_changed_at FROM triggers"
    args: tuple = ()
    if unit:
        query += " WHERE unit = ?"
        args = (unit,)
    query += " ORDER BY trigger_received DESC, first_seen DESC LIMIT ?"
    with _db() as conn:
        rows = conn.execute(query, args + (limit,)).fetchall()
    return [Entry(*row) for row in rows]


def stats() -> dict[str, int]:
    """Counts by status."""
    with _db() as conn:
        rows = conn.execute("SELECT status, COUNT(*) FROM triggers GROUP BY status").fetchall()
    return dict(rows)
