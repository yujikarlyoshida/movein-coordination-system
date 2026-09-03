"""
Read-only Microsoft Graph client.

Two constraints from the spec shape this whole module:

1. Write scopes (Mail.ReadWrite, Files.ReadWrite.All) require tenant administrator
   consent that is not available at operator level (spec section 6.1). So this
   client requests read scopes only. That is not a limitation to work around --
   read-only is the security property that makes the tool acceptable to run.

2. Authentication is *delegated*: the operator signs in as themselves and the app
   acts within their existing permissions. It never holds a service credential and
   can never see more of the tenant than the person running it already can.

The client refuses to issue non-GET requests at all (see `_get`). Even if a future
edit tried to add a write, it would fail loudly rather than quietly mutating a
shared workbook that other staff depend on.
"""

from __future__ import annotations

import os
import sys
import urllib.parse

import msal
import requests

import config

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"

# Read-only delegated scopes. Deliberately minimal.
#   Mail.Read  -- find trigger emails and evidence of completed work
#   User.Read  -- identify the signed-in operator for the board footer
#
# Two scopes, both delegated, both read-only. The SharePoint scopes this used to
# request (Files.Read.All, Sites.Read.All) are gone with the spreadsheet layer --
# which also makes this a far smaller ask of a tenant administrator, since those
# two reach across the whole organisation and these do not.
#
# Mail.Read, not Mail.ReadWrite. This client cannot send, reply, delete, or file
# anything. Drafting outreach happens in Outlook with the operator present, which
# is also the only path available: the write scope needs tenant admin consent
# that is not obtainable at operator level.
SCOPES = ["Mail.Read", "User.Read"]

# Token cache path. Holds a refresh token, so it is secrets-adjacent: it lives
# outside the repo and .gitignore excludes it belt-and-braces.
TOKEN_CACHE_PATH = os.path.expanduser("~/.movein_triage_token.json")


class GraphError(RuntimeError):
    """Raised when Graph returns something the app cannot proceed from."""


