"""
Database access. Import this, not `residentdb`, anywhere else in the app.

Django is used here as a standalone ORM -- configured in code, no settings
module, no web server, no manage.py. That is a supported way to run Django and
it buys three things worth having for a local app:

  * migrations, so the schema can change without hand-written ALTER statements
    or a wiped database
  * a field API clean enough that column-level encryption is nine lines
  * constraints and indexes declared next to the model rather than in SQL

Everything below is a plain function. Callers never touch a queryset, so the
ORM stays an implementation detail and the encryption cannot be bypassed by
accident.

WHERE THE DATA LIVES: ~/Library/Application Support/MoveInTriage/residents.sqlite3
That path is outside the project directory on purpose -- a database of resident
personal data must not sit inside a git repository where one `git add -A` would
commit it.
"""

from __future__ import annotations

import os
from pathlib import Path

import django
from django.conf import settings

APP_SUPPORT = Path.home() / "Library" / "Application Support" / "MoveInTriage"
DB_PATH = APP_SUPPORT / "residents.sqlite3"

_configured = False


def _configure() -> None:
    """
    Bring Django up. Idempotent, and safe to call from any entry point --
    the CLI, the menu bar app, and the tests all start differently.
    """
    global _configured
    if _configured:
        return

    if not settings.configured:
        # Tests point at a scratch file via this variable so a test run can
        # never touch the real database.
        db_path = os.environ.get("MOVEIN_DB_PATH", str(DB_PATH))

        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        settings.configure(
            INSTALLED_APPS=["residentdb"],
            DATABASES={
                "default": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": db_path,
                    "OPTIONS": {
                        # Wait rather than fail if the menu bar poll and a CLI
                        # run collide on the same file.
                        "timeout": 20,
                    },
                }
            },
            # Timestamps are stored in UTC and converted for display. Mixing
            # naive and aware datetimes across a poll loop that runs through a
            # daylight-saving change is a genuinely nasty class of bug.
            USE_TZ=True,
            TIME_ZONE="UTC",
            DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
        )

    django.setup()
    _configured = True


def migrate(verbose: bool = False) -> None:
    """
    Create or upgrade the schema. Called on startup; cheap when already current.
    """
    _configure()
    from django.core.management import call_command

    call_command("migrate", verbosity=1 if verbose else 0, interactive=False)

    # Tighten permissions on the database file itself. Encryption protects the
    # column contents, but the unit numbers, timestamps and status history are
    # plaintext and still nobody else's business.
    db_path = os.environ.get("MOVEIN_DB_PATH", str(DB_PATH))
    if db_path != ":memory:" and Path(db_path).exists():
        os.chmod(db_path, 0o600)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def record_trigger(
    *,
    unit: str,
    message_id: str,
    subject: str = "",
    sender: str = "",
    received=None,
    status: str = "needs_outreach",
    evidence_kind: str = "",
    evidence_detail: str = "",
    problems: list[str] | None = None,
    residents: list | None = None,
):
    """
    Record a move-in, or update it if this trigger has been seen before.

    Keyed on the trigger's message id, so the continuous poll loop can call this
    every fifteen minutes forever without duplicating anything. Status and
    evidence are refreshed on each pass -- a unit that reads NEEDS_OUTREACH now
    may well read HANDLED an hour later once a colleague replies, and that
    transition is exactly what the tool exists to notice.

    `residents` takes TriggerResident objects from triggers.py. They are written
    once, on first sight, and not rewritten on later passes -- re-encrypting
    unchanged names on every poll would churn the file for no benefit.
    """
    _configure()
    from residentdb.models import MoveIn, Resident

    move_in, created = MoveIn.objects.update_or_create(
        trigger_message_id=message_id,
        defaults={
            "unit": unit,
            "trigger_subject": subject,
            "trigger_sender": sender,
            "trigger_received": received,
            "status": status,
            "evidence_kind": evidence_kind,
            "evidence_detail": evidence_detail,
            "problems": "\n".join(problems or []),
        },
    )

    if created and residents:
        for r in residents:
            Resident.objects.create(
                move_in=move_in,
                first_name=getattr(r, "first_name", ""),
                last_name=getattr(r, "last_name", ""),
                email=getattr(r, "email", ""),
                phone=getattr(r, "phone", ""),
                date_of_birth=getattr(r, "date_of_birth", ""),
                unit=getattr(r, "unit", unit),
                lease_start=getattr(r, "lease_start", ""),
            )

    return move_in


def stage_action(move_in, *, kind: str, summary: str = "", blocked_on: str = ""):
    """
    Record that the tool has prepared something and is waiting on you.

    Staging is not doing. Nothing has been sent, saved or submitted at this
    point -- this row exists so that the confirmation screen has something to
    show and so a proposal that is never approved still leaves a trace.
    """
    _configure()
    from residentdb.models import Action

    return Action.objects.create(
        move_in=move_in,
        kind=kind,
        outcome=Action.Outcome.STAGED,
        summary=summary,
        blocked_on=blocked_on,
    )


def resolve_action(action, outcome: str) -> None:
    """Close out a staged action once you have approved or rejected it."""
    _configure()
    from django.utils import timezone

    action.outcome = outcome
    action.resolved_at = timezone.now()
    action.save(update_fields=["outcome", "resolved_at"])


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def already_recorded(message_id: str) -> bool:
    """Has this trigger been seen before? The idempotence check for the poll loop."""
    _configure()
    from residentdb.models import MoveIn

    return MoveIn.objects.filter(trigger_message_id=message_id).exists()


def outstanding():
    """
    Move-ins that still need something, newest first.

    Touches no encrypted column, so this runs without decrypting anything --
    which is what lets the menu bar refresh every fifteen minutes without ever
    holding a resident's name in memory.
    """
    _configure()
    from residentdb.models import MoveIn

    return list(
        MoveIn.objects.filter(
            status__in=[MoveIn.Status.NEEDS_OUTREACH, MoveIn.Status.UNCLEAR]
        )
    )


def history(unit: str | None = None, limit: int = 100):
    """Recorded move-ins, optionally for one unit. Also PII-free."""
    _configure()
    from residentdb.models import MoveIn

    qs = MoveIn.objects.all()
    if unit:
        qs = qs.filter(unit=unit)
    return list(qs[:limit])


def residents_for(move_in):
    """
    The people on a move-in, decrypted.

    The only function here that returns personal data. Called at exactly one
    point in the app -- composing a draft addressed to those residents -- and
    the narrowness is deliberate: if PII ever shows up somewhere it should not,
    this is the single call to look for.
    """
    _configure()
    return list(move_in.residents.all())


def stats() -> dict:
    """Counts by status, for the board header."""
    _configure()
    from django.db.models import Count

    from residentdb.models import MoveIn

    rows = MoveIn.objects.values("status").annotate(n=Count("id"))
    return {row["status"]: row["n"] for row in rows}
