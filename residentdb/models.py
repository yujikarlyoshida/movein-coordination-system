"""
The data model.

Three tables, and the split between them is the point:

    MoveIn     one per unit per move-in event. No personal data at all.
    Resident   the people. Every identifying column encrypted.
    Action     an audit trail of what the tool did and what you approved.

Keeping MoveIn free of PII means the board, the menu bar and every status query
run entirely against plaintext columns and never decrypt anything. Names are
read only at the one moment they are needed -- composing a draft addressed to
that person -- and nowhere else.

WHAT IS PLAINTEXT AND WHY: unit number, status, timestamps, message ids. None of
these identifies a person on its own, and all of them need to be queryable.
Encrypting them would break every lookup the tool performs while protecting
nothing that matters.

WHAT IS ENCRYPTED: name, email, phone, date of birth. Everything that makes a
row about a specific human being.
"""

from __future__ import annotations

from django.db import models

from .fields import EncryptedTextField


class MoveIn(models.Model):
    """
    One move-in event for one unit. Contains no personal data.

    A unit can appear more than once over time -- residents move out and new
    ones arrive, roommates get added, people transfer between units. So the
    unit number alone is not the identity of a row; the pairing of unit and
    triggering message is.
    """

    class Status(models.TextChoices):
        NEEDS_OUTREACH = "needs_outreach", "Needs outreach"
        HANDLED = "handled", "Already handled by a colleague"
        UNCLEAR = "unclear", "Could not be read"
        STAGED = "staged", "Prepared, waiting for confirmation"
        COMPLETED = "completed", "Confirmed and actioned"

    # Canonical four-digit form. Indexed because nearly every query starts here.
    unit = models.CharField(max_length=8, db_index=True)

    # The Graph message id of the email that triggered this. Unique so that
    # re-reading the mailbox cannot create a second row for the same trigger --
    # the app polls continuously, so this constraint is what makes it idempotent.
    trigger_message_id = models.CharField(max_length=512, unique=True)

    trigger_subject = models.TextField(blank=True)
    trigger_sender = models.CharField(max_length=320, blank=True)
    trigger_received = models.DateTimeField(null=True, blank=True)

    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.NEEDS_OUTREACH,
        db_index=True,
    )

    # How the tool concluded someone else had handled it -- "announcement" or
    # "welcome_sent" -- and the subject line that proved it. Kept so a past
    # decision can be re-examined without re-reading the mailbox.
    evidence_kind = models.CharField(max_length=32, blank=True)
    evidence_detail = models.TextField(blank=True)

    # Parsing problems worth surfacing: a blank apartment number, a missing
    # email. Free text, one per line.
    problems = models.TextField(blank=True)

    first_seen = models.DateTimeField(auto_now_add=True)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-trigger_received", "unit"]
        indexes = [
            models.Index(fields=["status", "unit"]),
        ]

    def __str__(self) -> str:
        # Deliberately unit-and-status only. This string ends up in logs and
        # debug output, so it must never carry a resident's name.
        return f"#{self.unit} ({self.get_status_display()})"


class Resident(models.Model):
    """
    A person named in a trigger email. Every identifying column is encrypted.

    Attribute access returns plaintext; the columns on disk are ciphertext. See
    fields.EncryptedTextField for why none of these can be filtered on.
    """

    move_in = models.ForeignKey(
        MoveIn,
        on_delete=models.CASCADE,
        related_name="residents",
    )

    first_name = EncryptedTextField(blank=True)
    last_name = EncryptedTextField(blank=True)
    email = EncryptedTextField(blank=True)
    phone = EncryptedTextField(blank=True)

    # Date of birth arrives as a garbage placeholder from the trigger email and
    # is stored as text rather than a DateField, because it is not a date -- it
    # is filler for a form that demands one. Encrypted anyway: it sits in a
    # column labelled "date of birth" and should not be readable as if it were
    # real, in case a future version ever populates it properly.
    date_of_birth = EncryptedTextField(blank=True)

    # Plaintext: needed for joins and display, identifies nobody alone.
    unit = models.CharField(max_length=8, db_index=True)
    lease_start = models.CharField(max_length=32, blank=True)

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["unit", "id"]

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def __str__(self) -> str:
        # Same rule as MoveIn: no name in the repr, because this is what lands
        # in tracebacks and admin logs.
        return f"resident of #{self.unit}"


class Action(models.Model):
    """
    What the tool did, and what you approved.

    The app stages work and waits for one confirmation. This table is the record
    of both halves -- what was proposed, and whether it was approved, rejected,
    or is still waiting. It answers "did we already contact this unit" without
    re-deriving it from the mailbox, and it means a rejected proposal leaves a
    trace rather than vanishing.
    """

    class Kind(models.TextChoices):
        WELCOME_DRAFT = "welcome_draft", "Welcome email draft"
        INTERNET_PORTAL = "internet_portal", "Internet provider entry"
        RESIDENT_APP = "resident_app", "Resident app entry"

    class Outcome(models.TextChoices):
        STAGED = "staged", "Staged, awaiting confirmation"
        APPROVED = "approved", "Approved and carried out"
        REJECTED = "rejected", "Rejected by the operator"
        FAILED = "failed", "Attempted and failed"

    move_in = models.ForeignKey(
        MoveIn,
        on_delete=models.CASCADE,
        related_name="actions",
    )

    kind = models.CharField(max_length=32, choices=Kind.choices)
    outcome = models.CharField(
        max_length=32,
        choices=Outcome.choices,
        default=Outcome.STAGED,
        db_index=True,
    )

    # A short, PII-free description of what was staged: "draft for 2 resident(s)",
    # "portal entry for #0415". Never the draft body, which contains names.
    summary = models.TextField(blank=True)

    # Anything the operator still has to supply -- a portal login, a missing
    # email address. This is the "then it tells me what else it needs" half.
    blocked_on = models.TextField(blank=True)

    staged_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-staged_at"]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} for #{self.move_in.unit} — {self.outcome}"
