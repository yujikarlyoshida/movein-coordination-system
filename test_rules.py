"""
Tests.

Run with:  python -m pytest test_rules.py -v
       or: python test_rules.py        (built-in runner, no dependencies)

No credentials, no network, no tenant. That is the payoff of keeping rules.py and
triggers.py pure: the decision logic, which is the part that actually matters, is
verifiable anywhere in about a second.

The cases encode the reasoning, not just current behaviour. Where a test asserts
something non-obvious, the comment says why that answer is the desired one, so a
future change that breaks it has to argue with the reasoning rather than quietly
update an expected value.
"""

from __future__ import annotations

import os
import tempfile

# The demo staging tests below record to the history log. Point it at a scratch
# file before anything imports it, as test_history.py does, so this file run on
# its own can never write to the real one.
os.environ.setdefault(
    "MOVEIN_HISTORY_PATH",
    os.path.join(tempfile.mkdtemp(prefix="movein-test-"), "history.sqlite3"),
)

import config  # noqa: E402

# Pin the senders the fixture uses, BEFORE importing anything that reads them.
#
# config.py imports config_local.py at the end if it exists, which on an
# installed copy replaces these with real colleagues' addresses. Without this
# the suite would pass on a clean checkout and fail on the machine actually
# running the tool -- the worst possible direction for a test to break, since
# it only goes red where it matters least.
#
# A test suite must assert on its own fixture, not on whoever happens to be
# staffing the leasing desk.
config.TRIGGER_SENDERS = [
    "leasing.manager@example.com",
    "leasing.specialist@example.com",
    "leasing.associate@example.com",
]
config.PROPERTY_NAME = "The Property"
config.TRIGGER_SENDER_NAMES = []

import rules  # noqa: E402
import sample_data  # noqa: E402
import triggers  # noqa: E402
from models import Verdict  # noqa: E402


# ---------------------------------------------------------------------------
# unit normalisation
# ---------------------------------------------------------------------------

def test_units_pad_to_four_digits():
    assert rules.normalise_unit("735") == "0735"
    assert rules.normalise_unit("#735") == "0735"
    assert rules.normalise_unit(" 0612 ") == "0612"
    assert rules.normalise_unit("0407") == "0407"


def test_padding_variants_collapse_to_one_unit():
    # The reason normalisation exists. "735" and "0735" are one apartment; if they
    # did not collapse, every duplicate check in the tool would silently fail.
    assert rules.normalise_unit("#735") == rules.normalise_unit("0735")


def test_non_numeric_units_are_left_alone():
    # Better to surface something odd than mangle it into a plausible wrong unit.
    assert rules.normalise_unit("PH-2") == "PH-2"
    assert rules.normalise_unit("") == ""


# ---------------------------------------------------------------------------
# trigger qualification
# ---------------------------------------------------------------------------

def test_all_configured_senders_qualify():
    for who in config.TRIGGER_SENDERS:
        assert triggers.is_trigger_sender(who)


def test_sender_matching_ignores_case_and_padding():
    assert triggers.is_trigger_sender("  Leasing.Manager@Example.com ")


def test_colleagues_are_not_trigger_senders():
    assert not triggers.is_trigger_sender("colleague@example.com")


def _with_sender_names(names, fn):
    saved = config.TRIGGER_SENDER_NAMES
    config.TRIGGER_SENDER_NAMES = names
    try:
        return fn()
    finally:
        config.TRIGGER_SENDER_NAMES = saved


def test_a_display_name_qualifies_when_there_is_no_address():
    # Outlook on the web shows names, not addresses, in the reading pane.
    ok = lambda: triggers.is_trigger_sender("", "Leasing Lead")  # noqa: E731
    assert _with_sender_names(["Leasing Lead"], ok)


def test_name_order_and_spacing_do_not_matter():
    check = lambda: (triggers.is_trigger_sender("", "Lead, Leasing")  # noqa: E731
                     and triggers.is_trigger_sender("", "  leasing   LEAD "))
    assert _with_sender_names(["Leasing Lead"], check)


def test_an_address_always_decides_over_a_name():
    # An address that is not on the list is a definite no; a matching name
    # must not rescue it.
    check = lambda: triggers.is_trigger_sender("colleague@example.com", "Leasing Lead")  # noqa: E731
    assert not _with_sender_names(["Leasing Lead"], check)


