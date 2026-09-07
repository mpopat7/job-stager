"""Encryption for the parts of a profile that are nobody else's business.

Voluntary EEO self-identification -- gender, race, veteran and disability status -- is
the most sensitive thing this application stores and the least useful to an attacker's
victim. It is encrypted with a key held outside the database, so a stolen dump is not a
list of who is disabled.

`JOBSTAGER_SECRET_KEY` holds a urlsafe-base64 32-byte Fernet key. A personal install
without one keeps working and stores the block in the clear, which is the same exposure
as the SQLite file already sitting on that person's own disk. A shared deployment must
set it, and refuses to start otherwise.
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

ENV_KEY = "JOBSTAGER_SECRET_KEY"
# Marks a value this module wrote, so a profile saved before a key existed still reads.
PREFIX = "enc:v1:"


def generate_key() -> str:
    """A new key, for an operator to put in the environment and keep."""
    return Fernet.generate_key().decode()


def _cipher() -> Optional[Fernet]:
    raw = os.getenv(ENV_KEY, "").strip()
    if not raw:
        return None
    try:
        return Fernet(raw.encode())
    except (ValueError, TypeError) as err:
        raise RuntimeError(
            f"{ENV_KEY} is not a valid Fernet key. Generate one with "
            "`python3 -c \"from core.store.crypto import generate_key; print(generate_key())\"`."
        ) from err


def have_key() -> bool:
    return bool(os.getenv(ENV_KEY, "").strip())


def encrypt(value: str) -> str:
    cipher = _cipher()
    if cipher is None or not value:
        return value
    return PREFIX + base64.urlsafe_b64encode(cipher.encrypt(value.encode())).decode()


def decrypt(value: str) -> str:
    if not isinstance(value, str) or not value.startswith(PREFIX):
        return value
    cipher = _cipher()
    if cipher is None:
        # The row was written under a key this process does not have. Returning the
        # ciphertext would put it in a job application, so return nothing instead.
        logger.error(f"Encrypted value present but {ENV_KEY} is unset; treating as empty.")
        return ""
    try:
        return cipher.decrypt(base64.urlsafe_b64decode(value[len(PREFIX):])).decode()
    except (InvalidToken, ValueError) as err:
        logger.error(f"Could not decrypt a stored value ({err}); treating as empty.")
        return ""


def _walk(payload: Dict[str, Any], path: str):
    """The container and final key for a dotted path, or None if it is absent."""
    parts = path.split(".")
    node: Any = payload
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return None, None
        node = node[part]
    if not isinstance(node, dict) or parts[-1] not in node:
        return None, None
    return node, parts[-1]


def protect(payload: Dict[str, Any], paths) -> Dict[str, Any]:
    """Encrypt the named string fields in a profile dict, in place."""
    for path in paths:
        node, key = _walk(payload, path)
        if node is not None and isinstance(node[key], str) and node[key]:
            node[key] = encrypt(node[key])
    return payload


def unprotect(payload: Dict[str, Any], paths) -> Dict[str, Any]:
    """Decrypt the named string fields in a profile dict, in place."""
    for path in paths:
        node, key = _walk(payload, path)
        if node is not None and isinstance(node[key], str):
            node[key] = decrypt(node[key])
    return payload
