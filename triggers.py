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
    # Corporate-lease signal codes (see corporate_signals). Non-empty means the
    # lease is out of scope: shown as skipped, never staged.
    corporate: list[str] = field(default_factory=list)

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

def _normalise_name(name: str) -> str:
    """
    "Lead, Leasing" and " leasing  LEAD " both become "leasing lead".

    Directories differ on name order, and a reading pane may add stray spacing.
    """
    text = " ".join((name or "").split()).lower()
    if "," in text:
        last, _, first = text.partition(",")
        text = f"{first.strip()} {last.strip()}".strip()
    return text


def is_trigger_sender(address: str, name: str = "") -> bool:
    """
    True when the sender is one of the configured trigger senders.

    The address decides whenever there is one. Outlook on the web shows only a
    display name in the reading pane, so a source that has no address may pass
    the name instead, checked against config.TRIGGER_SENDER_NAMES. A name is
    never consulted when an address is present: an address that is not on the
    list is a definite no, not a reason to try something looser.
    """
    if address.strip():
        return address.strip().lower() in {s.lower() for s in config.TRIGGER_SENDERS}
    wanted = {_normalise_name(n) for n in getattr(config, "TRIGGER_SENDER_NAMES", [])}
    return bool(name.strip()) and _normalise_name(name) in wanted


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


def qualifies(sender: str, body: str, sender_name: str = "") -> bool:
    return is_trigger_sender(sender, sender_name) and mentions_trigger_event(body)


# ---------------------------------------------------------------------------
# Corporate leases (out of scope: detected, then skipped)
# ---------------------------------------------------------------------------

# Signal codes, and how each reads on the board. Codes, never values: the
# company's name and contact stay in the email, like a resident's would.
CORPORATE_SIGNALS = {
    "poc_label": "POC label",
    "company_name": "company in the Name field",
    "company_subject": "company in the subject",
    "phone_na": "phone given as N/A",
    "shared_address": "shared or company contact address",
}

# Any one of these is enough to call it corporate.
STRONG_CORPORATE_SIGNALS = {"poc_label", "company_name", "company_subject"}


def _has_company_word(text: str) -> bool:
    import re

    words = {w.lower() for w in config.CORPORATE_NAME_WORDS}
    tokens = re.findall(r"[a-z][a-z.]*[a-z.]|[a-z]", (text or "").lower())
    # Compare with and without a trailing full stop: "Inc." and "Inc" alike.
    return any(t in words or t.rstrip(".") in words for t in tokens)


def corporate_signals(subject: str, body: str) -> list[str]:
    """
    Which corporate-lease signals a trigger shows, as codes from CORPORATE_SIGNALS.

    Seen in real triggers for company-held leases: a POC label, a company in the
    Name field (LLC, "Living", "Quarters"), a subject naming the company, a
    phone given as N/A, a shared or company contact address. The first three
    are strong on their own; the last two are weak. Any signal at all means the
    lease is skipped -- when unsure, skip and flag, never process -- and the
    board says which, so a person can check.
    """
    import re

    found: list[str] = []
    text = body or ""

    if re.search(r"(?im)\bPOC\s*[:\-]", text):
        found.append("poc_label")

    names = re.findall(r"(?im)^\s*(?:resident\s+)?name\s*:\s*(.+?)\s*$", text)
    if any(_has_company_word(n) for n in names):
        found.append("company_name")

    # The property's own name can sit in a subject line, and may itself contain
    # a word like "Living"; it must not make every lease read as corporate.
    own = getattr(config, "PROPERTY_NAME", "") or ""
    plain_subject = re.sub(re.escape(own), " ", subject or "", flags=re.I) if own.strip() else (subject or "")
    if _has_company_word(plain_subject):
        found.append("company_subject")

    if re.search(r"(?im)^\s*phone(?:\s*number)?\s*:\s*(?:n\s*/?\s*a|none|-+)\s*$", text):
        found.append("phone_na")

    shared = {s.lower() for s in config.CORPORATE_SHARED_MAILBOXES}
    addresses = re.findall(r"(?im)^\s*e-?mail(?:\s*address)?\s*:\s*<?([^\s<>@]+)@", text)
    if any(a.lower() in shared for a in addresses):
        found.append("shared_address")

    return found