def test_an_unlisted_or_missing_name_does_not_qualify():
    check = lambda: (triggers.is_trigger_sender("", "Someone Else")  # noqa: E731
                     or triggers.is_trigger_sender("", ""))
    assert not _with_sender_names(["Leasing Lead"], check)


def test_a_named_sender_still_needs_the_move_in_event():
    # A name match alone never makes a trigger: lunch plans from the leasing
    # desk are not a move-in.
    msg = {"id": "n1", "sender": "", "sender_name": "Leasing Lead",
           "subject": "Lunch", "received": "2026-10-01T12:00:00Z",
           "body": "Are we still doing the team lunch on Friday?"}
    assert _with_sender_names(["Leasing Lead"], lambda: triggers.find_triggers([msg])) == []


def test_each_event_type_is_recognised():
    for phrase in ("lease has been generated", "added a roommate", "transfer"):
        assert triggers.mentions_trigger_event(f"Hi team, {phrase} for 1204.")


def test_field_block_alone_qualifies():
    # Phrasing drifts; the field block has been stable across every sample.
    body = "Hi team,\n\nApartment Number: 0912\nLease Start Date: 10/01/2026\n"
    assert triggers.mentions_trigger_event(body)


def test_both_halves_are_required():
    trigger_body = sample_data.MESSAGES[0]["body"]

    # Right sender, wrong content.
    assert not triggers.qualifies("leasing.manager@example.com", "Lunch on Friday?")

    # Right content, wrong sender -- a colleague quoting the trigger in a reply
    # must not re-trigger work that is already underway.
    assert not triggers.qualifies("colleague@example.com", trigger_body)


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

def test_parses_a_resident_and_normalises_the_unit():
    residents, problems = triggers.parse_residents(sample_data.MESSAGES[0]["body"])
    assert len(residents) == 1
    assert residents[0].unit == "0735"          # "#735" normalised
    assert residents[0].email == "resident-alpha@test.invalid"
    assert problems == []


def test_parses_multiple_residents():
    residents, _ = triggers.parse_residents(sample_data.MESSAGES[5]["body"])
    assert len(residents) == 2


def test_blank_unit_inherits_the_group_and_is_reported():
    # A real message listed roommates and left one apartment number blank. Guess
    # from the others so the unit is not lost, but say so rather than trusting it.
    residents, problems = triggers.parse_residents(sample_data.MESSAGES[5]["body"])
    assert all(r.unit == "1408" for r in residents)
    assert any("no apartment number" in p for p in problems)


def test_missing_email_is_reported():
    residents, problems = triggers.parse_residents(
        "Name: Resident Foxtrot\nApartment Number: 0801\n"
    )
    assert len(residents) == 1
    assert any("no email" in p for p in problems)


def test_multi_part_surnames_survive():
    # Everything after the first token is the surname. Splitting on the last
    # space instead would turn "Van Der Berg" into a first name of "Resident Van
    # Der" -- wrong, and wrong in a way nobody notices until they see it in a
    # greeting line.
    residents, _ = triggers.parse_residents(
        "Name: Resident Van Der Berg\nEmail: r@test.invalid\n"
        "Apartment Number: 1408\n"
    )
    assert residents[0].first_name == "Resident"
    assert residents[0].last_name == "Van Der Berg"


def test_tight_spacing_is_tolerated():
    # These messages are hand-written, so the space after the colon is optional.
    residents, _ = triggers.parse_residents(
        "Name:Resident Golf\nEmail:g@test.invalid\nApartment Number:#902\n"
    )
    assert residents[0].unit == "0902"


# ---------------------------------------------------------------------------
# the essential four -- and the deliberate silence about everything else
# ---------------------------------------------------------------------------

def test_only_the_essential_four_are_read():
    # Name, unit, phone, email. Everything else in the message is skipped for
    # speed -- not flagged, not stored, not mentioned.
    residents, problems = triggers.parse_residents(
        "Name: Resident India\nEmail: i@test.invalid\nPhone: 555-0155\n"
        "Apartment Number: 0406\n"
        "Pet(s) Information: dog owner\n"
        "Vehicle: Camry 2024\n"
        "License Plate: 9JPS567\n"
    )
    r = residents[0]

    assert (r.full_name, r.unit, r.phone, r.email) == (
        "Resident India", "0406", "555-0155", "i@test.invalid"
    )
    assert problems == []


