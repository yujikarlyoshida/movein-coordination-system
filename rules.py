"""
The decision logic: given a mailbox, which units still need outreach.

Pure. Takes already-fetched messages, returns verdicts. No network, no
credentials, no I/O -- so the part most worth trusting is also the part that can
be fully tested offline, with no tenant and no mailbox. See test_rules.py.

    messages ──> triggers ──> for each unit: is there evidence it was handled?
                                    │
                                    ├── yes ──> HANDLED
                                    ├── a question or "not yet" in the thread ──> UNCLEAR (check)
                                    ├── no  ──> NEEDS OUTREACH
                                    └── unreadable ──> UNCLEAR

The "already handled" half is the one that matters. An audit of six consecutive
move-ins found five had been completed by a colleague, sometimes within three
minutes of the trigger arriving. A tool that skipped this step would propose
duplicate outreach in five cases out of six -- worse than doing nothing.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import config
from models import Evidence, Result, Verdict


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def normalise(value: object) -> str:
    """Reduce a value to a comparable stripped string."""
    if value is None:
        return ""
    return str(value).strip()


def normalise_unit(value: object) -> str:
    """
    Reduce a unit reference to the canonical four-digit form.

        "#735"   -> "0735"
        "735"    -> "0735"
        " 0612 " -> "0612"
        "Apt 742", "Unit #742", "Apartment 742", "Ste. 742" -> "0742"

    Every system writes unit numbers differently. Without one canonical form,
    "735" and "0735" read as two different apartments and the duplicate check
    silently stops working -- which is the exact failure this tool exists to
    prevent.

    Anything not a plain number (a name, a blank, a range) is returned stripped
    but otherwise untouched, so odd values stay visible rather than being mangled
    into a wrong-but-plausible unit.
    """
    import re

    text = normalise(value)
    # A leading word for "unit" carries no information: "Apt 742" is 742.
    text = re.sub(r"(?i)^(?:apt\.?|apartment|unit|suite|ste\.?|no\.?|number)\s*", "", text)
    text = text.lstrip("#").strip()
    if not text:
        return ""
    if not text.isdigit():
        return text
    return text.zfill(config.UNIT_DIGITS)


def _mentions_unit(text: str, unit: str) -> bool:
    """
    Does this text refer to the given unit?

    Checks both padded and unpadded forms, since a subject line may say "#735"
    where the canonical unit is "0735". Bounded by non-digits so that unit 0735
    is not matched by a subject mentioning 10735 or an order number.
    """
    candidates = {unit, unit.lstrip("0")}
    for form in candidates:
        if not form:
            continue
        if re.search(rf"(?<!\d){re.escape(form)}(?!\d)", text):
            return True
    return False


# ---------------------------------------------------------------------------
# Evidence that a unit is already handled
# ---------------------------------------------------------------------------

def is_announcement(body: str) -> bool:
    """
    Does this message announce that a move-in has been dealt with?

    Matched on body text rather than thread position on purpose. The obvious
    implementation -- "did anyone reply to the trigger?" -- misses a real observed
    case: a colleague sent a standalone message titled "0612 - added to all
    platforms" that replied to nothing. Body matching catches both shapes.
    """
    text = body.lower()
    if any(phrase in text for phrase in config.HANDLED_PHRASES):
        return True

    # Patterns, for the phrasings where words sit between the verb and the
    # object -- "added the resident of 706 to all platforms". See
    # config.ANNOUNCEMENT_PATTERNS.
    for pattern in getattr(config, "ANNOUNCEMENT_PATTERNS", []):
        if re.search(pattern, text):
            return True

    return is_terse_completion(body)


def is_terse_completion(body: str) -> bool:
    """
    A one-word completion reply: the whole message is "Complete", or "Done".

    WHY THIS IS SEPARATE FROM THE PHRASE LIST, AND WHY IT IS STRICT
    ---------------------------------------------------------------
    Colleagues close threads two ways. One is a sentence -- "the resident has
    been added to all platforms" -- which HANDLED_PHRASES catches. The other is
    a single word on the first line, with a signature and the quoted trigger
    below it. Two real move-ins were closed that way, and the phrase list missed
    both; they only read as handled because a welcome email happened to exist.

    "complete" could NOT simply be added to HANDLED_PHRASES. That list is a
    substring match over the whole body, and a reply saying "I will complete
    this tomorrow" would then mark the unit done -- a false HANDLED, which is
    the dangerous direction: the tool goes quiet about a unit that still needs
    work, and nobody finds out.

    So this checks the FIRST non-empty line only, and requires it to be the
    completion word and nothing else. "Complete" passes. "Complete once the COI
    arrives" does not. The quoted trigger and signature below are ignored
    because they are never the first line of a reply.
    """
    for raw in body.splitlines():
        line = raw.strip().strip(".!").lower()
        if not line:
            continue
        # Only the first line that has content gets a say.
        return line in config.TERSE_COMPLETIONS
    return False


def is_welcome_email(subject: str) -> bool:
    """Does this subject look like resident welcome outreach?"""
    text = subject.lower()
    return any(hint in text for hint in config.WELCOME_SUBJECT_HINTS)


def parse_received(value: object) -> datetime | None:
    """
    Read a message timestamp as an aware UTC datetime, or None if unreadable.

    Graph sends "2026-08-30T09:15:00Z". Older Pythons reject the "Z", and some
    sources add more fractional digits than fromisoformat accepts, so both are
    normalised first. A timestamp with no zone is taken as UTC.
    """
    text = normalise(value)
    if not text:
        return None
    text = re.sub(r"[Zz]$", "+00:00", text)
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc)


def find_evidence(
    unit: str,
    messages: list[dict],
    trigger_id: str = "",
    since: str | None = None,
) -> list[Evidence]:
    """
    Look for proof that `unit` has already been handled.

    Two independent signals, either sufficient:

      * a colleague's announcement mentioning this unit
      * a welcome email whose subject names this unit

    The trigger message itself is excluded by id. Without that, a trigger
    containing the word "transfer" and the unit number could match its own
    announcement test and mark itself handled the moment it arrived.

    With `since` (the trigger's received time), only mail at or after it counts,
    less config.EVIDENCE_CLOCK_SKEW_MINUTES. Unit numbers are reused, so an
    announcement from a previous tenant's move-in would otherwise clear a new
    lease. If `since` is given but unreadable, or a candidate's own date is
    unreadable, nothing can be shown to postdate the trigger and it does not
    count: a false HANDLED is the dangerous direction.
    """
    found: list[Evidence] = []

    cutoff = None
    if since is not None:
        cutoff = parse_received(since)
        if cutoff is None:
            return found
        cutoff -= timedelta(minutes=config.EVIDENCE_CLOCK_SKEW_MINUTES)

    for m in messages:
        if trigger_id and m.get("id") == trigger_id:
            continue

        if cutoff is not None:
            when = parse_received(m.get("received"))
            if when is None or when < cutoff:
                continue

        subject = m.get("subject", "") or ""
        body = m.get("body", "") or ""
        received = m.get("received", "") or ""

        # An announcement has to mention the unit somewhere -- subject or body --
        # or one colleague's "added to all platforms" would mark every open unit
        # as done.
        if is_announcement(body) and _mentions_unit(f"{subject}\n{body}", unit):
            found.append(Evidence("announcement", subject, received))
            continue

        if is_welcome_email(subject) and _mentions_unit(subject, unit):
            found.append(Evidence("welcome_sent", subject, received))

    return found


# Where a reply stops and the quoted email below it starts: Outlook's
# "From: ... Sent: ..." header, an "Original Message" rule, a long underscore
# line, or "On <date>, <name> wrote:".
_QUOTE_START = re.compile(
    r"(?:^|\s)(?:From:\s|-{3,}\s*Original|_{8,}|On [^\n]{0,120}? wrote:)", re.IGNORECASE
)


def thread_id(message: dict) -> str:
    """The conversation a message belongs to: Outlook on the web's conv_id, or
    Graph's conversationId. Blank when the source does not say."""
    return normalise(message.get("conv_id") or message.get("conversationId"))


def reply_text(body: str) -> str:
    """The reply itself, without the quoted email below it."""
    match = _QUOTE_START.search(body or "")
    return (body or "")[: match.start()] if match else (body or "")


def asks_question(body: str) -> bool:
    """Does the reply (not the quoted email) ask something?"""
    return "?" in reply_text(body)


def says_not_done(body: str) -> bool:
    """Does the reply (not the quoted email) say the work is still to come?
    See config.NOT_DONE_PHRASES."""
    text = " ".join(reply_text(body).lower().split())
    return any(re.search(rf"\b{re.escape(phrase)}\b", text)
               for phrase in getattr(config, "NOT_DONE_PHRASES", []))


def judge_thread(
    messages: list[dict],
    trigger_id: str,
    since: str | None,
) -> tuple[list[Evidence], str]:
    """
    What the lease email's own thread says: (evidence, why_it_needs_a_look).

    The team's SOP is to reply-all on the lease email once the work is done, so
    a reply in that thread from someone other than leasing is the main sign a
    unit is handled -- whatever words it uses. A real teammate's reply was
    missed because it matched none of the phrases is_announcement knows.

    Counted: replies in the same conversation as the trigger, dated at or after
    it (less the clock-skew allowance), from anyone who is not a lease sender.
    Leasing's own follow-ups ("adding a roommate", "thanks!") are requests, not
    completions, so they never count.

    The NEWEST such reply decides. If it asks a question, or says the work is
    still to come ("on it, will finish tomorrow"), the thread is still open --
    those are replies between people too -- so it returns no evidence and the
    reason, and the unit shows as Check rather than going quiet. Either is
    settled by a later reply that does neither.
    """
    import triggers

    trigger = next((m for m in messages if m.get("id") == trigger_id), None)
    conversation = thread_id(trigger) if trigger else ""
    if not conversation:
        return [], ""
    cutoff = parse_received(since)
    if cutoff is None:
        return [], ""
    cutoff -= timedelta(minutes=config.EVIDENCE_CLOCK_SKEW_MINUTES)

    replies = []
    for m in messages:
        if m.get("id") == trigger_id or thread_id(m) != conversation:
            continue
        if triggers.is_trigger_sender(m.get("sender", ""), m.get("sender_name", "")):
            continue
        when = parse_received(m.get("received"))
        if when is None or when < cutoff:
            continue
        replies.append((when, m))
    if not replies:
        return [], ""

    _, newest = max(replies, key=lambda pair: pair[0])
    body = newest.get("body", "") or ""
    if asks_question(body):
        return [], "a teammate asked a question in the thread; read it before acting"
    if says_not_done(body):
        return [], "a teammate's reply says it isn't done yet; read it before acting"
    return [Evidence("thread_reply", newest.get("subject", "") or "", newest.get("received", "") or "")], ""


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------

def evaluate(messages: list[dict]) -> list[Result]:
    """
    Run the whole thing: find triggers, then judge each unit against the mailbox.

    `messages` is the shape graph_client.list_recent_messages returns. Both the
    trigger senders' mail and your own sent mail need to be in here for the
    evidence half to work -- see graph_client for how that is fetched.
    """
    # Imported here rather than at module scope so this module stays free of the
    # dependency and remains trivially testable on its own.
    import triggers

    found = triggers.find_triggers(messages)

    # Newest trigger per unit wins. Corrections and updates are common -- a lease
    # start date changes, a roommate is added later -- and the latest word is the
    # one that matters.
    by_unit: dict[str, object] = {}
    for t in sorted(found, key=lambda t: getattr(t, "received", ""), reverse=True):
        for unit in t.units:
            by_unit.setdefault(unit, t)

    results: list[Result] = []

    for unit, trigger in by_unit.items():
        problems = list(getattr(trigger, "problems", []))
        trigger_id = getattr(trigger, "message_id", "")
        trigger_received = getattr(trigger, "received", "")

        # Evidence is judged against THIS trigger's time. For an updated or
        # corrected resend that is the resend, so a colleague's note about the
        # original does not clear the new version.
        evidence = find_evidence(unit, messages, trigger_id=trigger_id, since=trigger_received)
        replied, open_thread = judge_thread(messages, trigger_id, trigger_received)

        corporate = list(getattr(trigger, "corporate", []))
        if corporate:
            # Out of scope, whatever else the mailbox says. Checked first so no
            # later branch can turn a company lease into outreach.
            verdict = Verdict.SKIPPED_CORPORATE
            problems.insert(0, triggers.describe_corporate(corporate))
        elif parse_received(trigger_received) is None:
            # Without a trigger date, older mail cannot be ruled out as
            # evidence, so neither HANDLED nor a confident NEEDS OUTREACH holds.
            verdict = Verdict.UNCLEAR
            problems.append("trigger date unreadable; earlier mail could not be ruled out as evidence")
        elif open_thread:
            # The newest reply in the lease thread asks something or says the
            # work is still to come. Even with a "done" message elsewhere, an
            # open thread means a person looks.
            verdict = Verdict.UNCLEAR
            evidence = []
            problems.insert(0, open_thread)
        elif evidence or replied:
            verdict = Verdict.HANDLED
            evidence = replied + evidence
        elif not getattr(trigger, "residents", None):
            # A qualifying message whose contents could not be read. Do not
            # silently drop it: an unparseable trigger is the one most likely to
            # be missed entirely.
            verdict = Verdict.UNCLEAR
        else:
            verdict = Verdict.NEEDS_OUTREACH
            # Most often an updated resend after a colleague already acted on
            # the original. Still needs a look, but say why it might not.
            if find_evidence(unit, messages, trigger_id=trigger_id):
                problems.append("older handled mail exists for this unit but predates this trigger; not counted")

        results.append(
            Result(
                unit=unit,
                verdict=verdict,
                trigger_subject=getattr(trigger, "subject", ""),
                trigger_received=getattr(trigger, "received", ""),
                trigger_sender=getattr(trigger, "sender", ""),
                trigger_id=getattr(trigger, "message_id", ""),
                evidence=evidence,
                problems=problems,
                move_in=next((r.lease_start for r in getattr(trigger, "residents", [])
                              if r.unit == unit and r.lease_start), ""),
                # Only for units someone will act on: a handled or skipped unit's
                # gaps are nobody's to chase.
                missing=[(i + 1, triggers.missing_fields(r))
                         for i, r in enumerate(getattr(trigger, "residents", []))
                         if r.unit == unit and triggers.missing_fields(r)]
                if verdict is Verdict.NEEDS_OUTREACH else [],
            )
        )

    # Triggers that named no unit at all cannot be keyed by unit, but still need
    # to be seen.
    for t in found:
        if not t.units:
            corporate = list(getattr(t, "corporate", []))
            results.append(
                Result(
                    unit="?",
                    verdict=Verdict.SKIPPED_CORPORATE if corporate else Verdict.UNCLEAR,
                    trigger_subject=getattr(t, "subject", ""),
                    trigger_received=getattr(t, "received", ""),
                    trigger_sender=getattr(t, "sender", ""),
                    trigger_id=getattr(t, "message_id", ""),
                    problems=([triggers.describe_corporate(corporate)] if corporate else [])
                    + (list(getattr(t, "problems", [])) or ["no unit number found"]),
                )
            )

    return _sorted(results)


def _sorted(results: list[Result]) -> list[Result]:
    """Urgency first, so the top of the board is always the part that matters."""
    order = {
        Verdict.NEEDS_OUTREACH: 0,
        Verdict.UNCLEAR: 1,
        Verdict.SKIPPED_CORPORATE: 2,
        Verdict.HANDLED: 3,
    }
    return sorted(results, key=lambda r: (order[r.verdict], r.unit))


def with_marks(results: list[Result], marked: set[str]) -> list[Result]:
    """
    Units you marked done yourself, shown as Handled ("You marked this done").

    Keyed by the lease email's id, never the unit: a new lease email for the
    same unit is judged afresh. Corporate skips are left as they are.
    """
    from dataclasses import replace

    out = []
    for r in results:
        if r.trigger_id and r.trigger_id in marked and r.verdict in (Verdict.NEEDS_OUTREACH, Verdict.UNCLEAR):
            r = replace(r, verdict=Verdict.HANDLED, evidence=[Evidence("marked_by_you", "", "")])
        out.append(r)
    return out
