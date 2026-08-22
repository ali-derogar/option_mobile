"""Parse TSETMC API response messages (handles mixed casing)."""

from __future__ import annotations

from typing import Any, Optional, Tuple


def parse_api_error(body: dict[str, Any]) -> Tuple[Optional[int], str]:
    """Extract error code and message from API response body."""
    msg_field = body.get("Msg") or body.get("msg") or body.get("message")
    code: Optional[int] = None
    text = ""

    if isinstance(msg_field, dict):
        code = msg_field.get("Code") or msg_field.get("code")
        text = msg_field.get("Msg") or msg_field.get("msg") or str(msg_field)
    elif msg_field is not None:
        text = str(msg_field)

    if code is not None:
        try:
            code = int(code)
        except (TypeError, ValueError):
            pass

    if not text:
        text = "خطای نامشخص از سرور"

    return code, text


def is_success(body: dict[str, Any]) -> bool:
    return bool(body.get("isSuccess") or body.get("IsSuccess"))

def get_data(body: dict[str, Any]) -> Any:
    return body.get("Data") or body.get("data")