def test_extra_fields_are_dropped_silently():
    # The cost of the above, pinned so it stays a decision rather than drifting
    # into a bug report. A trigger saying "dog owner" is read and discarded, and
    # the tool says nothing. That information reaches the operator by reading
    # the original email; it does not reach them through this tool.
    #
    # This was implemented the other way first, surfacing extras as notes. If a
    # future change reinstates that, this test should be deleted deliberately,
    # not patched around.
    _, problems = triggers.parse_residents(
        "Name: Resident Juliet\nEmail: j@test.invalid\nPhone: 555-0166\n"
        "Apartment Number: 0406\n"
        "Pet(s) Information: dog owner\nVehicle: Camry 2024\n"
    )
    joined = " ".join(problems).lower()

    assert "dog" not in joined
    assert "camry" not in joined
    assert "vehicle" not in joined


def test_problem_messages_never_name_a_resident():
    # Problems reach the board, the menu bar and macOS notifications -- none of
    # which are access-controlled, and one of which renders on a lock screen.
    # Residents are referred to by position instead.
    #
    # This exact leak was live and invisible until the phone check made it fire:
    # every fixture resident had an email, so the equivalent line for a missing
    # address never ran.
    _, problems = triggers.parse_residents(
        "Name: Distinctive Surname\nApartment Number: 0406\n"
        "Name: Another Person\nApartment Number: 0406\n"
    )
    blob = " ".join(problems).lower()

    assert problems                      # it must actually be reporting something
    assert "distinctive" not in blob
    assert "surname" not in blob
    assert "another" not in blob
    assert "2nd resident" in blob


def test_missing_phone_is_reported():
    # Phone is an essential, so its absence is worth saying. Contrast with a
    # missing pet field, which is not.
    _, problems = triggers.parse_residents(
        "Name: Resident Kilo\nEmail: k@test.invalid\nApartment Number: 0406\n"
    )
    assert any("no phone number" in p for p in problems)


def test_a_quoted_reply_produces_no_spurious_notes():
    # A forwarded trigger carries mail headers and a signature block, all of
    # which look like fields. None of them may generate anything.
    _, problems = triggers.parse_residents(
        "Name: Resident Lima\nEmail: l@test.invalid\nPhone: 555-0177\n"
        "Apartment Number: 0406\n"
        "\nThank you,\nA Colleague\nTel: 555-0100\nWeb: example.com\n"
        "\nFrom: Leasing Manager <leasing.manager@example.com>\n"
        "Sent: Monday, September 1, 2026 8:09 PM\n"
        "Subject: New Resident Move In\nImportance: High\n"
    )
    assert problems == []


# ---------------------------------------------------------------------------
# already-handled detection -- the part that stops duplicate outreach
# ---------------------------------------------------------------------------

def test_announcement_phrases_are_recognised():
    assert rules.is_announcement("1204 has been added to all platforms.")
    assert rules.is_announcement("Residents of 210 have been added to all platforms")
    assert not rules.is_announcement("Do we know the move-in time?")


def test_one_word_completion_reply_counts():
    # Two real move-ins were closed with a bare "Complete" on the first line,
    # signature and quoted trigger below. The phrase list missed both; they only
    # read as handled because a welcome email happened to exist as well.
    body = (
        "Complete\n"
        "A Colleague\n"
        "DIRECTOR OF EXPERIENCE\n"
        "\n"
        "From: Leasing Manager\n"
        "Subject: New Resident Move In\n"
        "Apartment Number: #1420\n"
    )
    assert rules.is_announcement(body)


def test_completion_word_inside_a_sentence_does_not_count():
    # THE FALSE-POSITIVE GUARD, and the reason "complete" is not simply in
    # HANDLED_PHRASES. A false HANDLED is the dangerous direction: the tool goes
    # quiet about a unit that still needs work and nobody finds out.
    for body in (
        "I will complete this tomorrow once the COI arrives.",
        "Waiting on the lease before I can mark it done.",
        "Can you complete the checklist for 1420?",
    ):
        assert not rules.is_announcement(body), body


def test_completion_word_must_be_the_whole_first_line():
    # "Complete" alone closes the thread. "Complete once X" is a promise, not a
    # report, and must not read as done.
    assert rules.is_terse_completion("Done\n\nA Colleague")
    assert not rules.is_terse_completion("Complete once the elevator is booked")


