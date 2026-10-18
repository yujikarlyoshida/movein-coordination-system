"""
The welcome email from the captured template: used as it is, names filled in.

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
os.environ["MOVEIN_TEMPLATE_PATH"] = str(_TMP / "welcome_template.html")
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

# Shaped like an Outlook template body: layout table, hosted image, two empty
# icon-font blocks, a list, and the placeholder.
SAMPLE = (
    '<div><table style="width:600px"><tr><td align="center">'
    '<img src="https://images.example.invalid/logo.png" alt="logo"></td></tr></table>'
    '<p><span style="font-family: FluentSystemIcons; font-size:20px"></span></p>'
    '<h2 style="text-align:center">Hello (Resident Name),</h2>'
    '<p>Welcome home to The Property.&nbsp;Details below.</p>'
    '<div style="font-family:&quot;FluentSystemIcons&quot;"><span></span></div>'
    '<p><b>Getting started</b></p><ul><li>Connect to the network</li><li>Book an orientation</li></ul>'
    '<p>Questions? <a href="mailto:office@example.invalid">office@example.invalid</a></p>'
    "<p>With gratitude,</p></div>"
)


def test_the_template_is_used_as_it_is_with_names_filled_in():
    out, _ = wt.render(["Maria", "James"], SAMPLE)
    assert "Hello Maria and James," in out and "(Resident Name)" not in out
    expected = (SAMPLE.replace("(Resident Name)", "Maria and James")
                .replace('<p><span style="font-family: FluentSystemIcons; font-size:20px"></span></p>', "<p></p>")
                .replace('<div style="font-family:&quot;FluentSystemIcons&quot;"><span></span></div>', ""))
    assert out == expected      # nothing else changed, byte for byte


def test_icon_font_blocks_and_glyphs_are_removed():
    out, text = wt.render(["Maria"], SAMPLE + "\U0001f600✅")
    assert "fluentsystemicons" not in out.lower()
    for ch in ("", "", "\U0001f600", "✅"):
        assert ch not in out and ch not in text


def test_layout_images_and_links_survive():
    out, _ = wt.render(["Maria"], SAMPLE)
    assert '<table style="width:600px">' in out
    assert '<img src="https://images.example.invalid/logo.png" alt="logo">' in out
    assert '<a href="mailto:office@example.invalid">' in out and "&nbsp;" in out


def test_outlooks_own_signature_is_left_out():
    # A new message already carries the sender's signature; keeping the one
    # captured with the template would sign twice, and a shared profile would
    # sign teammates' emails with the capturer's name.
    signed = SAMPLE.replace("<p>With gratitude,</p></div>",
                            '<p>With gratitude,</p><div id="Signature"><table><tr><td>'
                            "<b>Capturer Name</b><br>Experience Ambassador<br>capturer@example.invalid"
                            '<img src="https://images.example.invalid/sig.png"></td></tr></table></div></div>')
    out, text = wt.render(["Maria"], signed)
    assert "With gratitude," in out
    assert "Capturer Name" not in out and "capturer@example.invalid" not in out and "sig.png" not in out
    assert "Capturer Name" not in text
    # Replies name it x_Signature.
    out, _ = wt.render(["Maria"], signed.replace('id="Signature"', 'id="x_Signature"'))
    assert "Capturer Name" not in out


def test_images_that_only_existed_in_one_outlook_window_are_dropped():
    out, _ = wt.render(["Maria"], SAMPLE + '<p><img src="blob:https://outlook.office.com/1234" width="135"></p>'
                                           '<p><img src="blob:x" /></p>')
    assert "blob:" not in out
    assert '<img src="https://images.example.invalid/logo.png" alt="logo">' in out


def test_names_are_escaped():
    out, _ = wt.render(['<b>Maria</b>'], SAMPLE)
    assert "<b>Maria</b>" not in out and "&lt;b&gt;Maria&lt;/b&gt;" in out


def test_greeting_for_none_one_or_many():
    assert wt.greeting_names([]) == "there"
    assert wt.greeting_names(["Ana"]) == "Ana"
    assert wt.greeting_names(["Ana", "Ben", "Cy"]) == "Ana, Ben and Cy"


def test_a_plain_text_copy_is_derived_from_the_same_html():
    _, text = wt.render(["Maria"], SAMPLE)
    assert text.startswith("Hello Maria,")
    assert "- Connect to the network\n" in text and "- Book an orientation" in text
    assert "<" not in text


def test_saving_is_private_and_staging_uses_it():
    import sample_data
    import staging

    wt.save(SAMPLE)
    try:
        assert Path(wt.TEMPLATE_PATH).stat().st_mode & 0o777 == 0o600
        with sample_data.fixture_config():
            plans = {p.unit: p for p in staging.collect(sample_data.MESSAGES)}
        draft = next(a.payload for a in plans["0735"].actions if a.kind == "welcome_draft")
        assert "Hello Resident," in draft["html"] and '<table style="width:600px">' in draft["html"]
        assert draft["body"].startswith("Hello Resident,")
        assert draft["subject"] == "The Property | Welcome Home #0735"
    finally:
        Path(wt.TEMPLATE_PATH).unlink()


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
