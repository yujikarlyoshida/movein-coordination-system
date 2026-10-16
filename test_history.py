"""
Tests for the history log and the staging pipeline.

Run with:  python test_history.py
       or: python -m pytest test_history.py -v

Uses a scratch log, set before anything imports history.py, so a test run can
never touch the real one.

THE TEST THAT MATTERS MOST is test_no_resident_data_reaches_the_log_file. It
runs the whole staging pipeline on the sample mailbox, then reads the log file
as bytes and greps for every name, address, phone number and subject in that
mailbox. The log is unencrypted on purpose; that is only safe while this holds.
"""

from __future__ import annotations

import os
import tempfile

# Must be set before history is imported.
_TMP = tempfile.mkdtemp(prefix="movein-test-")
os.environ["MOVEIN_HISTORY_PATH"] = os.path.join(_TMP, "history.sqlite3")

import config  # noqa: E402

# Same reason as test_rules.py: pin the fixture's senders so an installed copy
# with a real config_local.py does not turn the suite red.
config.TRIGGER_SENDERS = [
    "leasing.manager@example.com",
    "leasing.specialist@example.com",
    "leasing.associate@example.com",
]
config.PROPERTY_NAME = "The Property"
config.TRIGGER_SENDER_NAMES = []

import history  # noqa: E402
import sample_data  # noqa: E402
import staging  # noqa: E402
import watcher  # noqa: E402

LOG_FILE = os.environ["MOVEIN_HISTORY_PATH"]


def _fresh():
    """An empty log."""
    if os.path.exists(LOG_FILE):
        os.remove(LOG_FILE)


# ---------------------------------------------------------------------------
# what reaches disk
# ---------------------------------------------------------------------------

def test_no_resident_data_reaches_the_log_file():
    _fresh()
    staging.collect(sample_data.MESSAGES)
    raw = open(LOG_FILE, "rb").read().lower()

    for secret in (
        b"alpha", b"bravo", b"charlie", b"delta", b"echo",       # names
        b"@test.invalid",                                         # resident emails
        b"555-01",                                                # phones
        b"@example.com",                                          # senders
        b"new resident move in", b"transfer", b"roommate",        # subjects
    ):
        assert secret not in raw, f"{secret!r} was written to the history log"


def test_units_and_statuses_are_recorded():
    _fresh()
    staging.collect(sample_data.MESSAGES)
    by_unit = {e.unit: e.status for e in history.history()}

    assert by_unit == {
        "0735": "needs_outreach",
        "1408": "needs_outreach",
        "0901": "handled",
        "1204": "handled",
    }
    assert history.stats() == {"needs_outreach": 2, "handled": 2}


def test_the_log_file_is_private():
    _fresh()
    history.record(message_id="m-mode", unit="0512", status="needs_outreach")
    assert os.stat(LOG_FILE).st_mode & 0o777 == 0o600


def test_the_log_lives_outside_the_repository():
    # The strongest protection against committing it is that the file is not in
    # the project directory to begin with.
    import pathlib

    repo = pathlib.Path(__file__).resolve().parent
    assert repo not in history.LOG_PATH.resolve().parents, (
        f"the history log is inside the repository at {history.LOG_PATH}"
    )


def test_no_database_file_sits_in_the_repository():
    # Belt and braces: even a stray copy made while debugging should be caught
    # here rather than by a reviewer reading a diff.
    import pathlib

    repo = pathlib.Path(__file__).resolve().parent
    strays = [
        p for pattern in ("*.sqlite3", "*.db", "*.key")
        for p in repo.rglob(pattern)
    ]
    assert not strays, f"database or key files found inside the repo: {strays}"


# ---------------------------------------------------------------------------
# idempotence -- the property the continuous watcher depends on
# ---------------------------------------------------------------------------

def test_the_same_trigger_twice_creates_one_row():
    # The watcher re-reads the same mailbox every fifteen minutes forever. If
    # this failed, a unit open for a day would accrue ninety-six duplicate rows.
    _fresh()
    for _ in range(3):
        history.record(message_id="m-repeat", unit="0512", status="needs_outreach")

    assert len(history.history()) == 1


def test_status_updates_on_a_later_pass():
    # A unit that needs outreach now may be handled in an hour. Noticing that
    # transition is the tool's entire job.
    _fresh()
    history.record(message_id="m-transition", unit="0512", status="needs_outreach")
    (before,) = history.history()

    # Same status again: nothing changed, so the change time must not move.
    history.record(message_id="m-transition", unit="0512", status="needs_outreach")
    (same,) = history.history()
    assert same.status_changed_at == before.status_changed_at

    import time

    time.sleep(1.1)  # timestamps are to the second
    history.record(message_id="m-transition", unit="0512", status="handled")
    (after,) = history.history()

    assert after.status == "handled"
    assert after.first_seen == before.first_seen
    assert after.status_changed_at > before.status_changed_at


def test_a_trigger_with_no_message_id_is_not_recorded():
    # Nothing to key on, so it could not be updated idempotently.
    _fresh()
    history.record(message_id="", unit="?", status="unclear")
    assert history.history() == []


# ---------------------------------------------------------------------------
# staging -- prepared, not committed
# ---------------------------------------------------------------------------