def describe_corporate(codes: list[str]) -> str:
    """The board's note for a skipped lease. Signal names only, no values."""
    reasons = ", ".join(CORPORATE_SIGNALS[c] for c in codes)
    if set(codes) & STRONG_CORPORATE_SIGNALS:
        return f"corporate lease ({reasons}); skipped, out of scope"
    return f"possibly corporate ({reasons}); skipped, check by hand"


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

# THE ESSENTIAL FOUR.
#
# Name, unit, phone, email. These are what the tool reads, records and enters.
# Everything else in a trigger email -- pets, vehicles, lease dates, birthdays,
# parking, anything leasing invents next -- is skipped, by decision, for speed.
#
# The cost is real and worth stating: a trigger saying "Pet(s) Information: dog
# owner" is read and discarded, and the tool will not mention it. That
# information still matters at orientation; it just reaches you by reading the
# original email rather than through here.
#
# This was briefly implemented the other way, surfacing unrecognised fields as
# notes. It was removed deliberately, not lost. The judgement is that four
# fields entered quickly beats six fields entered slowly, and that the operator
# reads the trigger email anyway.
ESSENTIAL_FIELDS = ("name", "unit", "phone", "email")


def _ordinal(n: int) -> str:
    """1 -> '1st'. Used to refer to a resident by position instead of by name."""
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


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

    blocks: list[dict[str, str]] = []
    current: dict[str, str] | None = None

    for line in body.splitlines():
        match = _FIELD.match(line)
        if not match:
            continue

        raw_label = match.group(1).strip().lower()
        value = match.group(2).strip()
        key = _LABEL_ALIASES.get(raw_label)

        # Anything outside the essential four is skipped without comment. See
        # ESSENTIAL_FIELDS above for what that costs and why it is the choice.
        if key is None:
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

    # Residents are referred to by position, never by name.
    #
    # These strings become Result.problems, which reach the board, the menu bar
    # and macOS notifications -- surfaces with no access control, one of which
    # renders on a lock screen. Writing "{name}: no phone number given" would put
    # a resident's name on all three.
    #
    # This was latent for a while: every resident in the fixtures had an email,
    # so the equivalent line for a missing address never fired and the privacy
    # test stayed green. Adding the phone check made it fire, and the test caught
    # it immediately. That is the argument for asserting a property rather than
    # trusting it.
    #
    # "the 2nd resident listed" is enough to find the person in the trigger email,
    # which is open in front of you, and identifies nobody to anyone else.
    for position, b in enumerate(blocks, start=1):
        name = b.get("name", "").strip()
        if not name:
            continue

        first, last = _split_name(name)
        unit = normalise_unit(b.get("unit", "")) or group_unit

        who = "the resident" if len(blocks) == 1 else f"the {_ordinal(position)} resident listed"

        if not b.get("unit"):
            problems.append(
                f"{who}: no apartment number given"
                + (f"; assuming {unit} from the rest of the message" if unit else "")
            )

        # Email and phone are essentials, so a missing one is reported. Email is
        # also required by the resident-app form, which means a resident without
        # one cannot be added there at all -- that is a blocker, not a note.
        email = b.get("email", "").strip()
        if not email:
            problems.append(f"{who}: no email address given")

        phone = b.get("phone", "").strip()
        if not phone:
            problems.append(f"{who}: no phone number given")

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

    return residents, problems


def build_trigger(message: dict) -> Trigger | None:
    """
    Turn one fetched message into a Trigger, or None if it does not qualify.

    `message` is the shape graph_client.list_recent_messages returns.
    """
    sender = message.get("sender", "")
    sender_name = message.get("sender_name", "")
    body = message.get("body", "")

    if not qualifies(sender, body, sender_name):
        return None

    residents, problems = parse_residents(body)

    return Trigger(
        message_id=message.get("id", ""),
        sender=sender or sender_name,
        subject=message.get("subject", ""),
        received=message.get("received", ""),
        residents=residents,
        problems=problems,
        corporate=corporate_signals(message.get("subject", ""), body),
    )


def find_triggers(messages: list[dict]) -> list[Trigger]:
    """Filter a batch of fetched messages down to the qualifying ones."""
    out = []
    for m in messages:
        t = build_trigger(m)
        if t is not None:
            out.append(t)
    return out
