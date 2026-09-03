"""
Column-level encryption for Django fields.

Encrypting the whole database file (SQLCipher and friends) protects it at rest
but decrypts everything the moment the app opens it. Encrypting individual
columns means a name stays ciphertext in memory, in query results, in backups
and in any log line that accidentally prints a row -- and only becomes plaintext
where the code explicitly reads that attribute.

That is the stronger position for this app, because the whole risk here is
resident PII leaking somewhere incidental.

THE TRADE-OFF, STATED PLAINLY: you cannot filter, sort, or index on an encrypted
column. Fernet includes a random IV, so the same input encrypts differently every
time and `WHERE first_name = ?` can never match. This is not a limitation to work
around -- it is the property that makes the encryption worth having. Anything the
app needs to query by (unit number, status, dates) is deliberately left in
plaintext, and none of it identifies a person on its own.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken
from django.core.exceptions import ValidationError
from django.db import models

import keystore

# One cipher for the process. Built lazily so that merely importing the models
# does not go and touch the Keychain -- which would prompt the user at import
# time, including during tests that never read an encrypted value.
_cipher: Fernet | None = None


def _get_cipher() -> Fernet:
    global _cipher
    if _cipher is None:
        _cipher = keystore.get_cipher()
    return _cipher


class EncryptedTextField(models.TextField):
    """
    A TextField whose contents are encrypted in the database.

    Plaintext in Python, ciphertext on disk. The conversion happens at the
    field boundary, so model code reads and writes ordinary strings and cannot
    forget to encrypt.
    """

    description = "Text, encrypted at rest with Fernet (AES-128-CBC + HMAC)"

    def get_prep_value(self, value):
        """Python -> database. Called on every save."""
        if value is None:
            return None

        # Normalise to str first; an int unit number or a date would otherwise
        # encrypt to something that decrypts back as the wrong type.
        text = str(value)
        if text == "":
            # Empty stays empty rather than becoming ciphertext-of-nothing, so
            # "no value recorded" is still visibly distinct in the raw table.
            return ""

        return _get_cipher().encrypt(text.encode()).decode()

    def from_db_value(self, value, expression, connection):
        """Database -> Python. Called on every read."""
        if value is None or value == "":
            return value

        try:
            return _get_cipher().decrypt(value.encode()).decode()
        except InvalidToken:
            # Wrong key, or a tampered row. Do not silently return the ciphertext
            # as if it were the value -- that would put garbage into a welcome
            # email addressed to a resident. Fail loudly instead.
            raise ValidationError(
                "Could not decrypt a stored value. The encryption key does not "
                "match the one used to write this database. If the Keychain item "
                "was deleted or the database was copied from another machine, "
                "the contents are unrecoverable."
            )

    def to_python(self, value):
        return value
