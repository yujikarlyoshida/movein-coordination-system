"""
Sample mailbox for --demo and for the test suite.

Shaped exactly like graph_client.list_recent_messages output, so demo mode
exercises the real code path rather than a simplified one.

Covers every case the logic has to get right:

    0735  triggered, nothing else              -> needs outreach
    1204  triggered, colleague announced it    -> handled (announcement)
    0901  triggered, welcome already sent      -> handled (welcome_sent)
    1408  triggered, one resident's unit blank -> needs outreach, with a note
    ----  right sender, not a move-in          -> not a trigger at all
    ----  wrong sender quoting the trigger     -> not a trigger at all

Every identity here is a placeholder, deliberately shaped so it cannot be
mistaken for a person: residents are "Resident Alpha", "Resident Bravo" and so
on, addresses are on .invalid (a reserved TLD that can never resolve), and phone
numbers use the 555-01xx block reserved for fiction. Unit numbers are invented.

The only thing modelled from reality is the *shape* of the messages -- field
order, the inconsistent unit formatting ("#735" alongside "1204"), the blank
field, the reply that quotes an entire trigger. That shape is what the parser
has to survive, and none of it identifies anyone.
"""

from __future__ import annotations

MESSAGES = [
    # ---- 0735: a plain new lease, nothing follows it. The one that needs you.
    {
        "id": "m-0735-trigger",
        "sender": "leasing.manager@example.com",
        "subject": "New Resident Move In - #735",
        "received": "2026-08-30T09:15:00Z",
        "body": """Hi Team,

This resident's lease has been generated. Please reach out to the below
resident(s) to schedule their move-in orientation.

Name: Resident Alpha
Phone: 555-0142
Email: resident-alpha@test.invalid
Birthday: 04/11
Apartment Number: #735
Lease Start Date: 09/20/2026
Pet(s) Information: N/A
""",
    },

    # ---- 1204: triggered, then a colleague announced it. Must read as handled.
    {
        "id": "m-1204-trigger",
        "sender": "leasing.specialist@example.com",
        "subject": "New Resident Move In - 1204",
        "received": "2026-08-30T08:02:00Z",
        "body": """Hi Team,

This resident's lease has been generated.

Name: Resident Bravo
Email: resident-bravo@test.invalid
Apartment Number: 1204
Lease Start Date: 09/12/2026
""",
    },
    {
        # Note this replies to nothing and is titled by unit -- the standalone
        # shape that thread-based detection would miss.
        "id": "m-1204-done",
        "sender": "colleague@example.com",
        "subject": "1204 - added to all platforms",
        "received": "2026-08-30T08:40:00Z",
        "body": "Hi Team,\n\n1204 has been added to all platforms.\n\nThank you,\nTeammate",
    },

    # ---- 0901: triggered, and a welcome email has already gone out.
    {
        "id": "m-0901-trigger",
        "sender": "leasing.associate@example.com",
        "subject": "Transfer - 0901",
        "received": "2026-08-29T16:20:00Z",
        "body": """Hi Team,

This resident is transferring from another home in the building.

Name: Resident Charlie
Email: resident-charlie@test.invalid
Apartment Number: 0901
Lease Start Date: 09/03/2026
""",
    },
    {
        "id": "m-0901-welcome",
        "sender": "me@example.com",
        "subject": "Welcome Home # 0901",
        "received": "2026-08-29T17:05:00Z",
        "body": "Hello,\n\nWelcome home...",
    },

    # ---- 1408: roommate added, and one resident's apartment number is blank.
    # A blank field mid-block is a shape that occurs in practice; the parser has
    # to infer the unit from the others and say that it did.
    {
        "id": "m-1408-trigger",
        "sender": "leasing.manager@example.com",
        "subject": "Adding a roommate - #1408",
        "received": "2026-08-30T10:41:00Z",
        "body": """Hi Team,

Adding a roommate to this home.

Name: Resident Delta
Email: resident-delta@test.invalid
Apartment Number: #1408
Lease Start Date: 09/08/2026

Name: Resident Echo
Email: resident-echo@test.invalid
Apartment Number:
Lease Start Date: 09/08/2026
""",
    },

    # ---- Right sender, not a move-in. Must not trigger.
    {
        "id": "m-noise-1",
        "sender": "leasing.manager@example.com",
        "subject": "Lunch order",
        "received": "2026-08-30T12:00:00Z",
        "body": "Are we still doing the team lunch on Friday?",
    },

    # ---- Wrong sender, quoting an entire trigger. Must not re-trigger work
    # that is already underway -- this is the shape that would cause duplicate
    # outreach if sender filtering were dropped.
    {
        "id": "m-noise-2",
        "sender": "colleague@example.com",
        "subject": "RE: New Resident Move In - #735",
        "received": "2026-08-30T09:50:00Z",
        "body": """Do we know the move-in time for this one?

From: Leasing Manager
Name: Resident Alpha
Apartment Number: #735
Lease Start Date: 09/20/2026
""",
    },
]
