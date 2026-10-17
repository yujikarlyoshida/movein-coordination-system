"""
Data entry: what would be typed into the welcome draft, Xfinity and RiseIO.

Run with:  python -m pytest test_entry.py -v
       or: python test_entry.py

Each case is a way real lease emails vary. The rule throughout: a value is
cleaned into the exact form a system needs, or it is dropped and reported so
the user supplies it. Nothing plausible-but-wrong is ever passed on. All data
is fictional.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "MOVEIN_HISTORY_PATH",
    os.path.join(tempfile.mkdtemp(prefix="movein-test-"), "history.sqlite3"),
)

import config  # noqa: E402

# The same roster as every other suite: the test files share one config.
config.TRIGGER_SENDERS = [
    "leasing.manager@example.com",
    "leasing.specialist@example.com",
    "leasing.associate@example.com",
]
config.TRIGGER_SENDER_NAMES = []
config.PROPERTY_NAME = "The Property"

import rules  # noqa: E402
import staging  # noqa: E402
import triggers  # noqa: E402


def _lease(name="Resident Alpha", phone="201-555-0142", email="resident-alpha@test.invalid",
           unit="#735", start="09/20/2026", sep=": "):
    body = "Hi Team,\n\nThis resident's lease has been generated.\n\n"
    for label, value in (("Name", name), ("Phone", phone), ("Email", email),
                         ("Apartment Number", unit), ("Lease Start Date", start)):
        if value is not None:
            body += f"{label}{sep}{value}\n"
    return {"id": "e1", "sender": "leasing.manager@example.com", "subject": "New Resident Move In",
            "received": "2026-10-01T12:00:00Z", "body": body}


def _resident(**kw):
    (t,) = triggers.find_triggers([_lease(**kw)])
    return t.residents[0], t.problems


def _actions(**kw):
    (plan,) = staging.collect([_lease(**kw)])
    return {a.kind: a.payload for a in plan.actions}


# ---------------------------------------------------------------------------
# cleaning
# ---------------------------------------------------------------------------

def test_emails_are_cleaned_or_dropped():
    assert triggers.clean_email("Resident.Echo@Test.Invalid.") == "resident.echo@test.invalid"
    assert triggers.clean_email("<mailto:r-g@test.invalid>") == "r-g@test.invalid"
    assert triggers.clean_email(" r@test.invalid; ") == "r@test.invalid"
    for junk in ("", "resident at test dot invalid", "r@", "@test.invalid", "r@test"):
        assert triggers.clean_email(junk) == "", junk


def test_phones_are_ten_digits_or_dropped():
    assert triggers.clean_phone("(201) 555-0145") == "201-555-0145"
    assert triggers.clean_phone("+1 201.555.0146") == "201-555-0146"
    assert triggers.clean_phone("2015550147") == "201-555-0147"
    # No area code is not a phone Xfinity can use; never guess one.
    for junk in ("555-0142", "N/A", "", "call the office"):
        assert triggers.clean_phone(junk) == "", junk


def test_dates_are_one_format_or_dropped():
    for text in ("09/28/2026", "9/28/26", "September 28, 2026", "Sep 28 2026", "2026-09-28"):
        assert triggers.clean_date(text) == "09/28/2026", text
    assert triggers.clean_date("TBD") == ""


def test_units_lose_their_label_and_gain_four_digits():
    for text in ("#735", "735", "Apt 735", "Apt. 735", "Unit #735", "Apartment 735", "Ste 735"):
        assert rules.normalise_unit(text) == "0735", text
    assert rules.normalise_unit("PH-2") == "PH-2"          # odd values stay visible


def test_name_splits_that_are_guesses_are_flagged():
    assert not triggers.name_needs_check("Resident Alpha")
    assert not triggers.name_needs_check("Resident De La Cruz")
    assert not triggers.name_needs_check("Resident Van Der Berg")
    assert triggers.name_needs_check("Mary Jo Bravo")
    assert triggers.name_needs_check("Resident Quinn Charlie")


# ---------------------------------------------------------------------------
# parsing, end to end
# ---------------------------------------------------------------------------

def test_a_clean_lease_parses_into_exact_values():
    r, problems = _resident()
    assert (r.first_name, r.last_name, r.email, r.phone, r.unit, r.lease_start) == (
        "Resident", "Alpha", "resident-alpha@test.invalid", "201-555-0142", "0735", "09/20/2026")
    assert problems == []
    assert triggers.missing_fields(r) == []


def test_unreadable_values_are_dropped_and_reported_by_position():
    r, problems = _resident(email="<not an email>", phone="555-0142", start="TBD")
    assert (r.email, r.phone, r.lease_start) == ("", "", "")
    assert problems == [
        "the resident: email address couldn't be read",
        "the resident: phone number couldn't be read",
        "the resident: lease start date couldn't be read",
    ]
    assert set(triggers.missing_fields(r)) == {"email", "phone", "move-in date"}


def test_tight_spacing_still_parses():
    r, _ = _resident(sep=":")
    assert (r.last_name, r.phone, r.unit) == ("Alpha", "201-555-0142", "0735")


def test_a_guessed_name_split_blocks_every_step_until_confirmed():
    r, problems = _resident(name="Mary Jo Bravo")
    assert r.name_check and "name check" in triggers.missing_fields(r)
    assert triggers.blocked_steps(["name check"]) == ["Welcome email", "Xfinity", "RiseIO"]
    assert any("confirm the first and last name" in p for p in problems)
    assert not any("Mary" in p or "Bravo" in p for p in problems)


# ---------------------------------------------------------------------------
# what each system receives
# ---------------------------------------------------------------------------

def test_welcome_draft_uses_the_clean_address_and_four_digit_unit():
    draft = _actions(email="<mailto:Resident-Alpha@Test.Invalid>.")["welcome_draft"]
    assert draft["to"] == ["resident-alpha@test.invalid"]
    assert draft["subject"] == "The Property | Welcome Home #0735"


def test_xfinity_gets_every_field_its_wizard_asks_for():
    x = _actions(phone="(201) 555-0142", start="9/20/26")["internet_portal"]
    assert x["unit"] == "0735"
    assert x["residents"] == [{"first_name": "Resident", "last_name": "Alpha",
                               "email": "resident-alpha@test.invalid",
                               "phone": "201-555-0142", "move_in": "09/20/2026"}]


def test_riseio_gets_the_move_into_unit_step_and_no_activation_email():
    r = _actions()["resident_app"]
    assert r["move_into_unit"] == {"unit": "0735", "move_in": "09/20/2026"}
    # Activation emails the resident: off unless the user chooses it.
    assert r["send_activation_email"] is False
    assert r["residents"][0]["date_of_birth"] == config.PLACEHOLDER_BIRTHDAY


def test_draft_warnings_name_residents_by_position_only():
    (plan,) = staging.collect([_lease(email="nope")])
    assert any("the resident has no usable email" in p for p in plan.problems)
    assert not any("Resident" in p for p in plan.problems)


# ---------------------------------------------------------------------------
# runner
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
