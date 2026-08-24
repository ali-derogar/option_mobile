"""Short-lived activation code validation."""

from __future__ import annotations

import base64
import time

OFFSET = 739184


def encode(timestamp: int) -> str:
    """Timestamp -> public code."""
    value = int(timestamp) + OFFSET
    raw = str(value).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode(code: str) -> int:
    """Public code -> timestamp."""
    if not isinstance(code, str):
        raise TypeError("code must be a string")
    padding = "=" * (-len(code) % 4)
    raw = base64.urlsafe_b64decode(code.strip() + padding)
    value = int(raw.decode())
    return value - OFFSET


def is_valid(code: str, window: int = 3600) -> bool:
    """Return true when code timestamp is within ±window seconds."""
    try:
        timestamp = decode(code)
        now = int(time.time())
        return abs(now - timestamp) <= window
    except Exception:
        return False
