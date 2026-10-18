"""
Who is using the app, and for which property -- set once, editable later.

Everything that used to be hard-coded in config_local.py for one person lives
here instead, so anyone can install the app and make it theirs:

    ~/Library/Application Support/MoveInTriage/settings.json   (mode 0600)

    {"you":      {"name": "...", "email": "..."},
     "property": {"name": "...", "signature": "...", "welcome_cc": "...",
                  "folder": "Move ins"},
     "setup_complete": true}

"you" is the person at this Mac. "property" is what everyone at one property
shares, and is what a profile carries (see export_profile / import_profile):
one person sets the property up, exports a small file, and teammates import it
on the welcome page instead of typing it all again.

On first use the file is seeded from config, so an existing config_local.py
carries over. `apply()` puts the values into config, which is all the rest of
the app reads. No resident data is ever stored here, and no passwords: signing
in happens in the app's browser, as the person themselves.

MOVEIN_SETTINGS_PATH points tests and demo runs at a scratch file.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

import config

APP_SUPPORT = Path.home() / "Library" / "Application Support" / "MoveInTriage"
SETTINGS_PATH = APP_SUPPORT / "settings.json"
_ENV_VAR = "MOVEIN_SETTINGS_PATH"

PROFILE_KIND = "movein-triage-profile"
PROFILE_VERSION = 1
DEFAULT_FOLDER = "Move ins"

_ADDRESS = re.compile(r"^[^\s@<>,;]+@[^\s@<>,;]+\.[A-Za-z]{2,}$")
_MAX = 120                 # per text field
_MAX_TEMPLATE = 500_000    # a captured Outlook template is ~50 KB


def path() -> Path:
    return Path(os.environ.get(_ENV_VAR, str(SETTINGS_PATH)))


def use_throwaway() -> None:
    """Demo runs: a scratch file, so the demo never touches the real settings."""
    if path() == SETTINGS_PATH:
        os.environ[_ENV_VAR] = os.path.join(tempfile.mkdtemp(prefix="movein-demo-"), "settings.json")


def _text(value) -> str:
    """One line of plain text: whitespace collapsed, control characters gone."""
    text = " ".join(str(value or "").split())
    return "".join(ch for ch in text if ch.isprintable())[:_MAX]


def _email(value) -> str:
    text = _text(value).lower()
    return text if _ADDRESS.match(text) else ""


def _defaults() -> dict:
    """What config says today -- an existing config_local.py carries over."""
    return {
        "you": {"name": "", "email": ""},
        "property": {
            "name": _text(getattr(config, "PROPERTY_NAME", "")),
            "signature": _text(getattr(config, "SIGNATURE", "")),
            "welcome_cc": _email(getattr(config, "WELCOME_CC", "")),
            "folder": _text(getattr(config, "MAIL_FOLDER", DEFAULT_FOLDER)) or DEFAULT_FOLDER,
        },
        # Someone already running from a config_local.py is set up; a fresh
        # install, still on the placeholder property name, is not.
        "setup_complete": _text(getattr(config, "PROPERTY_NAME", "")) not in ("", "The Property"),
    }


def _clean(raw: dict) -> dict:
    you = raw.get("you") or {}
    prop = raw.get("property") or {}
    return {
        "you": {"name": _text(you.get("name")), "email": _email(you.get("email"))},
        "property": {
            "name": _text(prop.get("name")),
            "signature": _text(prop.get("signature")),
            "welcome_cc": _email(prop.get("welcome_cc")),
            "folder": _text(prop.get("folder")) or DEFAULT_FOLDER,
        },
        "setup_complete": bool(raw.get("setup_complete")),
    }


def load() -> dict:
    """The settings, seeding the file from config the first time."""
    p = path()
    if not p.exists():
        data = _defaults()
        save(data)
        return data
    with open(p, encoding="utf-8") as fh:
        return _clean(json.load(fh))


def save(data: dict) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # Owner-only from the moment it exists.
    fd = os.open(p, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(_clean(data), fh, indent=2)
    os.chmod(p, 0o600)


def apply(data: dict | None = None) -> dict:
    """Make the app use these settings. Returns what was applied."""
    data = data if data is not None else load()
    prop = data["property"]
    if prop["name"]:
        config.PROPERTY_NAME = prop["name"]
    if prop["signature"]:
        config.SIGNATURE = prop["signature"]
    config.WELCOME_CC = prop["welcome_cc"]
    config.MAIL_FOLDER = prop["folder"]
    return data


def needs_setup(data: dict | None = None) -> bool:
    """True until the welcome page has been completed once."""
    data = data if data is not None else load()
    return not data["setup_complete"]


def update(section: str, values: dict) -> tuple[dict, list[str]]:
    """
    Change some fields of "you" or "property". Returns (settings, problems):
    a field that does not pass its check is left as it was and named in
    problems, never half-saved.
    """
    if section not in ("you", "property"):
        raise ValueError(f"unknown settings section: {section}")
    data = load()
    problems = []
    for key, value in values.items():
        if key not in data[section]:
            continue
        if key in ("email", "welcome_cc"):
            cleaned = _email(value)
            if _text(value) and not cleaned:
                problems.append(f"{key.replace('_', ' ')} is not an email address")
                continue
        else:
            cleaned = _text(value)
        data[section][key] = cleaned
    if section == "property" and not data["property"]["folder"]:
        data["property"]["folder"] = DEFAULT_FOLDER
    save(data)
    apply(data)
    return data, problems


def mark_complete() -> dict:
    data = load()
    data["setup_complete"] = True
    save(data)
    return data


# ---------------------------------------------------------------------------
# Property profiles: share a property's setup with teammates
# ---------------------------------------------------------------------------

def export_profile(include_template: bool = True) -> dict:
    """
    What everyone at this property shares: property settings, the lease
    senders, and (optionally) the captured welcome template. Never "you" --
    each person signs in and names themselves.
    """
    import senders
    import welcome_template

    data = load()
    profile = {
        "kind": PROFILE_KIND,
        "version": PROFILE_VERSION,
        "property": dict(data["property"]),
        "senders": senders.load(),
    }
    if include_template and welcome_template.available():
        profile["welcome_template_html"] = welcome_template.load()
    return profile


def write_profile(target: Path, include_template: bool = True) -> Path:
    """Save the profile as a file to hand to a teammate."""
    target = Path(target)
    target.write_text(json.dumps(export_profile(include_template), indent=2), encoding="utf-8")
    return target


def import_profile(profile: dict) -> tuple[bool, str]:
    """
    Take on a teammate's property setup. Returns (ok, message for the page).

    Checked before anything changes: wrong kind, newer version, or bad values
    reject the whole file, so a profile is applied completely or not at all.
    """
    import senders
    import welcome_template

    if not isinstance(profile, dict) or profile.get("kind") != PROFILE_KIND:
        return False, "That file isn't a Move-in triage profile."
    if not isinstance(profile.get("version"), int) or profile["version"] > PROFILE_VERSION:
        return False, "That profile is from a newer version of the app. Update the app first."
    prop = profile.get("property")
    if not isinstance(prop, dict):
        return False, "That profile has no property settings."
    if _text(prop.get("welcome_cc")) and not _email(prop.get("welcome_cc")):
        return False, "The profile's Cc address isn't an email address."
    roster = profile.get("senders") or {}
    if not isinstance(roster, dict) or not all(
            isinstance(roster.get(k, []), list) for k in ("addresses", "names")):
        return False, "The profile's sender list is not readable."
    template = profile.get("welcome_template_html", "")
    if template and (not isinstance(template, str) or len(template) > _MAX_TEMPLATE):
        return False, "The profile's welcome template is not readable."

    data = load()
    data["property"] = _clean({"property": prop})["property"]
    save(data)
    apply(data)
    senders.save({"addresses": [a.lower() for a in roster.get("addresses", []) if _ADDRESS.match(str(a).strip())],
                  "names": roster.get("names", [])})
    senders.apply()
    if template:
        welcome_template.save(template)
    return True, "Profile imported." + (" Welcome template included." if template else "")


def read_profile(source: Path) -> tuple[bool, str]:
    try:
        profile = json.loads(Path(source).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, "That file couldn't be read as a profile."
    return import_profile(profile)