def test_words_between_the_verb_and_the_object_still_count():
    # Real phrasing: "Edna has added the resident of 706 to all platforms."
    # Substring matching cannot see this -- "added to all platforms" never
    # appears contiguously -- so it was missed entirely.
    assert rules.is_announcement("Edna has added the resident of 706 to all platforms.")
    assert rules.is_announcement("I have added both residents of 1408 to all platforms.")


def test_an_instruction_to_add_is_not_a_completion():
    # The false-positive guard for the patterns above. Present tense is someone
    # asking for the work; past tense is someone reporting it. Only the second
    # may mark a unit done.
    for body in (
        "Please add them to all platforms when you get a chance.",
        "Can someone add 0706 to all platforms today?",
        "We should add the resident to all platforms before Friday.",
    ):
        assert not rules.is_announcement(body), body


def test_single_system_completion_counts():
    # Observed: "<name> (0315) has been added to <resident app>" -- naming one
    # system rather than "all platforms". Previously missed entirely.
    assert rules.is_announcement("Daksh (315) has been added to the resident app.")


def test_welcome_subject_is_recognised():
    assert rules.is_welcome_email("Welcome Home # 0901")
    assert not rules.is_welcome_email("RE: New Resident Move In")


def test_announcement_must_mention_the_unit():
    # Otherwise one colleague's "added to all platforms" would mark every open
    # unit as done.
    msgs = [{"id": "x", "subject": "done", "body": "added to all platforms", "received": ""}]
    assert rules.find_evidence("0735", msgs) == []


def test_unit_match_is_not_a_loose_substring():
    # Unit 0735 must not be matched by 10735, or by an order number that happens
    # to contain the digits.
    msgs = [
        {
            "id": "x",
            "subject": "10735 - added to all platforms",
            "body": "10735 added to all platforms",
            "received": "",
        }
    ]
    assert rules.find_evidence("0735", msgs) == []


def test_a_trigger_cannot_mark_itself_handled():
    # A transfer trigger contains the unit number and could match its own
    # announcement test. Excluding the trigger by id prevents it declaring itself
    # done the moment it arrives.
    trigger = {
        "id": "t1",
        "subject": "Transfer - 0901",
        "body": "transfer ... added to all 0901",
        "received": "",
    }
    assert rules.find_evidence("0901", [trigger], trigger_id="t1") == []


# ---------------------------------------------------------------------------
# evidence must postdate the trigger it clears
# ---------------------------------------------------------------------------

config.EVIDENCE_CLOCK_SKEW_MINUTES = 5

_TRIGGER_AT = "2026-10-01T12:00:00Z"


def _announcement(received):
    return {"id": "a", "subject": "0640 - added to all platforms",
            "body": "0640 has been added to all platforms.", "received": received}


def _lease_0640(msg_id="t", subject="New Resident Move In - 0640", received=_TRIGGER_AT):
    return {"id": msg_id, "sender": "leasing.manager@example.com", "subject": subject,
            "received": received,
            "body": "Lease generated\nName: Resident Foxtrot\n"
                    "Email: f@test.invalid\nApartment Number: 0640\n"}


def test_older_evidence_does_not_clear_a_newer_trigger():
    # Unit numbers are reused. An announcement for the previous tenant's move-in
    # says nothing about this lease; counting it is a false HANDLED, the
    # dangerous direction.
    msgs = [_announcement("2026-08-01T12:00:00Z")]
    assert rules.find_evidence("0640", msgs, since=_TRIGGER_AT) == []


def test_same_day_but_earlier_evidence_does_not_count():
    msgs = [_announcement("2026-10-01T09:00:00Z")]
    assert rules.find_evidence("0640", msgs, since=_TRIGGER_AT) == []


def test_evidence_within_clock_skew_counts():
    # Two mailboxes can disagree by a minute or two. Evidence a hair before the
    # trigger is the same event, not an older one.
    msgs = [_announcement("2026-10-01T11:57:00Z")]
    assert len(rules.find_evidence("0640", msgs, since=_TRIGGER_AT)) == 1


def test_later_evidence_counts():
    msgs = [_announcement("2026-10-01T12:03:00Z")]
    assert len(rules.find_evidence("0640", msgs, since=_TRIGGER_AT)) == 1


