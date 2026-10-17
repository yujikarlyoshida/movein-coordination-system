"""
The runner that carries out a confirmed unit, on the practice pages.

Run with:  python -m pytest test_autofill.py -v
       or: python test_autofill.py

Real WebKit, the practice pages in practice/, fictional residents. Skipped
where WebKit is not available.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Never the real welcome template: a scratch location with nothing in it.
os.environ.setdefault("MOVEIN_TEMPLATE_PATH", os.path.join(tempfile.mkdtemp(prefix="movein-test-"), "no-template.txt"))

os.environ.setdefault(
    "MOVEIN_HISTORY_PATH",
    os.path.join(tempfile.mkdtemp(prefix="movein-test-"), "history.sqlite3"),
)

import config  # noqa: E402

config.TRIGGER_SENDERS = [
    "leasing.manager@example.com",
    "leasing.specialist@example.com",
    "leasing.associate@example.com",
]
config.TRIGGER_SENDER_NAMES = []
config.PROPERTY_NAME = "The Property"

import autofill  # noqa: E402


def _webkit() -> bool:
    try:
        import WebKit  # noqa: F401
    except ImportError:
        return False
    return True


def _skip():
    if not _webkit():
        try:
            import pytest
        except ImportError:
            return True
        pytest.skip("WebKit (pyobjc) not available")
    return False


def _view():
    from AppKit import NSApplication
    from Foundation import NSMakeRect
    from WebKit import WKWebsiteDataStore, WKWebView, WKWebViewConfiguration

    NSApplication.sharedApplication()
    conf = WKWebViewConfiguration.alloc().init()
    conf.setWebsiteDataStore_(WKWebsiteDataStore.nonPersistentDataStore())
    return WKWebView.alloc().initWithFrame_configuration_(NSMakeRect(0, 0, 900, 800), conf)


ROOMMATES = [
    {"first": "Resident", "last": "Mike", "email": "r-mike@test.invalid",
     "phone": "201-555-0181", "move_in": "11/01/2026"},
    {"first": "Resident", "last": "November", "email": "r-november@test.invalid",
     "phone": "201-555-0182", "move_in": "11/01/2026"},
]


def _job(**kw):
    base = dict(
        unit="0735", residents=[dict(r) for r in ROOMMATES], steps=["welcome", "xfinity", "riseio"],
        welcome={"to": [r["email"] for r in ROOMMATES], "cc": "team@example.com",
                 "subject": "The Property | Welcome Home #0735", "body": "Hello Resident and Resident,"},
    )
    base.update(kw)
    return autofill.Job(**base)


def test_one_click_carries_out_every_step_for_every_resident():
    if _skip():
        return
    seen = []
    results = autofill.Runner(_view()).run(_job(), progress=lambda s, st, d: seen.append((s, st)))
    assert [(r.step, r.ok) for r in results] == [("welcome", True), ("xfinity", True), ("riseio", True)], \
        [r.detail for r in results]
    assert results[0].detail == "sent to 2 resident(s)"
    assert results[1].detail == "added 2 resident(s)"
    assert results[2].detail == "added 2 resident(s), moved into 0735, no activation email"
    # Progress reported each step starting and finishing, in order.
    assert seen == [("welcome", "running"), ("welcome", "done"), ("xfinity", "running"),
                    ("xfinity", "done"), ("riseio", "running"), ("riseio", "done")]


def test_activation_email_goes_only_when_chosen():
    if _skip():
        return
    runner = autofill.Runner(_view())
    (r,) = runner.run(_job(steps=["riseio"], residents=ROOMMATES[:1]))
    assert r.ok and r.detail.endswith("no activation email")
    assert runner._state()["activation_email_sent"] is False      # unticked though the form defaults on
    (r,) = runner.run(_job(steps=["riseio"], residents=ROOMMATES[:1], send_activation_email=True))
    assert r.ok and r.detail.endswith("activation email sent")
    assert runner._state()["activation_email_sent"] is True


def test_a_rejected_value_fails_that_step_and_says_why_while_others_run():
    if _skip():
        return
    bad = [dict(ROOMMATES[0], phone="555-0181")]          # no area code
    results = autofill.Runner(_view()).run(_job(residents=bad,
                                                 welcome={"to": [bad[0]["email"]], "cc": "",
                                                          "subject": "s", "body": "b"}))
    by = {r.step: r for r in results}
    assert by["welcome"].ok and by["riseio"].ok
    assert not by["xfinity"].ok
    assert by["xfinity"].detail == "Enter the phone as 555-555-5555."


def test_a_missing_field_stops_the_step_and_names_it():
    if _skip():
        return
    # A version of the portal page whose Phone field has gone, as a redesign
    # might do. The runner must stop, not type the phone somewhere else.
    page = Path(tempfile.mkdtemp()) / "xfinity.html"
    page.write_text(autofill.PRACTICE_PAGES["xfinity"].read_text().replace(
        '<label for="phone">Phone</label>', '<label for="phone">Mobile</label>'))
    runner = autofill.Runner(_view(), pages=dict(autofill.PRACTICE_PAGES, xfinity=page))
    (r,) = runner.run(_job(steps=["xfinity"]))
    assert not r.ok and r.detail == "could not find the Phone field"


def test_values_are_data_not_code():
    # A quote in a name must be typed as a quote, not run.
    if _skip():
        return
    odd = [dict(ROOMMATES[0], last="O'Neil\"); alert(1); (\"")]
    runner = autofill.Runner(_view())
    (r,) = runner.run(_job(steps=["riseio"], residents=odd))
    assert r.ok, r.detail
    assert runner._state()["created"]["last"] == odd[0]["last"]


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