def test_handled_units_are_never_staged():
    # The core safety property, restated at the pipeline level. Of the four
    # triggered units in the fixture, two were closed by a colleague; neither
    # may reach a staged plan.
    _fresh()
    plans = staging.collect(sample_data.MESSAGES)
    units = {p.unit for p in plans}

    assert units == {"0735", "1408"}
    assert "1204" not in units          # colleague announced it
    assert "0901" not in units          # welcome already sent


def test_draft_is_composed_in_full_before_confirmation():
    # A confirmation that shows a description rather than the real thing is not
    # something anyone can meaningfully approve.
    _fresh()
    plans = staging.collect(sample_data.MESSAGES)
    plan = next(p for p in plans if p.unit == "0735")
    draft = next(a for a in plan.actions if a.kind == "welcome_draft")

    assert draft.payload["subject"]
    assert draft.payload["to"] == ["resident-alpha@test.invalid"]
    assert len(draft.payload["body"]) > 200


def test_staging_commits_nothing():
    # Staged plans are inert: no plan may claim to have been approved.
    _fresh()
    plans = staging.collect(sample_data.MESSAGES)

    assert plans
    assert all(plan.approved is False for plan in plans)


def test_blockers_are_reported_not_hidden():
    # "Then it tells me what else it needs."
    _fresh()
    plans = staging.collect(sample_data.MESSAGES)
    plan = plans[0]

    assert plan.blocked_on
    assert any("sign-in" in b for b in plan.blocked_on)
    assert plan.is_actionable is False


def test_multiple_residents_share_one_draft():
    # Two roommates get one welcome email, not two.
    _fresh()
    plans = staging.collect(sample_data.MESSAGES)
    plan = next(p for p in plans if p.unit == "1408")
    drafts = [a for a in plan.actions if a.kind == "welcome_draft"]

    assert len(drafts) == 1
    assert len(drafts[0].payload["to"]) == 2


def test_a_resent_trigger_is_staged_from_the_newest_version():
    # rules.evaluate judges the newest trigger for a unit; the draft must be
    # built from that same message, not whichever one happened to be listed last.
    _fresh()

    def lease(msg_id, received, email):
        return {"id": msg_id, "sender": "leasing.manager@example.com",
                "subject": "New Resident Move In - 0500", "received": received,
                "body": f"Lease generated\nName: Resident Hotel\nEmail: {email}\n"
                        "Apartment Number: 0500\n"}

    msgs = [lease("new", "2026-08-02T10:00:00Z", "corrected@test.invalid"),
            lease("old", "2026-08-01T10:00:00Z", "original@test.invalid")]
    (plan,) = staging.collect(msgs)
    draft = next(a for a in plan.actions if a.kind == "welcome_draft")
    assert draft.payload["to"] == ["corrected@test.invalid"]


def test_summaries_carry_no_resident_names():
    # Summaries reach the menu bar, notifications and logs -- surfaces with no
    # access control. They must be counts and unit numbers only.
    _fresh()
    plans = staging.collect(sample_data.MESSAGES)

    blob = " ".join(
        a.summary for plan in plans for a in plan.actions
    ).lower()

    for name in ("alpha", "delta", "echo", "resident-alpha@test.invalid"):
        assert name not in blob


# ---------------------------------------------------------------------------
# pause state
# ---------------------------------------------------------------------------

def test_pause_survives_as_a_file():
    # Pause is a file, not an in-memory flag, so it survives the watcher being
    # restarted by the LaunchAgent. Pointed at a scratch path: the real pause
    # file belongs to whatever watcher is running on this machine.
    import pathlib

    real = watcher.PAUSE_FILE
    watcher.PAUSE_FILE = pathlib.Path(_TMP) / "pause-test" / "paused"
    try:
        assert not watcher.is_paused()
        watcher.pause()
        assert watcher.is_paused()
        assert watcher.PAUSE_FILE.exists()

        watcher.resume()
        assert not watcher.is_paused()
    finally:
        watcher.PAUSE_FILE = real


# ---------------------------------------------------------------------------
# demo mode
# ---------------------------------------------------------------------------

def test_demo_stage_never_touches_the_real_log():
    # A fresh process, as a user would run it, with HOME moved to a scratch
    # directory. If demo mode wrote to the real log it would appear under HOME.
    import subprocess
    import sys
    from pathlib import Path

    home = Path(tempfile.mkdtemp(prefix="movein-home-"))
    env = {k: v for k, v in os.environ.items() if not k.startswith("MOVEIN_")}
    env["HOME"] = str(home)

    repo = Path(__file__).resolve().parent
    proc = subprocess.run(
        [sys.executable, "app.py", "--stage", "--demo"],
        cwd=repo, env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "0735" in proc.stdout
    assert not (home / "Library" / "Application Support" / "MoveInTriage").exists()


def test_throwaway_log_replaces_only_the_real_path():
    saved = os.environ.pop("MOVEIN_HISTORY_PATH")
    try:
        history.use_throwaway_log()
        assert history.path() == ":memory:"

        # An explicit scratch path (this suite's) is left alone.
        os.environ["MOVEIN_HISTORY_PATH"] = saved
        history.use_throwaway_log()
        assert history.path() == saved
    finally:
        os.environ["MOVEIN_HISTORY_PATH"] = saved


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import traceback

    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]

    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
        except Exception:
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
        else:
            passed += 1
            print(f"ok    {name}")

    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
