"""
Move-in triggers: finding qualifying emails and reading the residents out of them.

A trigger is an email that is BOTH:

  * from one of the people in config.TRIGGER_SENDERS, and
  * about a move-in event -- a lease generated, a roommate added, or a transfer
    (config.TRIGGER_PHRASES), or failing that carrying the standard field block
    (config.TRIGGER_FIELD_MARKERS).

Both halves matter. Sender alone would sweep in every unrelated message from the
leasing desk. Phrase alone would match colleagues discussing a move-in in reply.

Parsing is deliberately forgiving. These emails are hand-written and drift: field
labels gain and lose spaces, unit numbers appear as "#735" and "0812" in the same
paragraph, and one observed message left a resident's apartment number blank
entirely. Anything that cannot be read is reported rather than guessed at.

This module does no I/O of its own beyond the one fetch helper at the bottom, and
the parsing half is pure -- so it is testable without a mailbox.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import config
from rules import normalise_unit


@dataclass
class TriggerResident:
    """One resident named in a trigger email."""

    first_name: str
    last_name: str
    email: str
    unit: str
    lease_start: str = ""
    phone: str = ""

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


@dataclass
class Trigger:
    """A qualifying email and everything read out of it."""

    message_id: str
    sender: str
    subject: str
    received: str
    residents: list[TriggerResident] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def units(self) -> list[str]:
        """Distinct units mentioned, canonical four-digit form."""
        seen = []
        for r in self.residents:
            if r.unit and r.unit not in seen:
                seen.append(r.unit)
        return seen


# ---------------------------------------------------------------------------
# Qualification
# ---------------------------------------------------------------------------

def is_trigger_sender(address: str) -> bool:
    """True when the address is one of the configured trigger senders."""
    return address.strip().lower() in {s.lower() for s in config.TRIGGER_SENDERS}


def mentions_trigger_event(body: str) -> bool:
    """
    True when the body describes a move-in event worth acting on.

    Falls back to the structural field block, because phrasing drifts but the
    block -- Name / Email / Apartment Number / Lease Start Date -- has been
    stable across every observed sample.
    """
    text = body.lower()

    if any(phrase in text for phrase in config.TRIGGER_PHRASES):
        return True

    return all(marker in text for marker in config.TRIGGER_FIELD_MARKERS)


def qualifies(sender: str, body: str) -> bool:
    return is_trigger_sender(sender) and mentions_trigger_event(body)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

# "Label: value", tolerant of the spacing these emails actually use. Source
# messages are hand-written, so the space after the colon is optional and
# sometimes absent -- "Phone:555-0100" and "Name: A B" must both parse.
_FIELD = re.compile(r"^\s*([A-Za-z][A-Za-z'()/ ]{1,40}?)\s*:\s*(.*?)\s*$")

# Labels that begin a new resident block. A message may describe several people.
_NAME_LABELS = {"name", "resident", "resident name"}

_LABEL_ALIASES = {
    "name": "name",
    "resident": "name",
    "resident name": "name",
    "email": "email",
    "e-mail": "email",
    "phone": "phone",
    "phone number": "phone",
    "apartment number": "unit",
    "apartment": "unit",
    "apt": "unit",
    "apt number": "unit",
    "unit": "unit",
    "unit number": "unit",
    "lease start date": "lease_start",
    "lease start": "lease_start",
}

# Labels that are structurally "Label: value" but are not resident data: mail
# headers in a quoted reply, signature lines, boilerplate. These are discarded
# without comment, because reporting them would bury the genuine surprises.
#
# This list is the price of reporting unknown fields at all. Everything not in
# it and not in _LABEL_ALIASES gets surfaced, so the list has to cover the
# ordinary furniture of a forwarded email or the confirmation screen fills with
# noise and stops being read -- which is the failure mode that makes warnings
# worthless.
_IGNORABLE_LABELS = {
    "from", "sent", "to", "cc", "bcc", "subject", "date", "reply to",
    "importance", "attachments", "re", "fw", "fwd",
    "thank you", "thanks", "best", "regards", "warm regards", "sincerely",
    "hi team", "hi", "hello", "team",
    "tel", "mobile", "office", "fax", "web", "website",
    "caution", "warning", "disclaimer", "confidentiality notice",

    # Date of birth is deliberately not handled. The trigger supplies day and
    # month; the resident-app form demands a full date and the field is filler
    # (see config.PLACEHOLDER_BIRTHDAY). Flagging it every time would train the
    # operator to ignore this whole category of warning.
    "birthday", "birthdate", "date of birth", "dob",
}

# Values that mean "explicitly nothing". A trigger reading "Pet(s) Information:
# N/A" has answered the question, and reporting it as unhandled would be noise
# of exactly the kind that gets warnings ignored.
_EMPTY_VALUES = {"n/a", "na", "none", "no", "-", "--", "tbd", "n/a.", "null"}


def _is_noteworthy_unknown(label: str, value: str) -> bool:
    """
    Is this unrecognised field worth telling the operator about?

    Filters out the debris that any real email carries -- empty values, mail
    headers, signature lines, and anything long enough to be a sentence rather
    than a field.
    """
    if not value or not value.strip():
        return False
    if value.strip().lower() in _EMPTY_VALUES:
        return False
    if label in _IGNORABLE_LABELS:
        return False
    # A "label" longer than a few words is almost certainly a sentence that
    # happens to contain a colon, not a field.
    if len(label.split()) > 4:
        return False
    # Likewise a value that runs on is prose, not data.
    if len(value) > 120:
        return False
    return True


def _split_name(full: str) -> tuple[str, str]:
    """
    Split a full name into first and last.

    Everything after the first token is the surname, so double-barrelled and
    multi-part family names survive intact. A single token yields an empty
    surname rather than duplicating the first name.
    """
    parts = full.split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def parse_residents(body: str, fallback_unit: str = "") -> tuple[list[TriggerResident], list[str]]:
    """
    Read every resident described in a trigger email body.

    Returns (residents, problems). Problems are human-readable notes about what
    could not be read -- a resident with no email, a blank apartment number --
    rather than exceptions, because one unreadable resident should not discard
    the others in the same message.

    `fallback_unit` is used when a resident block omits the apartment number.
    That is not hypothetical: an observed message listed three roommates and left
    the third one's unit blank. The other two named the unit, so the group's unit
    is a far better guess than nothing -- but it is recorded as a problem so the
    operator can confirm rather than trust it silently.
    """
    residents: list[TriggerResident] = []
    problems: list[str] = []

    # Fields present in the email that the tool has no column for. Collected
    # across the whole message rather than per resident, because an unhandled
    # label rarely sits inside one person's block cleanly.
    unknown_fields: list[str] = []

    blocks: list[dict[str, str]] = []
    current: dict[str, str] | None = None

    for line in body.splitlines():
        match = _FIELD.match(line)
        if not match:
            continue

        raw_label = match.group(1).strip().lower()
        value = match.group(2).strip()
        key = _LABEL_ALIASES.get(raw_label)

        if key is None:
            # An unrecognised field. Report it rather than discarding it.
            #
            # This used to be a bare `continue`, which meant any information the
            # tool had no column for vanished without trace. The trigger template
            # carries "Pet(s) Information", and a resident who owns a dog would
            # have had that fact silently dropped -- the tool would look like it
            # had read the email completely while having thrown away the one
            # detail that changes what you do at orientation.
            #
            # Not stored, not parsed into a field: surfaced. The operator decides
            # what an unhandled field means. Adding a column for every possible
            # extra would be a losing race against whatever leasing types next;
            # saying "there was something here I do not understand" is not.
            if _is_noteworthy_unknown(raw_label, value):
                label_display = match.group(1).strip()
                unknown_fields.append(f"{label_display}: {value}")
            continue

        # A Name line starts a new resident.
        if raw_label in _NAME_LABELS:
            if current:
                blocks.append(current)
            current = {"name": value}
            continue

        if current is None:
            # Fields before any Name line: keep them as defaults for the first
            # resident, since some senders put the unit above the names.
            current = {}

        # First value wins; signature blocks repeat labels further down.
        current.setdefault(key, value)

    if current:
        blocks.append(current)

    # A unit stated anywhere in the message, used for blocks that omit it.
    group_unit = fallback_unit
    for b in blocks:
        if b.get("unit"):
            group_unit = normalise_unit(b["unit"])
            break

    for b in blocks:
        name = b.get("name", "").strip()
        if not name:
            continue

        first, last = _split_name(name)
        unit = normalise_unit(b.get("unit", "")) or group_unit

        if not b.get("unit"):
            problems.append(
                f"{name}: no apartment number given"
                + (f"; assuming {unit} from the rest of the message" if unit else "")
            )

        email = b.get("email", "").strip()
        if not email:
            problems.append(f"{name}: no email address given")

        residents.append(
            TriggerResident(
                first_name=first,
                last_name=last,
                email=email,
                unit=unit,
                lease_start=b.get("lease_start", "").strip(),
                phone=b.get("phone", "").strip(),
            )
        )

    if not residents:
        problems.append("no resident details could be read from this message")

    # Unhandled fields go last, so they read as "and also, note this" rather
    # than competing with the problems that block the work.
    for field_text in unknown_fields:
        problems.append(
            f"unhandled field, needs entering by hand — {field_text}"
        )

    return residents, problems


def build_trigger(message: dict) -> Trigger | None:
    """
    Turn one fetched message into a Trigger, or None if it does not qualify.

    `message` is the shape graph_client.list_recent_messages returns.
    """
    sender = message.get("sender", "")
    body = message.get("body", "")

    if not qualifies(sender, body):
        return None

    residents, problems = parse_residents(body)

    return Trigger(
        message_id=message.get("id", ""),
        sender=sender,
        subject=message.get("subject", ""),
        received=message.get("received", ""),
        residents=residents,
        problems=problems,
    )


def find_triggers(messages: list[dict]) -> list[Trigger]:
    """Filter a batch of fetched messages down to the qualifying ones."""
    out = []
    for m in messages:
        t = build_trigger(m)
        if t is not None:
            out.append(t)
    return out