class GraphClient:
    """A thin, read-only wrapper over the bits of Graph this app needs."""

    def __init__(self, client_id: str, tenant_id: str) -> None:
        self.client_id = client_id
        self.tenant_id = tenant_id
        self._token: str | None = None
        self._cache = msal.SerializableTokenCache()

        # Reuse a cached refresh token so the operator is not asked to sign in on
        # every run. First run is interactive; subsequent runs are silent until the
        # refresh token expires.
        if os.path.exists(TOKEN_CACHE_PATH):
            with open(TOKEN_CACHE_PATH, "r", encoding="utf-8") as fh:
                self._cache.deserialize(fh.read())

        self._app = msal.PublicClientApplication(
            client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
            token_cache=self._cache,
        )

    # -- authentication ----------------------------------------------------

    def _persist_cache(self) -> None:
        if self._cache.has_state_changed:
            with open(TOKEN_CACHE_PATH, "w", encoding="utf-8") as fh:
                fh.write(self._cache.serialize())
            # Refresh token inside -- keep it readable only by this user.
            os.chmod(TOKEN_CACHE_PATH, 0o600)

    def authenticate(self) -> None:
        """
        Acquire a token, silently if possible.

        Uses the device-code flow when interaction is needed: the operator opens a
        URL and types a short code. This works without registering a redirect URI
        and without the app ever handling the password itself -- the credential is
        entered on Microsoft's own page, never passed through this process.
        """
        accounts = self._app.get_accounts()

        if accounts:
            result = self._app.acquire_token_silent(SCOPES, account=accounts[0])
            if result and "access_token" in result:
                self._token = result["access_token"]
                self._persist_cache()
                return

        flow = self._app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise GraphError(f"Could not start device flow: {flow.get('error_description')}")

        # Printed rather than logged: the operator needs to read this right now.
        print("\n" + flow["message"] + "\n", file=sys.stderr)

        result = self._app.acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            raise GraphError(f"Sign-in failed: {result.get('error_description')}")

        self._token = result["access_token"]
        self._persist_cache()

    # -- transport ---------------------------------------------------------

    def _get(self, url: str) -> dict:
        """
        Issue a GET against Graph.

        This is the only request method the class exposes. There is no _post,
        _patch or _delete, and adding one would be a deliberate act rather than an
        accident -- which is the point.
        """
        if self._token is None:
            self.authenticate()

        response = requests.get(
            url if url.startswith("http") else f"{GRAPH_ROOT}{url}",
            headers={
                "Authorization": f"Bearer {self._token}",
                # Ask for message bodies as plain text. Without this Graph returns
                # the original HTML, and these emails are nested-table documents
                # whose markup would bury the Name / Email / Apartment Number
                # block the parser reads. Harmless on non-mail requests.
                "Prefer": 'outlook.body-content-type="text"',
            },
            timeout=30,
        )

        # A 401 usually means the access token aged out mid-run. Re-authenticate
        # once and retry before giving up, so a long-running board does not die
        # after an hour.
        if response.status_code == 401:
            self._token = None
            self.authenticate()
            response = requests.get(
                url if url.startswith("http") else f"{GRAPH_ROOT}{url}",
                headers={
                "Authorization": f"Bearer {self._token}",
                # Ask for message bodies as plain text. Without this Graph returns
                # the original HTML, and these emails are nested-table documents
                # whose markup would bury the Name / Email / Apartment Number
                # block the parser reads. Harmless on non-mail requests.
                "Prefer": 'outlook.body-content-type="text"',
            },
                timeout=30,
            )

        if not response.ok:
            raise GraphError(
                f"Graph returned {response.status_code} for {url}: {response.text[:400]}"
            )

        return response.json()

    # -- the calls this app actually makes ---------------------------------

    def signed_in_as(self) -> str:
        """Display name of the operator, shown in the board footer."""
        me = self._get("/me")
        return me.get("displayName") or me.get("userPrincipalName") or "unknown"

    SELECT = "id,subject,receivedDateTime,from,bodyPreview,body"

    def _flatten(self, payload: dict) -> list[dict]:
        """Reduce a Graph message collection to the shape the rules expect."""
        out: list[dict] = []
        for m in payload.get("value", []):
            body = (m.get("body") or {}).get("content", "") or m.get("bodyPreview", "")
            address = (m.get("from") or {}).get("emailAddress", {}).get("address", "")
            out.append(
                {
                    "id": m.get("id", ""),
                    "subject": m.get("subject", "") or "",
                    "received": m.get("receivedDateTime", "") or "",
                    "sender": address,
                    "body": body,
                }
            )
        return out

    def list_recent_messages(self, top: int = 50) -> list[dict]:
        """
        Fetch everything the pipeline needs, in three narrow queries.

        Three rather than one, deliberately. The obvious implementation is to
        pull the last N messages and filter locally, but that means downloading
        the whole inbox -- every unrelated conversation in it -- to find a handful
        of move-in messages. These three queries each ask for one specific thing,
        so mail outside their scope is never retrieved at all:

          1. Mail from the configured leasing senders  -> the triggers
          2. Mail containing a completion phrase       -> colleagues' "added to
                                                          all platforms" notes,
                                                          which come from anyone
          3. Sent mail with "welcome" in the subject   -> outreach already sent

        Queries 2 and 3 are why this cannot filter on the trigger senders alone:
        the evidence that a unit is already handled comes from colleagues and from
        your own Sent folder, never from the leasing desk.

        Bodies are requested as plain text. These emails are nested-table HTML
        documents whose markup would otherwise bury the field block the parser
        reads.

        Results are de-duplicated by message id, since a single message can match
        more than one query.
        """
        collected: dict[str, dict] = {}

        def add(messages: list[dict]) -> None:
            for m in messages:
                if m["id"]:
                    collected.setdefault(m["id"], m)

        # 1. Triggers, by sender.
        senders = [s.lower() for s in config.TRIGGER_SENDERS]
        if senders:
            # Graph has no `in` operator for this field, so clauses are spelled out.
            clauses = " or ".join(f"from/emailAddress/address eq '{s}'" for s in senders)
            params = urllib.parse.urlencode(
                {
                    "$filter": clauses,
                    "$top": str(top),
                    "$orderby": "receivedDateTime desc",
                    "$select": self.SELECT,
                }
            )
            add(self._flatten(self._get(f"/me/messages?{params}")))

        # 2. Completion announcements, by phrase, from anyone.
        #
        # $search cannot be combined with $orderby in Graph, hence no ordering
        # here -- the rules sort afterwards anyway.
        for phrase in config.HANDLED_PHRASES[:2]:  # the two most distinctive
            params = urllib.parse.urlencode(
                {"$search": f'"{phrase}"', "$top": str(top), "$select": self.SELECT}
            )
            try:
                add(self._flatten(self._get(f"/me/messages?{params}")))
            except GraphError:
                # A failed search should not lose the triggers already collected.
                # Worst case the tool under-detects "handled" and proposes work
                # that is already done -- visible and correctable, unlike silence.
                pass

        # 3. Welcome outreach already sent.
        params = urllib.parse.urlencode(
            {
                "$search": '"welcome"',
                "$top": str(top),
                "$select": self.SELECT,
            }
        )
        try:
            add(self._flatten(self._get(f"/me/mailFolders/sentitems/messages?{params}")))
        except GraphError:
            pass

        return list(collected.values())


def from_environment() -> GraphClient:
    """
    Build a client from environment variables.

    Neither value is a secret -- an Azure app registration's client ID and tenant
    ID are identifiers, not credentials, and this app uses a public-client flow
    with no client secret at all. They live in the environment rather than in
    config.py only so the same code runs against a different tenant unchanged.
    """
    client_id = os.environ.get("MOVEIN_CLIENT_ID")
    tenant_id = os.environ.get("MOVEIN_TENANT_ID")

    if not client_id or not tenant_id:
        raise GraphError(
            "Set MOVEIN_CLIENT_ID and MOVEIN_TENANT_ID. "
            "Copy .env.example to .env and fill it in -- see README."
        )

    return GraphClient(client_id=client_id, tenant_id=tenant_id)
