"""
Data structures.

The important property here is that resident personally identifiable information
never enters these objects.

`triggers.py` parses names and email addresses out of a trigger message, because
it must in order to tell residents apart within one email. Those values stay
inside the parser. What crosses into this module -- and therefore into the board,
the menu bar, and any log line -- is a unit number, a verdict, and an email
subject. There is no field here for a resident's name, address or phone number,
so none can leak into the output.

That is what makes the tool acceptable to run under the privacy constraint that
stopped the original design: the board answers "which units need attention",
which needs no PII. When somebody needs to know who lives in 0640, they open
Outlook and read it there, as themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Verdict(str, Enum):
    """What the pipeline concluded about one unit."""

    # Triggered, and nothing in the mailbox suggests anyone has picked it up.
    # This is the only state that asks anything of you.
    NEEDS_OUTREACH = "needs_outreach"

    # Triggered, and there is positive evidence a colleague already handled it --
    # an "added to all platforms" message, or a welcome email already sent for
    # that unit.
    HANDLED = "handled"

    # Triggered, but the message could not be read well enough to be sure what it
    # is asking for. Surfaced rather than dropped, because a trigger nobody can
    # parse is exactly the one most likely to be missed.
    UNCLEAR = "unclear"


@dataclass(frozen=True)
class Evidence:
    """One reason a unit was judged already handled."""

    kind: str        # "announcement" | "welcome_sent"
    subject: str
    received: str

    def describe(self) -> str:
        if self.kind == "welcome_sent":
            return f"welcome email already sent — {self.subject}"
        return f"colleague reported it done — {self.subject}"


@dataclass(frozen=True)
class Result:
    """The pipeline's conclusion about one unit."""

    unit: str
    verdict: Verdict

    # Where this unit came from. Subject and timestamp only.
    trigger_subject: str = ""
    trigger_received: str = ""
    trigger_sender: str = ""

    # The mailbox id of the triggering message. Not display data -- it is the
    # key the database stores rows against, so that a poll loop running every
    # fifteen minutes forever records each trigger exactly once. An id is not
    # personal data; it identifies an email, not a person.
    trigger_id: str = ""

    # Why it was judged handled, when it was.
    evidence: list[Evidence] = field(default_factory=list)

    # Anything the parser could not read cleanly -- a blank apartment number, a
    # missing email address. Reported rather than guessed at.
    problems: list[str] = field(default_factory=list)

    @property
    def needs_attention(self) -> bool:
        return self.verdict in (Verdict.NEEDS_OUTREACH, Verdict.UNCLEAR)

    def summary(self) -> str:
        """One-line description for the console report."""
        if self.verdict is Verdict.HANDLED:
            if self.evidence:
                return self.evidence[0].describe()
            return "already handled"
        if self.verdict is Verdict.UNCLEAR:
            return "could not read this trigger"
        return "needs outreach"
