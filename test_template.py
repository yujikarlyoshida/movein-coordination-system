"""
The welcome email from a saved template: formatting rebuilt from plain text.

Run with:  python -m pytest test_template.py -v
       or: python test_template.py

Uses a short FICTIONAL template in a scratch file; the real one lives only in
Application Support and is never read by the tests.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="movein-test-"))
os.environ["MOVEIN_TEMPLATE_PATH"] = str(_TMP / "welcome_template.txt")
os.environ.setdefault("MOVEIN_HISTORY_PATH", str(_TMP / "history.sqlite3"))

import config  # noqa: E402

config.TRIGGER_SENDERS = [
    "leasing.manager@example.com",
    "leasing.specialist@example.com",
    "leasing.associate@example.com",
]
config.TRIGGER_SENDER_NAMES = []
config.PROPERTY_NAME = "The Property"

import welcome_template as wt  # noqa: E402

SAMPLE = """
Hello (Resident Name),
Welcome home to The Property. Below are a few details for your arrival.

Getting Started
Here is what to know before you arrive at the building.

We will cover:
Connecting to the network
The resident app
And more
Orientations are held a few days before move-in, at a time that suits you and your schedule.

To book, open the app and go to:
Community > Services > Orientation



Payments

Checks may be sent to:

The Property
Attn: Office
100 Example Street
Springfield, ZZ 00000

Questions? Write to office@example.com.

With gratitude,
"""


def _blocks():
    return [k for k, _ in wt._blocks(SAMPLE)]


def test_structure_is_read_back_from_plain_text():
    b = wt._blocks(SAMPLE)
    kinds = [k for k, _ in b]
    assert kinds.count("heading") == 2
    assert ("list", ["Connecting to the network", "The resident app", "And more"]) in b
    assert ("path", "Community > Services > Orientation") in b
    assert ("address", ["The Property", "Attn: Office", "100 Example Street", "Springfield, ZZ 00000"]) in b
    assert "break" in kinds
    # The sentence after the list is a paragraph, not a fourth item.
    assert any(k == "para" and str(c).startswith("Orientations are held") for k, c in b)


def test_names_fill_the_placeholder():
    html_body, text = wt.render(["Maria", "James"], SAMPLE)
    assert "Hello Maria and James," in text and "(Resident Name)" not in html_body
    assert wt.render([], SAMPLE)[1].startswith("Hello there,")
    assert "Hello Ana," in wt.render(["Ana"], SAMPLE)[1]


def test_formatting_is_rebuilt_in_html():
    html_body, _ = wt.render(["Maria"], SAMPLE)
    assert "<b style=\"font-size:16px\">Getting Started</b>" in html_body
    assert "<ul" in html_body and "<li style=\"margin:0 0 4px\">The resident app</li>" in html_body
    assert "<b>Community &gt; Services &gt; Orientation</b>" in html_body
    assert "100 Example Street<br>Springfield, ZZ 00000" in html_body
    assert '<a href="mailto:office@example.com"' in html_body


def test_icon_glyphs_and_emoji_are_stripped():
    html_body, text = wt.render(["Maria"], SAMPLE + "\U0001f600✅")
    for ch in ("", "\U0001f600", "✅"):
        assert ch not in html_body and ch not in text


def test_values_are_escaped():
    html_body, _ = wt.render(['<b>Maria</b>'], SAMPLE)
    assert "<b>Maria</b>" not in html_body and "&lt;b&gt;Maria&lt;/b&gt;" in html_body


def test_saving_is_private_and_staging_uses_it():
    import rules
    import sample_data
    import staging

    wt.save(SAMPLE)
    try:
        assert wt.TEMPLATE_PATH.stat().st_mode & 0o777 == 0o600
        with sample_data.fixture_config():
            plans = {p.unit: p for p in staging.collect(sample_data.MESSAGES)}
        draft = next(a.payload for a in plans["0735"].actions if a.kind == "welcome_draft")
        assert draft["body"].startswith("Hello Resident,\nWelcome home to The Property.")
        assert "<ul" in draft["html"]
        assert draft["subject"] == "The Property | Welcome Home #0735"
    finally:
        wt.TEMPLATE_PATH.unlink()


def test_without_a_template_the_built_in_text_is_used():
    import sample_data
    import staging

    assert not wt.available()
    with sample_data.fixture_config():
        plans = {p.unit: p for p in staging.collect(sample_data.MESSAGES)}
    draft = next(a.payload for a in plans["0735"].actions if a.kind == "welcome_draft")
    assert "Welcome home!" in draft["body"] and draft["html"] == ""


if __name__ == "__main__":
    import traceback

    tests = [(n, o) for n, o in sorted(globals().items()) if n.startswith("test_") and callable(o)]
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
