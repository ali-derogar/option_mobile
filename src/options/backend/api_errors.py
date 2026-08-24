"""Parse TSETMC API response messages (handles mixed casing)."""

from __future__ import annotations

from typing import Any, Optional, Tuple


def parse_api_error(body: dict[str, Any]) -> Tuple[Optional[int], str]:
    """Extract error code and message from API response body."""
    msg_field = _first_present(body, "Msg", "msg", "message")
    code: Optional[int] = None
    text = ""

    if isinstance(msg_field, dict):
        code = _first_present(msg_field, "Code", "code")
        msg_text = _first_present(msg_field, "Msg", "msg")
        text = str(msg_text) if msg_text is not None else str(msg_field)
    elif msg_field is not None:
        text = str(msg_field)

    if code is not None:
        try:
            code = int(code)
        except (OverflowError, TypeError, ValueError):
            code = None

    if not text:
        text = "خطای نامشخص از سرور"

    return code, text


def is_success(body: dict[str, Any]) -> bool:
    value = _first_present(body, "isSuccess", "IsSuccess")
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)

def get_data(body: dict[str, Any]) -> Any:
    return _first_present(body, "Data", "data")


def _first_present(body: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in body:
            return body[key]
    return None
