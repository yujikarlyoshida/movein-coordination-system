"""
Configuration for the move-in triage tool.

Everything site-specific lives here rather than being scattered through the
codebase, so pointing this at another property means writing a new config rather
than editing logic.

Nothing here is a secret. Credentials come from environment variables, loaded in
graph_client.py, and are never checked into source control.

This tool is **email only**. It reads the shared Experience mailbox and nothing
else -- no spreadsheet, no SharePoint, no per-step checklist columns. An earlier
version read a Master Sheet to track orientation, internet, COI and key issuance;
that was removed by direction. The consequence, stated plainly: this tool can
tell you a move-in has been triggered and whether anyone has visibly picked it
up, but it can no longer tell you which individual checklist steps remain. That
tracking now lives wherever the team keeps it.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Unit numbers
# ---------------------------------------------------------------------------

# Units are always four digits, zero-padded: 0407, 0901, 1527.
#
# Source data does not respect this. A single trigger email has been observed
# writing "#735" and "0812" in the same paragraph. Every unit is normalised to
# this width before being compared to anything, because "735" and "0735" must not
# read as different apartments -- that alone would defeat the duplicate check.
UNIT_DIGITS = 4


# ---------------------------------------------------------------------------
# Trigger detection
# ---------------------------------------------------------------------------

# A move-in trigger is an email from one of these people. Sender is a role, not a
# person: the same trigger has arrived from a Sales Manager and a Sales
# Specialist, and will arrive from whoever holds the desk next. Add addresses
# here rather than editing logic.
TRIGGER_SENDERS = [
    "leasing.manager@example.com",
    "leasing.specialist@example.com",
    "leasing.associate@example.com",
]

# ...and whose body describes one of the events worth acting on. Matched
# case-insensitively as substrings.
#
# Three situations, one downstream workload: a new tenancy, an extra person
# joining an existing one, and somebody moving between units. The latter two are
# why duplicate detection cannot key on resident name -- a transferring resident
# legitimately appears twice.
TRIGGER_PHRASES = [
    "lease generated",
    "lease has been generated",
    "added a roommate",
    "adding a roommate",
    "roommate",
    "transfer",
    "transferring",
]

# Structural fallback. Subject lines are inconsistent and phrasing drifts, so a
# message from a trigger sender carrying the standard field block counts as a
# trigger even when no phrase above matches.
TRIGGER_FIELD_MARKERS = [
    "apartment number",
    "lease start date",
]


# ---------------------------------------------------------------------------
# "Already handled" detection
# ---------------------------------------------------------------------------
#
# This replaces the spreadsheet cross-check, and it is the part that stops the
# tool proposing work a colleague has already finished. Five of six audited
# move-ins had been handled by someone else before this tool would have run, so
# without this the tool is not merely less useful -- it is actively harmful.
#
# Two independent signals, either sufficient.

# 1. A colleague announcing completion. Usually a reply on the trigger thread,
#    but not always: one observed case was a standalone email titled
#    "0612 - added to all platforms" that replied to nothing. Matching on body
#    text rather than threading catches both.
HANDLED_PHRASES = [
    "added to all platforms",
    "have been added to all",
    "has been added to all",
    "added to all",
    "resident has been added",
    "residents have been added",
]

# 2. A welcome email already sent for that unit. Matched on the unit number
#    appearing in the subject of something you sent.
#
#    Deliberately not matched on sender or body: each colleague personalises the
#    template with their own name and title, so body text is not a stable key.
#    The unit number is.
WELCOME_SUBJECT_HINTS = [
    "welcome",
]

# How far back to look for both triggers and evidence. Long enough to cover a
# lease generated well ahead of move-in; short enough to keep the query cheap.
LOOKBACK_DAYS = 30


# ---------------------------------------------------------------------------
# Resident-app field handling
# ---------------------------------------------------------------------------

# Date of birth on the resident-app form is filler. The trigger email supplies
# month and day; the form demands day, month and year. The field is not
# maintained as real data and is not used for anything.
#
# Deliberately 1 January 1900: unmistakably not a real birthday, so nobody
# downstream mistakes it for one, and trivially searchable if the field ever
# starts mattering. Leaving the form's own default (1 January 1990) would write a
# plausible-looking date instead, which is worse.
PLACEHOLDER_BIRTHDAY = {"day": 1, "month": "January", "year": 1900}


# ---------------------------------------------------------------------------
# Confirmation
# ---------------------------------------------------------------------------

# Nothing leaves this system without the operator saying so. Outreach email is
# written to Drafts and never transmitted; portal and resident-app entries stop
# at their confirmation screens.
CONFIRM_BEFORE_EVERY_ACTION = True
SAVE_OUTREACH_AS_DRAFT_ONLY = True


# ---------------------------------------------------------------------------
# Outreach
# ---------------------------------------------------------------------------

# Used in the welcome email subject and signature. Kept here rather than in the
# template so no site-specific name is baked into the logic.
PROPERTY_NAME = "The Property"
SIGNATURE = "The Experience Team"


# ---------------------------------------------------------------------------
# Mail source
# ---------------------------------------------------------------------------

# Where triggers are read from. See mailsource.py.
#
#   "graph"      direct Microsoft Graph -- the target. Needs an Azure app
#                registration (MOVEIN_CLIENT_ID / MOVEIN_TENANT_ID), which is
#                the only thing standing between this and a true background app.
#   "connector"  the already-authorised Microsoft 365 connection. Works today,
#                but only while an assistant session is open, so it cannot back
#                the always-on watcher.
#   "demo"       the sample mailbox. No credentials, no network.
#
# Set to "graph" the moment credentials exist; nothing else changes.
MAIL_SOURCE = "demo"


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------

# Refresh interval, seconds. The work is a couple of read-only Graph calls, so
# this is cheap; 15 minutes feels live without being noisy.
REFRESH_INTERVAL_SECONDS = 15 * 60

# Run continuously in the background from login, until explicitly paused.
#
# Pausing is deliberately persistent: it survives a restart and a reboot, because
# a pause that silently expires overnight is worse than no pause at all -- you
# would believe the tool was quiet when it had started polling again.
RUN_CONTINUOUSLY = True
PAUSE_STATE_FILE = "paused"

# Where the rendered board is written.
OUTPUT_HTML = "board.html"
