"""
Staging: prepare everything, commit nothing.

THE RULE THIS MODULE ENFORCES: every action is worked out in full and held in
memory, and none of it touches an external system until you approve it in one
place. Compose the draft, resolve the recipients, build the portal payloads,
work out what is missing -- then stop.

Why one confirmation rather than several: a prompt per step trains you to click
through them. A single screen that shows the whole plan at once is a decision you
can actually make, and it is the last point at which rejecting costs nothing.

Why prepare everything before asking: the confirmation is only meaningful if it
shows the real thing. Asking "shall I draft a welcome email?" before composing it
is asking you to approve something neither of us has seen. Asking "here is the
draft, these two residents, this portal entry, and I still need the portal login
-- go?" is a question with enough in it to answer.

WHAT STAGING NEVER DOES:
  * send anything
  * save a draft to the mailbox
  * submit a portal form
  * write resident data anywhere outside the encrypted local database

A StagedPlan is inert. Handing one to a function that ignores `approved` would
be the bug; nothing here does that.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import config
import rules
import store
import triggers
from models import Verdict


# ---------------------------------------------------------------------------
# What a prepared action looks like
# ---------------------------------------------------------------------------

@dataclass
class StagedAction:
    """
    One thing the tool is proposing to do, worked out in full.

    `payload` holds the finished article -- the draft's subject, body and
    recipients; the field values for a portal form. It exists so the
    confirmation screen can show the actual content rather than a description
    of it.
    """

    kind: str                       # matches Action.Kind in the database
    unit: str
    summary: str                    # PII-free, safe for logs and the menu bar
    payload: dict = field(default_factory=dict)
    blocked_on: list[str] = field(default_factory=list)

    @property
    def is_blocked(self) -> bool:
        return bool(self.blocked_on)


@dataclass
class StagedPlan:
    """
    Everything prepared for one move-in, waiting on a single yes.

    Nothing in here has happened. `approved` starts False and only the
    confirmation step may change it.
    """

    unit: str
    trigger_subject: str
    actions: list[StagedAction] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    approved: bool = False

    @property
    def blocked_on(self) -> list[str]:
        """Everything the operator still has to supply, de-duplicated."""
        seen: list[str] = []
        for action in self.actions:
            for item in action.blocked_on:
                if item not in seen:
                    seen.append(item)
        return seen

    @property
    def is_actionable(self) -> bool:
        return bool(self.actions) and not self.blocked_on


# ---------------------------------------------------------------------------
# Composing the outreach
# ---------------------------------------------------------------------------

# Property name comes from config so no site-specific string is baked into the
# logic -- the same reason senders and trigger phrases live there.
WELCOME_SUBJECT = "{property} | Welcome Home #{unit}"

WELCOME_BODY = """Hello {greeting},

Welcome home! We are delighted you will be joining us and want to help you
prepare for your move. Below are a few details to ensure a smooth arrival.

Resident Orientation
We would love to invite you to a New Resident Orientation before your move-in
date. Please let us know a time that suits you and we will confirm it.

Before Move-In Day
  * Move-in funds: cashier's check or money order
  * PG&E account set up in your name
  * Renter's insurance confirmation

Elevator Reservation
Please let us know your preferred moving window so we can reserve the elevator.

If you have any questions at all, simply reply to this message.

