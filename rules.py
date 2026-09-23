"""
The decision logic: given a mailbox, which units still need outreach.

Pure. Takes already-fetched messages, returns verdicts. No network, no
credentials, no I/O -- so the part most worth trusting is also the part that can
be fully tested offline, with no tenant and no mailbox. See test_rules.py.

    messages ──> triggers ──> for each unit: is there evidence it was handled?
                                    │
                                    ├── yes ──> HANDLED
                                    ├── no  ──> NEEDS OUTREACH
                                    └── unreadable ──> UNCLEAR

The "already handled" half is the one that matters. An audit of six consecutive
move-ins found five had been completed by a colleague, sometimes within three
minutes of the trigger arriving. A tool that skipped this step would propose
duplicate outreach in five cases out of six -- worse than doing nothing.
"""

from __future__ import annotations

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

    Every system writes unit numbers differently. Without one canonical form,
    "735" and "0735" read as two different apartments and the duplicate check
    silently stops working -- which is the exact failure this tool exists to
    prevent.

    Anything not a plain number (a name, a blank, a range) is returned stripped
    but otherwise untouched, so odd values stay visible rather than being mangled
    into a wrong-but-plausible unit.
    """
    text = normalise(value).lstrip("#").strip()
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
    import re

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
    import re

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


def find_evidence(unit: str, messages: list[dict], trigger_id: str = "") -> list[Evidence]:
    """
    Look for proof that `unit` has already been handled.

    Two independent signals, either sufficient:

      * a colleague's announcement mentioning this unit
      * a welcome email whose subject names this unit

    The trigger message itself is excluded by id. Without that, a trigger
    containing the word "transfer" and the unit number could match its own
    announcement test and mark itself handled the moment it arrived.
    """
    found: list[Evidence] = []

    for m in messages:
        if trigger_id and m.get("id") == trigger_id:
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
        evidence = find_evidence(unit, messages, trigger_id=getattr(trigger, "message_id", ""))

        if evidence:
            verdict = Verdict.HANDLED
        elif not getattr(trigger, "residents", None):
            # A qualifying message whose contents could not be read. Do not
            # silently drop it: an unparseable trigger is the one most likely to
            # be missed entirely.
            verdict = Verdict.UNCLEAR
        else:
            verdict = Verdict.NEEDS_OUTREACH

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
            )
        )

    # Triggers that named no unit at all cannot be keyed by unit, but still need
    # to be seen.
    for t in found:
        if not t.units:
            results.append(
                Result(
                    unit="?",
                    verdict=Verdict.UNCLEAR,
                    trigger_subject=getattr(t, "subject", ""),
                    trigger_received=getattr(t, "received", ""),
                    trigger_sender=getattr(t, "sender", ""),
                    trigger_id=getattr(t, "message_id", ""),
                    problems=list(getattr(t, "problems", [])) or ["no unit number found"],
                )
            )

    return _sorted(results)


def _sorted(results: list[Result]) -> list[Result]:
    """Urgency first, so the top of the board is always the part that matters."""
    order = {
        Verdict.NEEDS_OUTREACH: 0,
        Verdict.UNCLEAR: 1,
        Verdict.HANDLED: 2,
    }
    return sorted(results, key=lambda r: (order[r.verdict], r.unit))
