"""
Which senders count as lease emails, editable from the board.

The list used to live only in config_local.py, which meant editing Python to
add a colleague. It now lives in a private file the board can change:

    ~/Library/Application Support/MoveInTriage/senders.json   (mode 0600)

    {"addresses": ["leasing.manager@example.com"], "names": ["Leasing Lead"]}

On first use the file is seeded from config_local.py when there is one (so an
existing setup carries over); a fresh install starts empty. From then on the
file is the list. `apply()` puts it
into config.TRIGGER_SENDERS / config.TRIGGER_SENDER_NAMES, which is all the rest
of the app reads.

Colleagues' addresses and names are not resident data, but they are still
nobody else's business: the file is outside the repository, owner-only, and
never logged. MOVEIN_SENDERS_PATH points tests (and demo runs) elsewhere.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

import config

APP_SUPPORT = Path.home() / "Library" / "Application Support" / "MoveInTriage"
SENDERS_PATH = APP_SUPPORT / "senders.json"
_ENV_VAR = "MOVEIN_SENDERS_PATH"

_ADDRESS = re.compile(r"^[^\s@<>,;]+@[^\s@<>,;]+\.[A-Za-z]{2,}$")
# Two to four words of letters, as Outlook shows a person: "Leasing Lead",
# "Lead, Leasing", "Mary-Kate O'Neil".
_NAME = re.compile(r"^[A-Za-zÀ-ɏ][\w'.\-À-ɏ]*(?:,? [A-Za-zÀ-ɏ][\w'.\-À-ɏ]*){1,3}$")


def path() -> Path:
    return Path(os.environ.get(_ENV_VAR, str(SENDERS_PATH)))


def use_throwaway() -> None:
    """Demo runs: a scratch file, so the demo never touches the real list."""
    if path() == SENDERS_PATH:
        os.environ[_ENV_VAR] = os.path.join(tempfile.mkdtemp(prefix="movein-demo-"), "senders.json")


def _clean(values) -> list[str]:
    out, seen = [], set()
    for v in values or []:
        v = " ".join(str(v).split())
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return out


def load() -> dict[str, list[str]]:
    """The list, seeding the file from config the first time."""
    p = path()
    if not p.exists():
        # Only a real local config is carried over. A fresh install's config
        # holds placeholder example.com addresses, which would show up as
        # senders nobody added.
        seed = getattr(config, "HAS_LOCAL_CONFIG", True)
        data = {"addresses": _clean(config.TRIGGER_SENDERS) if seed else [],
                "names": _clean(getattr(config, "TRIGGER_SENDER_NAMES", [])) if seed else []}
        save(data)
        return data
    with open(p, encoding="utf-8") as fh:
        raw = json.load(fh)
    return {"addresses": _clean(raw.get("addresses")), "names": _clean(raw.get("names"))}


def save(data: dict[str, list[str]]) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # Create owner-only before writing, so the list is never readable by others.
    fd = os.open(p, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"addresses": _clean(data.get("addresses")),
                   "names": _clean(data.get("names"))}, fh, indent=2)
    os.chmod(p, 0o600)


def apply(data: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
    """Make the app use this list. Returns what was applied."""
    data = data if data is not None else load()
    config.TRIGGER_SENDERS = list(data["addresses"])
    config.TRIGGER_SENDER_NAMES = list(data["names"])
    return data


def add(entry: str) -> tuple[bool, str]:
    """
    Add an address or a display name. Returns (changed, message for the board).

    Messages describe the entry by kind, never repeat it: the board's notice
    line is not the place to echo colleagues' details back.
    """
    entry = " ".join((entry or "").split())
    data = load()
    if _ADDRESS.match(entry):
        key, kind = "addresses", "address"
        entry = entry.lower()
    elif _NAME.match(entry):
        key, kind = "names", "name"
    else:
        return False, "Enter an email address, or a full name as Outlook shows it."
    if entry.lower() in {v.lower() for v in data[key]}:
        return False, f"That {kind} is already on the list."
    data[key].append(entry)
    save(data)
    apply(data)
    return True, f"Added the {kind}. Checking again now."


def remove(entry: str) -> tuple[bool, str]:
    """Take an address or name off the list. Returns (changed, message)."""
    entry = " ".join((entry or "").split()).lower()
    data = load()
    for key, kind in (("addresses", "address"), ("names", "name")):
        kept = [v for v in data[key] if v.lower() != entry]
        if len(kept) != len(data[key]):
            data[key] = kept
            save(data)
            apply(data)
            return True, f"Removed the {kind}. Checking again now."
    return False, "That sender wasn't on the list."