Warm regards,
{signature}
"""


def compose_welcome(unit: str, residents: list) -> tuple[str, str, list[str], list[str]]:
    """
    Build the welcome email in full.

    Returns (subject, body, recipient addresses, problems).

    Greeting uses first names only. A resident with no email address is reported
    as a problem rather than silently dropped -- a draft addressed to one of two
    roommates looks complete and is not, which is the kind of error that reaches
    a real person.
    """
    problems: list[str] = []

    first_names = [r.first_name for r in residents if getattr(r, "first_name", "")]
    recipients = [r.email for r in residents if getattr(r, "email", "")]

    for r in residents:
        if not getattr(r, "email", ""):
            name = getattr(r, "first_name", "") or "a resident"
            problems.append(f"{name} has no email address in the trigger email")

    if len(first_names) == 0:
        greeting = "there"
    elif len(first_names) == 1:
        greeting = first_names[0]
    elif len(first_names) == 2:
        greeting = f"{first_names[0]} and {first_names[1]}"
    else:
        greeting = ", ".join(first_names[:-1]) + f" and {first_names[-1]}"

    subject = WELCOME_SUBJECT.format(
        property=getattr(config, "PROPERTY_NAME", "The Property"),
        unit=unit.lstrip("0") or unit,
    )
    body = WELCOME_BODY.format(
        greeting=greeting,
        signature=getattr(config, "SIGNATURE", "The Experience Team"),
    )

    return subject, body, recipients, problems


# ---------------------------------------------------------------------------
# Building the plan
# ---------------------------------------------------------------------------

def stage_for_move_in(move_in, residents: list) -> StagedPlan:
    """
    Prepare every action for one move-in. Commits nothing.

    Three actions, matching the three tabs you work through by hand: the welcome
    email, the internet portal entry, and the resident app entry.
    """
    plan = StagedPlan(
        unit=move_in.unit,
        trigger_subject=move_in.trigger_subject,
        problems=[p for p in (move_in.problems or "").split("\n") if p],
    )

    # ---- 1. Welcome email, composed in full.
    subject, body, recipients, compose_problems = compose_welcome(move_in.unit, residents)
    plan.problems.extend(compose_problems)

    draft_blocked = []
    if not recipients:
        draft_blocked.append("No resident email address, so there is nobody to address the draft to")

    plan.actions.append(
        StagedAction(
            kind="welcome_draft",
            unit=move_in.unit,
            # Count, never names -- this string reaches the menu bar and the log.
            summary=f"Welcome email for {len(residents)} resident(s) in #{move_in.unit}",
            payload={"subject": subject, "body": body, "to": recipients},
            blocked_on=draft_blocked,
        )
    )

    # ---- 2. Internet provider entry.
    # No public API, so this stages the values and stops at the form. The portal
    # login is not stored anywhere by this tool, hence the standing block.
    plan.actions.append(
        StagedAction(
            kind="internet_portal",
            unit=move_in.unit,
            summary=f"Internet portal entry for #{move_in.unit}",
            payload={
                "unit": move_in.unit,
                "residents": [
                    {
                        "first_name": r.first_name,
                        "last_name": r.last_name,
                        "email": r.email,
                    }
                    for r in residents
                ],
            },
            blocked_on=["Internet portal sign-in (no API; the session must be open already)"],
        )
    )

    # ---- 3. Resident app entry.
    #
    # Verified against the live form. What it actually accepts:
    #
    #   required   first name, last name, email
    #   optional   phone, birthday (three dropdowns), permission-to-enter,
    #              profile picture, free-form bio rows, tags, notes, membership
    #   ABSENT     apartment/unit -- there is no unit field on this form at all
    #
    # The missing unit field is the important one. Every other system in this
    # workflow is keyed by unit; this one is not, so a resident added here is not
    # attached to a home by the act of adding them. Whatever links the two
    # happens elsewhere, and until that is known this action cannot be called
    # complete no matter how carefully the payload is built.
    #
    # Pets and vehicles are separate records (/app/pet, /app/vehicle), not
    # fields here -- which is why the unhandled-field reports in triggers.py are
    # the right home for them rather than columns on Resident.
    birthday = getattr(config, "PLACEHOLDER_BIRTHDAY", {})
    plan.actions.append(
        StagedAction(
            kind="resident_app",
            unit=move_in.unit,
            summary=f"Resident app entry for #{move_in.unit}",
            payload={
                "unit": move_in.unit,
                "residents": [
                    {
                        # The essential four, and nothing else.
                        "first_name": r.first_name,
                        "last_name": r.last_name,
                        "email": r.email,
                        "phone": r.phone,
                        # The one exception, and it is not data: the form offers
                        # a birthday and leaving it blank invites someone to
                        # "helpfully" fill it in later with the plausible default.
                        # Writing an obvious placeholder closes that door. See
                        # config.PLACEHOLDER_BIRTHDAY.
                        "date_of_birth": birthday,
                    }
                    for r in residents
                ],
            },
            blocked_on=[
                "Resident app sign-in (no API; the session must be open already)",
                f"Resident app has no unit field — link #{move_in.unit} by hand after adding",
            ],
        )
    )

    return plan


def collect(messages: list[dict]) -> list[StagedPlan]:
    """
    The whole pipeline: messages in, staged plans out. Commits nothing external.

    Order matters. Verification runs before any preparation, so a move-in a
    colleague has already handled is recorded and then left alone -- no draft is
    composed for it, no portal payload is built. Across every move-in audited so
    far, that is the branch taken almost every time.
    """
    store.migrate()

    results = rules.evaluate(messages)
    found = triggers.find_triggers(messages)
    by_unit = {t.units[0]: t for t in found if t.units}

    plans: list[StagedPlan] = []

    for result in results:
        trigger = by_unit.get(result.unit)

        move_in = store.record_trigger(
            unit=result.unit,
            message_id=result.trigger_id,
            subject=result.trigger_subject,
            sender=result.trigger_sender,
            received=_parse_received(result.trigger_received),
            status=_status_for(result.verdict),
            evidence_kind=result.evidence[0].kind if result.evidence else "",
            evidence_detail=result.evidence[0].describe() if result.evidence else "",
            problems=result.problems,
            residents=trigger.residents if trigger else None,
        )

        # Already handled by a colleague: recorded, and that is all. Preparing
        # outreach here is the exact duplicate this tool exists to prevent.
        if result.verdict is Verdict.HANDLED:
            continue

        residents = store.residents_for(move_in)
        plans.append(stage_for_move_in(move_in, residents))

    return plans


def _status_for(verdict) -> str:
    return {
        Verdict.NEEDS_OUTREACH: "needs_outreach",
        Verdict.HANDLED: "handled",
        Verdict.UNCLEAR: "unclear",
    }.get(verdict, "unclear")


def _parse_received(value):
    """Graph timestamps are ISO 8601 with a trailing Z that Python 3.10 rejects."""
    if not value:
        return None
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# The confirmation gate
# ---------------------------------------------------------------------------

def render_confirmation(plans: list[StagedPlan]) -> str:
    """
    The single screen you approve. Everything, at once, in full.

    Shows real subject lines and real recipients, because a confirmation that
    summarises rather than shows is not something anyone can meaningfully agree
    to.
    """
    if not plans:
        return "Nothing needs you. Every triggered move-in has been handled.\n"

    lines = [
        f"{len(plans)} move-in(s) prepared. NOTHING HAS BEEN SENT OR SAVED YET.",
        "",
    ]

    for plan in plans:
        lines.append(f"─── #{plan.unit} " + "─" * 46)
        lines.append(f"    trigger: {plan.trigger_subject}")
        lines.append("")

        for action in plan.actions:
            mark = "!" if action.is_blocked else "+"
            lines.append(f"  [{mark}] {action.summary}")

            if action.kind == "welcome_draft" and action.payload.get("to"):
                lines.append(f"        to:      {', '.join(action.payload['to'])}")
                lines.append(f"        subject: {action.payload['subject']}")

            for blocker in action.blocked_on:
                lines.append(f"        needs:   {blocker}")

        if plan.problems:
            lines.append("")
            lines.append("    Parsing notes:")
            for problem in plan.problems:
                lines.append(f"      - {problem}")

        lines.append("")

    outstanding = sorted({b for plan in plans for b in plan.blocked_on})
    if outstanding:
        lines.append("Still required from you:")
        for item in outstanding:
            lines.append(f"  - {item}")
        lines.append("")

    lines.append("Approve to save the drafts. Drafts are never sent.")
    return "\n".join(lines)
