"""
Settings and property profiles: who is using the app, and for which property.

Run with:  python -m pytest test_settings.py -v
       or: python test_settings.py

Every file lives in a scratch folder; the real settings, sender list and
template are never read. All values fictional.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="movein-test-"))
# Scratch files for this suite only. Set at import (so nothing here can reach a
# real file) and again in setup_module, because other suites in the same run
# set these at their own import; restored in teardown_module.
_PATHS = {
    "MOVEIN_SETTINGS_PATH": str(_TMP / "settings.json"),
    "MOVEIN_SENDERS_PATH": str(_TMP / "senders.json"),
    "MOVEIN_TEMPLATE_PATH": str(_TMP / "welcome_template.html"),
}
os.environ.update(_PATHS)
os.environ.setdefault("MOVEIN_HISTORY_PATH", str(_TMP / "history.sqlite3"))

import config  # noqa: E402


import senders  # noqa: E402
import settings  # noqa: E402
import welcome_template  # noqa: E402

# Other suites in the same run pin their own config at import. Put back what
# these tests change, so nothing leaks into them.
_KEYS = ("TRIGGER_SENDERS", "TRIGGER_SENDER_NAMES", "PROPERTY_NAME", "SIGNATURE", "WELCOME_CC", "MAIL_FOLDER")
_SAVED: dict = {}
_ENV: dict = {}


def setup_module(module=None):
    # Snapshot when this suite starts, after every suite has been imported.
    _SAVED.update({k: getattr(config, k, None) for k in _KEYS})
    _ENV.update({k: os.environ.get(k) for k in _PATHS})
    os.environ.update(_PATHS)
    # Pinned only while this suite runs, so import order can't leak them.
    config.TRIGGER_SENDERS = ["leasing.manager@example.com"]
    config.TRIGGER_SENDER_NAMES = ["Leasing Lead"]
    config.PROPERTY_NAME = "The Property"
    config.SIGNATURE = "The Experience Team"
    config.WELCOME_CC = ""


def teardown_module(module=None):
    for key, value in _SAVED.items():
        setattr(config, key, value)
    for key, value in _ENV.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _fresh(**config_values):
    """A first launch: no files yet, config as given."""
    for p in (settings.path(), senders.path(), Path(welcome_template.TEMPLATE_PATH)):
        if Path(p).exists():
            Path(p).unlink()
    for key, value in {"PROPERTY_NAME": "The Property", "SIGNATURE": "The Experience Team",
                       "WELCOME_CC": "", "MAIL_FOLDER": "Move ins", **config_values}.items():
        setattr(config, key, value)


def test_a_fresh_install_needs_the_welcome_page():
    _fresh()
    data = settings.load()
    assert settings.needs_setup(data)
    assert data["you"] == {"name": "", "email": ""}
    assert data["property"]["folder"] == "Move ins"


def test_an_existing_local_config_carries_over_and_counts_as_set_up():
    # Someone already running from config_local.py is not sent through setup.
    _fresh(PROPERTY_NAME="Example Commons", WELCOME_CC="Team@Example.com")
    data = settings.load()
    assert not settings.needs_setup(data)
    assert data["property"]["name"] == "Example Commons"
    assert data["property"]["welcome_cc"] == "team@example.com"


def test_the_file_is_private():
    _fresh()
    settings.load()
    assert settings.path().stat().st_mode & 0o777 == 0o600


def test_apply_is_what_the_rest_of_the_app_reads():
    _fresh()
    settings.update("property", {"name": "Example Commons", "signature": "The Commons Team",
                                 "welcome_cc": "team@example.com", "folder": "New leases"})
    assert config.PROPERTY_NAME == "Example Commons"
    assert config.SIGNATURE == "The Commons Team"
    assert config.WELCOME_CC == "team@example.com"
    assert config.MAIL_FOLDER == "New leases"


def test_a_bad_email_is_refused_and_the_old_value_kept():
    _fresh()
    settings.update("you", {"email": "me@example.com"})
    data, problems = settings.update("you", {"email": "not an address", "name": "Jordan Lee"})
    assert data["you"]["email"] == "me@example.com"
    assert data["you"]["name"] == "Jordan Lee"
    assert problems == ["email is not an email address"]


def test_values_are_one_line_of_plain_text():
    _fresh()
    data, _ = settings.update("property", {"name": "Example\n\tCommons\x00" + "x" * 500, "folder": "  "})
    assert data["property"]["name"].startswith("Example Commons")
    assert len(data["property"]["name"]) <= 120 and "\x00" not in data["property"]["name"]
    assert data["property"]["folder"] == "Move ins"   # blank falls back to the default


def test_the_reader_watches_the_folder_from_settings():
    import outlook_web

    _fresh()
    settings.update("property", {"folder": "New leases"})
    assert outlook_web.OutlookWebReader(None).folder == "New leases"
    assert outlook_web.OutlookWebReader(None, folder="Other").folder == "Other"


# ---------------------------------------------------------------------------
# profiles
# ---------------------------------------------------------------------------

TEMPLATE = "<p>Hello (Resident Name), welcome to Example Commons.</p>"


def _set_up_property():
    _fresh()
    settings.update("you", {"name": "Jordan Lee", "email": "jordan@example.com"})
    settings.update("property", {"name": "Example Commons", "signature": "The Commons Team",
                                 "welcome_cc": "team@example.com", "folder": "Move ins"})
    senders.save({"addresses": ["leasing.desk@example.com"], "names": ["Leasing Lead"]})
    welcome_template.save(TEMPLATE)


def test_a_profile_carries_the_property_but_never_you():
    _set_up_property()
    profile = settings.export_profile()
    assert profile["kind"] == "housewarm-profile" and profile["version"] == 1
    assert profile["property"]["name"] == "Example Commons"
    assert profile["senders"] == {"addresses": ["leasing.desk@example.com"], "names": ["Leasing Lead"]}
    assert profile["welcome_template_html"] == TEMPLATE
    text = json.dumps(profile)
    assert "jordan" not in text.lower() and "you" not in profile


def test_a_teammate_imports_the_profile_and_keeps_their_own_name():
    _set_up_property()
    file = settings.write_profile(_TMP / "commons.json")

    # The teammate's Mac: a fresh install with their own name.
    _fresh()
    settings.update("you", {"name": "Sam Rivera", "email": "sam@example.com"})
    ok, message = settings.read_profile(file)
    assert ok, message
    data = settings.load()
    assert data["you"] == {"name": "Sam Rivera", "email": "sam@example.com"}
    assert data["property"]["welcome_cc"] == "team@example.com"
    assert senders.load()["addresses"] == ["leasing.desk@example.com"]
    assert config.TRIGGER_SENDER_NAMES == ["Leasing Lead"]
    assert welcome_template.load() == TEMPLATE
    assert "Welcome template included" in message


def test_a_bad_profile_changes_nothing():
    _set_up_property()
    before = (settings.path().read_text(), senders.path().read_text())
    good = settings.export_profile()
    bad = [
        {"kind": "something-else"},
        dict(good, version=99),
        dict(good, property=dict(good["property"], welcome_cc="nope")),
        dict(good, senders={"addresses": "leasing.desk@example.com"}),
        dict(good, welcome_template_html=123),
    ]
    for profile in bad:
        ok, message = settings.import_profile(profile)
        assert not ok and message
    assert (settings.path().read_text(), senders.path().read_text()) == before
    ok, _ = settings.read_profile(_TMP / "does-not-exist.json")
    assert not ok


def test_a_profile_saved_before_the_rename_still_imports():
    _set_up_property()
    profile = dict(settings.export_profile(include_template=False), kind="movein-triage-profile")
    ok, message = settings.import_profile(profile)
    assert ok, message


def test_the_logo_is_a_private_png_and_travels_in_profiles():
    _set_up_property()
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    assert settings.save_logo(b"GIF89a") == (False, "Choose a PNG image for the logo.")
    assert not settings.has_logo()
    ok, _ = settings.save_logo(png)
    assert ok and settings.has_logo()
    assert settings.logo_path().stat().st_mode & 0o777 == 0o600
    profile = settings.export_profile(include_template=False)
    assert profile["logo_png_base64"]
    settings.remove_logo()
    assert not settings.has_logo()
    ok, message = settings.import_profile(profile)
    assert ok and "Logo included" in message and settings.logo_path().read_bytes() == png
    bad = dict(profile, logo_png_base64="not base64!")
    assert settings.import_profile(bad)[0] is False
    settings.remove_logo()


def test_profile_sender_addresses_are_checked():
    _set_up_property()
    profile = settings.export_profile(include_template=False)
    profile["senders"] = {"addresses": ["good@example.com", "not-an-address"], "names": ["Leasing Lead"]}
    ok, _ = settings.import_profile(profile)
    assert ok
    assert senders.load()["addresses"] == ["good@example.com"]


if __name__ == "__main__":
    import traceback

    setup_module()

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


def test_the_xfinity_residents_page_is_remembered_and_nothing_else():
    assert settings.remember_xfinity_residents("/properties/P1234/residents/")
    assert settings.xfinity_residents() == "/properties/P1234/residents"
    # Not a residents page, or anything carrying more than an address path: refused.
    for bad in ("/organizations/9/properties", "/properties/P1/residents/create",
                "https://evil.example/properties/P1/residents", "/properties/P1/residents?q=x"):
        assert not settings.remember_xfinity_residents(bad), bad
    assert settings.xfinity_residents() == "/properties/P1234/residents"
