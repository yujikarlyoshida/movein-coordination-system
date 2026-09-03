"""
Encryption key management.

The database holds resident personal data, so it is encrypted at rest. That is
only worth anything if the key is kept somewhere better than next to the data --
an encrypted file with the key sitting beside it is a locked door with the key in
the lock.

So the key lives in the macOS Keychain, which is itself encrypted, tied to the
login password, and not readable by another user account on the same machine.
The database file is useless without it.

Order of preference:

  1. MOVEIN_DB_KEY environment variable  -- for CI and tests, never for real use
  2. macOS Keychain                      -- the real path
  3. A 0600 file in Application Support  -- fallback for non-macOS development

The fallback is deliberately noisy: it prints a warning, because storing the key
on the same disk as the database is a meaningfully weaker position and nobody
should end up there without knowing.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from cryptography.fernet import Fernet

# Keychain coordinates. The service/account pair is how `security` finds the item
# again; changing either orphans the existing key and the database with it.
KEYCHAIN_SERVICE = "MoveInTriage"
KEYCHAIN_ACCOUNT = "database-encryption-key"

APP_SUPPORT = Path.home() / "Library" / "Application Support" / "MoveInTriage"
FALLBACK_KEY_FILE = APP_SUPPORT / "db.key"

_ENV_VAR = "MOVEIN_DB_KEY"


def _is_macos() -> bool:
    return sys.platform == "darwin"


# ---------------------------------------------------------------------------
# Keychain
# ---------------------------------------------------------------------------

def _keychain_read() -> str | None:
    """Return the stored key, or None if there isn't one yet."""
    try:
        result = subprocess.run(
            [
                "security", "find-generic-password",
                "-s", KEYCHAIN_SERVICE,
                "-a", KEYCHAIN_ACCOUNT,
                "-w",                      # print the password only
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    # Exit code 44 is "item not found", which is a normal first run, not an error.
    if result.returncode != 0:
        return None

    return result.stdout.strip() or None


def _keychain_write(key: str) -> bool:
    """Store the key. Returns True on success."""
    try:
        result = subprocess.run(
            [
                "security", "add-generic-password",
                "-s", KEYCHAIN_SERVICE,
                "-a", KEYCHAIN_ACCOUNT,
                "-w", key,
                "-U",                      # update if it already exists
                "-T", "",                  # no app is pre-trusted; prompt instead
                "-D", "MoveInTriage database encryption key",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False

    return result.returncode == 0


# ---------------------------------------------------------------------------
# File fallback
# ---------------------------------------------------------------------------

def _file_read() -> str | None:
    if not FALLBACK_KEY_FILE.exists():
        return None
    return FALLBACK_KEY_FILE.read_text().strip() or None


def _file_write(key: str) -> None:
    APP_SUPPORT.mkdir(parents=True, exist_ok=True)

    # Create with restrictive permissions from the outset rather than writing
    # first and chmod-ing after -- that leaves a window where the key is
    # world-readable, which is exactly the kind of gap that is never noticed.
    fd = os.open(FALLBACK_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(key)

    print(
        f"WARNING: encryption key written to {FALLBACK_KEY_FILE} because the "
        f"macOS Keychain was unavailable. The key now sits on the same disk as "
        f"the database it protects.",
        file=sys.stderr,
    )


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------

def get_or_create_key() -> bytes:
    """
    Return the Fernet key, generating and storing one on first run.

    Always returns the *same* key for a given machine and user. Losing it means
    losing the database contents -- there is no recovery path, by design.
    """
    from_env = os.environ.get(_ENV_VAR)
    if from_env:
        return from_env.encode()

    if _is_macos():
        existing = _keychain_read()
        if existing:
            return existing.encode()

    existing = _file_read()
    if existing:
        return existing.encode()

    # First run for this user: mint one.
    key = Fernet.generate_key().decode()

    if _is_macos() and _keychain_write(key):
        return key.encode()

    _file_write(key)
    return key.encode()


def get_cipher() -> Fernet:
    """A Fernet instance built on this machine's key."""
    return Fernet(get_or_create_key())
