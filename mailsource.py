"""
Where mail comes from.

Two ways to reach the same mailbox, one interface. The rules engine never learns
which one is in use, so switching is a config change rather than a rewrite.

    GraphMailSource      the target. Direct Microsoft Graph, device-code sign-in,
                         read-only scopes, no dependency on this chat app being
                         open. Needs an Azure app registration first.

    ConnectorMailSource  the interim. Uses the Microsoft 365 connector that is
                         already authorised, so the tool can run today. Works
                         only while the assistant session is available, which is
                         precisely why it is not the destination.

    DemoMailSource       the sample mailbox. No credentials, no network.

WHY THIS EXISTS RATHER THAN JUST WAITING FOR THE API: the Graph path needs a
tenant admin to approve an app registration, and that request is outstanding.
Building against an interface means the waiting costs nothing -- the decision
logic, the database, the staging pipeline and the confirmation flow can all be
finished and exercised against the connector, and the day the credentials land
the only change is one line in config.

Every source returns the same dict shape, which is also the shape sample_data
uses, so demo mode exercises the identical code path:

    {"id", "sender", "subject", "received", "body"}
"""

from __future__ import annotations

import config


class MailSource:
    """Interface. Read-only by construction -- there is no send method here."""

    name = "abstract"

    def list_recent_messages(self, days: int = 30) -> list[dict]:
        raise NotImplementedError

    def is_available(self) -> tuple[bool, str]:
        """(usable, human-readable reason if not)."""
        raise NotImplementedError


class DemoMailSource(MailSource):
    """The fictional mailbox. Used by --demo and by the tests."""

    name = "demo"

    def list_recent_messages(self, days: int = 30) -> list[dict]:
        import sample_data

        return list(sample_data.MESSAGES)

    def is_available(self) -> tuple[bool, str]:
        return True, ""


class GraphMailSource(MailSource):
    """
    Direct Microsoft Graph. The destination architecture.

    Unavailable until MOVEIN_CLIENT_ID and MOVEIN_TENANT_ID are set from an
    Azure app registration. Reports that clearly rather than failing obscurely
    at the first request.
    """

    name = "graph"

    def is_available(self) -> tuple[bool, str]:
        import os

        if not os.environ.get("MOVEIN_CLIENT_ID"):
            return False, "MOVEIN_CLIENT_ID is not set (Azure app registration pending)"
        if not os.environ.get("MOVEIN_TENANT_ID"):
            return False, "MOVEIN_TENANT_ID is not set (Azure app registration pending)"
        return True, ""

    def list_recent_messages(self, days: int = 30) -> list[dict]:
        ok, reason = self.is_available()
        if not ok:
            raise RuntimeError(f"Graph source unavailable: {reason}")

        import graph_client

        return graph_client.list_recent_messages(days=days)


class ConnectorMailSource(MailSource):
    """
    The Microsoft 365 connector.

    Reads through an already-authorised connection, so it needs no app
    registration and no admin consent. The trade-off is that it only works while
    the assistant session is running -- a background daemon on your Mac cannot
    reach it. Fine for supervised runs, wrong for the always-on watcher, which
    is why Graph remains the target.

    Messages arrive here already fetched, because the connector call is made
    outside this process. `feed()` hands them in.
    """

    name = "connector"

    def __init__(self) -> None:
        self._messages: list[dict] = []

    def feed(self, messages: list[dict]) -> None:
        """Supply messages fetched via the connector."""
        self._messages = list(messages)

    def list_recent_messages(self, days: int = 30) -> list[dict]:
        return list(self._messages)

    def is_available(self) -> tuple[bool, str]:
        if not self._messages:
            return False, "no messages supplied; the connector fetch runs outside this process"
        return True, ""


def get_source(name: str | None = None) -> MailSource:
    """
    Build the configured source.

    Falls back to demo rather than crashing, because a menu bar app that dies on
    launch because a credential is missing is worse than one that runs and says
    so.
    """
    choice = (name or getattr(config, "MAIL_SOURCE", "demo")).lower()

    if choice == "graph":
        source = GraphMailSource()
        ok, _ = source.is_available()
        return source if ok else DemoMailSource()

    if choice == "connector":
        return ConnectorMailSource()

    return DemoMailSource()