def test_offsets_are_compared_as_instants():
    # 08:03 at UTC-4 is 12:03 UTC: after the trigger, so it counts.
    msgs = [_announcement("2026-10-01T08:03:00-04:00")]
    assert len(rules.find_evidence("0640", msgs, since=_TRIGGER_AT)) == 1


def test_evidence_with_no_readable_date_does_not_count():
    # It cannot be shown to postdate the trigger.
    for received in ("", "yesterday"):
        assert rules.find_evidence("0640", [_announcement(received)], since=_TRIGGER_AT) == []


def test_stale_announcement_leaves_the_unit_needing_outreach():
    # The reported repro, end to end: an August announcement must not mark an
    # October lease for the same unit as handled.
    msgs = [_announcement("2026-08-01T12:00:00Z"), _lease_0640()]
    (r,) = rules.evaluate(msgs)
    assert r.verdict is Verdict.NEEDS_OUTREACH
    assert r.evidence == []
    assert any("predates this trigger" in p for p in r.problems)


def test_updated_resend_is_judged_on_mail_after_the_resend():
    # A colleague handled the original; then a corrected version arrived. The
    # newest trigger wins, and only mail after IT can clear it.
    original = _lease_0640("t-old", received="2026-10-01T10:00:00Z")
    done = _announcement("2026-10-01T10:30:00Z")
    resend = _lease_0640("t-new", "UPDATED: New Resident Move In - 0640")
    (r,) = rules.evaluate([original, done, resend])
    assert r.trigger_id == "t-new"
    assert r.verdict is Verdict.NEEDS_OUTREACH

    later = _announcement("2026-10-01T12:20:00Z") | {"id": "a2"}
    (r,) = rules.evaluate([original, done, resend, later])
    assert r.verdict is Verdict.HANDLED


def test_unreadable_trigger_date_is_unclear_not_handled():
    msgs = [_announcement("2026-10-01T12:30:00Z"), _lease_0640(received="")]
    (r,) = rules.evaluate(msgs)
    assert r.verdict is Verdict.UNCLEAR
    assert r.evidence == []


def test_received_parsing_accepts_graph_shapes():
    assert rules.parse_received("2026-08-30T09:15:00Z") is not None
    assert rules.parse_received("2026-08-30T09:15:00.1234567Z") is not None
    assert rules.parse_received("2026-08-30T09:15:00") is not None
    assert rules.parse_received("not a date") is None
    assert rules.parse_received(None) is None


# ---------------------------------------------------------------------------
# end to end
# ---------------------------------------------------------------------------

def _demo():
    return {r.unit: r for r in rules.evaluate(sample_data.MESSAGES)}


def test_plain_trigger_needs_outreach():
    assert _demo()["0735"].verdict is Verdict.NEEDS_OUTREACH


def test_announced_unit_reads_as_handled():
    # A standalone "1204 - added to all platforms" that replies to nothing --
    # the shape thread-based detection would miss.
    r = _demo()["1204"]
    assert r.verdict is Verdict.HANDLED
    assert r.evidence[0].kind == "announcement"


def test_unit_with_a_sent_welcome_reads_as_handled():
    r = _demo()["0901"]
    assert r.verdict is Verdict.HANDLED
    assert r.evidence[0].kind == "welcome_sent"


def test_roommate_trigger_needs_outreach_and_carries_its_note():
    r = _demo()["1408"]
    assert r.verdict is Verdict.NEEDS_OUTREACH
    assert any("no apartment number" in p for p in r.problems)


def test_noise_produces_no_units():
    units = set(_demo())
    assert units == {"0735", "1204", "0901", "1408", "1520"}


def test_needs_outreach_sorts_above_handled():
    # The board is only useful if the actionable thing is the first thing seen.
    results = rules.evaluate(sample_data.MESSAGES)
    assert results[0].verdict is Verdict.NEEDS_OUTREACH


def test_newest_trigger_wins_for_a_repeated_unit():
    msgs = [
        {
            "id": "old", "sender": "leasing.manager@example.com", "subject": "Original",
            "received": "2026-08-01T10:00:00Z",
            "body": "Lease generated\nName: Resident Hotel\n"
                    "Email: h@test.invalid\nApartment Number: 0500\n",
        },
        {
            "id": "new", "sender": "leasing.manager@example.com", "subject": "UPDATED",
            "received": "2026-08-02T10:00:00Z",
            "body": "Lease generated\nName: Resident Hotel\n"
                    "Email: h@test.invalid\nApartment Number: 0500\n",
        },
    ]
    results = rules.evaluate(msgs)
    assert len(results) == 1
    assert results[0].trigger_subject == "UPDATED"


