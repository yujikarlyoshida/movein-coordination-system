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

import config
import rules
import sample_data
import triggers
from models import Verdict


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
# already-handled detection -- the part that stops duplicate outreach
# ---------------------------------------------------------------------------

def test_announcement_phrases_are_recognised():
    assert rules.is_announcement("1204 has been added to all platforms.")
    assert rules.is_announcement("Residents of 210 have been added to all platforms")
    assert not rules.is_announcement("Do we know the move-in time?")


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
    assert units == {"0735", "1204", "0901", "1408"}


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


def test_no_resident_data_reaches_the_results():
    # The privacy property, asserted rather than trusted.
    #
    # SCOPE, precisely: resident data IS stored, in the encrypted local database
    # (see test_store.py). What this test guards is the *display* path -- Result
    # objects feed the board, the menu bar, notifications and the log, none of
    # which are encrypted and one of which renders on a lock screen. Names are
    # parsed in triggers.py and must reach only two places: the encrypted
    # database, and the draft addressed to those residents.
    #
    # This mattered before the database existed. It matters more now, because
    # "the tool holds no PII at all" is no longer the thing keeping it out of
    # these surfaces -- this test is.
    blob = " ".join(
        f"{r.unit} {r.trigger_subject} {r.trigger_sender} "
        f"{' '.join(r.problems)} {' '.join(e.describe() for e in r.evidence)}"
        for r in rules.evaluate(sample_data.MESSAGES)
    ).lower()

    for leak in ("resident-alpha@test.invalid", "resident-bravo@test.invalid",
                 "resident-charlie@test.invalid", "resident-delta@test.invalid",
                 "resident-echo@test.invalid", "alpha", "bravo", "charlie"):
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
