"""
Tests for the encrypted database and the staging pipeline.

Run with:  python test_store.py
       or: python -m pytest test_store.py -v

Uses a scratch database and a throwaway key, both set before anything imports
Django, so a test run can never touch the real database or the real Keychain
item.

THE TEST THAT MATTERS MOST is test_pii_is_not_readable_in_the_raw_file. It opens
the database file as bytes and greps for the names it just wrote. Every other
assertion here checks that the code behaves as designed; that one checks the
design actually holds on disk, which is the only place it counts.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field

# Must be set before store/Django are imported.
_TMP = tempfile.mkdtemp(prefix="movein-test-")
os.environ["MOVEIN_DB_PATH"] = os.path.join(_TMP, "test.sqlite3")

from cryptography.fernet import Fernet  # noqa: E402

os.environ["MOVEIN_DB_KEY"] = Fernet.generate_key().decode()

import sample_data  # noqa: E402
import staging  # noqa: E402
import store  # noqa: E402
import watcher  # noqa: E402

DB_FILE = os.environ["MOVEIN_DB_PATH"]


@dataclass
class FakeResident:
    """Stands in for triggers.TriggerResident."""

    first_name: str = "Resident"
    last_name: str = "Zulu"
    email: str = "resident-zulu@test.invalid"
    phone: str = "555-0199"
    unit: str = "0512"
    lease_start: str = "10/01/2026"
    date_of_birth: str = ""


def _fresh():
    """A migrated, empty database."""
    store.migrate()
    from residentdb.models import Action, MoveIn, Resident

    Action.objects.all().delete()
    Resident.objects.all().delete()
    MoveIn.objects.all().delete()


# ---------------------------------------------------------------------------
# encryption
# ---------------------------------------------------------------------------

def test_round_trip_returns_plaintext():
    _fresh()
    mi = store.record_trigger(
        unit="0512", message_id="m-round-trip", residents=[FakeResident()]
    )
    r = store.residents_for(mi)[0]

    assert r.first_name == "Resident"
    assert r.email == "resident-zulu@test.invalid"
    assert r.full_name == "Resident Zulu"


def test_pii_is_not_readable_in_the_raw_file():
    # The whole point. Write a record, then read the database file as bytes and
    # confirm none of the personal data appears in it.
    _fresh()
    store.record_trigger(
        unit="0512",
        message_id="m-raw-bytes",
        residents=[
            FakeResident(
                first_name="Distinctive",
                last_name="Surname",
                email="distinctive@test.invalid",
                phone="555-0177",
            )
        ],
    )

    # Close the connection so everything is flushed to disk rather than sitting
    # in a write-ahead log the test would not see.
    from django.db import connection

    connection.close()

    raw = open(DB_FILE, "rb").read()

    for secret in (b"Distinctive", b"Surname", b"distinctive@test.invalid", b"555-0177"):
        assert secret not in raw, f"{secret!r} was written in plaintext"


def test_queryable_columns_stay_plaintext():
    # The other half of the trade-off: unit and status must remain searchable,
    # because every lookup in the app starts with one of them.
    _fresh()
    store.record_trigger(unit="0512", message_id="m-queryable", residents=[FakeResident()])

    from django.db import connection

    connection.close()
    raw = open(DB_FILE, "rb").read()

    assert b"0512" in raw
    assert b"needs_outreach" in raw


def test_wrong_key_raises_rather_than_returning_garbage():
    # A wrong key must fail loudly. Returning ciphertext as if it were a name
    # would put unreadable text into an email addressed to a real person.
    from django.core.exceptions import ValidationError

    _fresh()
    mi = store.record_trigger(
        unit="0512", message_id="m-wrong-key", residents=[FakeResident()]
    )

    import residentdb.fields as fields

    original = fields._cipher
    try:
        fields._cipher = Fernet(Fernet.generate_key())
        try:
            _ = store.residents_for(mi)[0].first_name
        except ValidationError:
            pass
        else:
            raise AssertionError("decryption with the wrong key silently succeeded")
    finally:
        fields._cipher = original


# ---------------------------------------------------------------------------
# idempotence -- the property the continuous watcher depends on
# ---------------------------------------------------------------------------

def test_the_same_trigger_twice_creates_one_row():
    # The watcher re-reads the same mailbox every fifteen minutes forever. If
    # this failed, a unit open for a day would accrue ninety-six duplicate rows.
    _fresh()
    from residentdb.models import MoveIn, Resident

    for _ in range(3):
        store.record_trigger(
            unit="0512", message_id="m-repeat", residents=[FakeResident()]
        )

    assert MoveIn.objects.count() == 1
    assert Resident.objects.count() == 1


def test_status_updates_on_a_later_pass():
    # A unit that needs outreach now may be handled in an hour. Noticing that
    # transition is the tool's entire job.
    _fresh()
    store.record_trigger(unit="0512", message_id="m-transition", status="needs_outreach")
    store.record_trigger(
        unit="0512",
        message_id="m-transition",
        status="handled",
        evidence_kind="announcement",
    )

    from residentdb.models import MoveIn

    assert MoveIn.objects.get(trigger_message_id="m-transition").status == "handled"


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
    # Staged plans are inert: no action may claim to have been carried out.
    _fresh()
    plans = staging.collect(sample_data.MESSAGES)

    assert all(plan.approved is False for plan in plans)

    from residentdb.models import Action

    assert not Action.objects.filter(outcome="approved").exists()


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
    # Pausing must outlive the process. A pause held in memory would quietly
    # expire on reboot and the tool would resume without saying so.
    was_paused = watcher.is_paused()
    try:
        watcher.pause()
        assert watcher.is_paused()
        assert watcher.PAUSE_FILE.exists()

        watcher.resume()
        assert not watcher.is_paused()
    finally:
        watcher.pause() if was_paused else watcher.resume()


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