# ---------------------------------------------------------------------------
# corporate leases: detected, skipped, never outreach
# ---------------------------------------------------------------------------

def _lease(unit="0777", name="Resident Golf", extra="", phone="555-0177",
           email="r-golf@test.invalid", subject="New Resident Move In - 0777",
           received="2026-10-01T12:00:00Z", msg_id="corp"):
    return {"id": msg_id, "sender": "leasing.manager@example.com", "subject": subject,
            "received": received,
            "body": f"Lease generated\nName: {name}\n{extra}Phone: {phone}\n"
                    f"Email: {email}\nApartment Number: {unit}\n"}


def _signals(**kw):
    m = _lease(**kw)
    return triggers.corporate_signals(m["subject"], m["body"])


def test_a_plain_resident_lease_shows_no_corporate_signal():
    assert _signals() == []


def test_each_strong_signal_is_enough_on_its_own():
    assert _signals(extra="POC: Resident Golf\n") == ["poc_label"]
    assert _signals(extra="POC- Resident Golf\n") == ["poc_label"]
    for company in ("Example Holdings LLC", "Example Living", "Example Quarters", "Example Suites Inc."):
        assert _signals(name=company) == ["company_name"], company
    assert _signals(subject="New Resident Move In - 0777 - Example Stays") == ["company_subject"]


def test_weak_signals_are_detected():
    assert _signals(phone="N/A") == ["phone_na"]
    assert _signals(phone="n/a") == ["phone_na"]
    assert _signals(email="housing@example-co.invalid") == ["shared_address"]


def test_company_words_match_whole_words_only():
    # "Vincent" contains "inc"; "Coleman" starts with "co"; neither is a company.
    assert _signals(name="Vincent Coleman") == []
    assert _signals(name="Resident Livingston") == []
    # A POC-like word inside another word is not a label.
    assert _signals(extra="Note: Napoc: none\n") == []


def test_the_propertys_own_name_never_makes_a_lease_corporate():
    # A property called "... Living" would otherwise flag every subject.
    saved = config.PROPERTY_NAME
    config.PROPERTY_NAME = "Harbor Living"
    try:
        assert _signals(subject="Harbor Living | New Resident Move In - 0777") == []
    finally:
        config.PROPERTY_NAME = saved


def test_a_corporate_lease_reads_as_skipped_not_outreach():
    (r,) = rules.evaluate([_lease(name="Example Quarters LLC", extra="POC: Resident Golf\n")])
    assert r.verdict is Verdict.SKIPPED_CORPORATE
    assert r.problems[0].startswith("corporate lease (")


def test_a_weak_signal_alone_still_skips_but_asks_for_a_check():
    # When unsure, skip and flag, never process.
    (r,) = rules.evaluate([_lease(phone="N/A")])
    assert r.verdict is Verdict.SKIPPED_CORPORATE
    assert r.problems[0].startswith("possibly corporate (")
    assert "check by hand" in r.problems[0]


def test_corporate_wins_over_handled_evidence_and_an_unreadable_date():
    done = {"id": "d", "subject": "0777 - added to all platforms",
            "body": "0777 has been added to all platforms.", "received": "2026-10-01T13:00:00Z"}
    (r,) = rules.evaluate([_lease(extra="POC: Resident Golf\n"), done])
    assert r.verdict is Verdict.SKIPPED_CORPORATE
    (r,) = rules.evaluate([_lease(extra="POC: Resident Golf\n", received="")])
    assert r.verdict is Verdict.SKIPPED_CORPORATE


def test_a_corporate_lease_without_a_unit_is_skipped_too():
    m = _lease(extra="POC: Resident Golf\n")
    m["body"] = m["body"].replace("Apartment Number: 0777\n", "")
    results = rules.evaluate([m])
    assert [r.verdict for r in results] == [Verdict.SKIPPED_CORPORATE]


def test_corporate_notes_name_signals_never_values():
    (r,) = rules.evaluate([_lease(name="Example Quarters LLC", extra="POC: Resident Golf\n",
                                  email="housing@example-quarters.invalid", phone="N/A")])
    note = r.problems[0].lower()
    for value in ("example quarters", "golf", "housing@", "example-quarters"):
        assert value not in note


def test_skipped_sorts_after_outreach_and_unclear_before_handled():
    msgs = [
        _lease(unit="0101", msg_id="a"),
        _lease(unit="0102", msg_id="b", extra="POC: Resident Golf\n"),
        _lease(unit="0103", msg_id="c"),
        {"id": "d", "subject": "0103 - added to all platforms",
         "body": "0103 has been added to all platforms.", "received": "2026-10-01T13:00:00Z"},
    ]
    order = [r.verdict for r in rules.evaluate(msgs)]
    assert order == [Verdict.NEEDS_OUTREACH, Verdict.SKIPPED_CORPORATE, Verdict.HANDLED]


def test_the_demo_shows_its_corporate_lease_as_skipped():
    r = _demo()["1520"]
    assert r.verdict is Verdict.SKIPPED_CORPORATE


# ---------------------------------------------------------------------------
# demo mode must survive a real config_local.py
# ---------------------------------------------------------------------------

_HOSTILE_SENDERS = ["someone.real@corp.invalid"]
_HOSTILE_PROPERTY = "Hostile Local Property"


def _with_hostile_local_config(fn):
    # Stands in for an installed copy's config_local.py: real senders, none of
    # whom sent anything in the sample mailbox. Restores the suite's pins after.
    saved = (config.TRIGGER_SENDERS, config.PROPERTY_NAME)
    config.TRIGGER_SENDERS = list(_HOSTILE_SENDERS)
    config.PROPERTY_NAME = _HOSTILE_PROPERTY
    try:
        return fn()
    finally:
        config.TRIGGER_SENDERS, config.PROPERTY_NAME = saved


def test_demo_board_ignores_a_local_sender_roster():
    import app

    def run():
        results, _ = app.collect(use_demo=True)
        # The pin is scoped: the real roster is back once the demo run ends, or
        # the next real poll would match nothing.
        assert config.TRIGGER_SENDERS == _HOSTILE_SENDERS
        assert config.PROPERTY_NAME == _HOSTILE_PROPERTY
        return results

    results = _with_hostile_local_config(run)
    assert {r.unit for r in results} == {"0735", "1204", "0901", "1408", "1520"}


def test_demo_fallback_source_ignores_a_local_sender_roster():
    # get_source() hands back the sample mailbox when Graph is not configured.
    import app
    import mailsource

    original = mailsource.get_source
    mailsource.get_source = lambda *a, **k: mailsource.DemoMailSource()
    try:
        results, _ = _with_hostile_local_config(lambda: app.collect())
    finally:
        mailsource.get_source = original
    assert {r.unit for r in results} == {"0735", "1204", "0901", "1408", "1520"}


def test_demo_staging_ignores_a_local_config():
    import argparse
    import contextlib
    import io

    import app

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        _with_hostile_local_config(lambda: app.run_stage(argparse.Namespace(demo=True)))
    text = out.getvalue()
    assert "0735" in text and "1408" in text
    assert _HOSTILE_PROPERTY not in text


def test_no_resident_data_reaches_the_results():
    # The privacy property, asserted rather than trusted.
    #
    # SCOPE, precisely: this guards the *display* path -- Result objects feed the
    # board, the menu bar, notifications and the history log, none of which are
    # encrypted and one of which renders on a lock screen. Names are parsed in
    # triggers.py and must reach exactly one place: the draft addressed to those
    # residents. test_history.py checks the log file on disk the same way.
    blob = " ".join(
        f"{r.unit} {r.trigger_subject} {r.trigger_sender} "
        f"{' '.join(r.problems)} {' '.join(e.describe() for e in r.evidence)}"
        for r in rules.evaluate(sample_data.MESSAGES)
    ).lower()

    for leak in ("resident-alpha@test.invalid", "resident-bravo@test.invalid",
                 "resident-charlie@test.invalid", "resident-delta@test.invalid",
                 "resident-echo@test.invalid", "alpha", "bravo", "charlie",
                 "foxtrot", "housing@example-quarters.invalid"):
        assert leak not in blob


# ---------------------------------------------------------------------------
# minimal runner, so the suite works without pytest installed
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import traceback

    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]

    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
        except Exception:
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
        else:
            passed += 1
            print(f"ok    {name}")

    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
