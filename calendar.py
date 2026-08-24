"""Compatibility wrapper for Python's stdlib calendar module.

This project historically had a root-level ``calendar.py`` helper. Because the
file name shadows Python's own ``calendar`` module, imports from the standard
library can land here first. Keep this module safe to import by loading and
re-exporting stdlib calendar without importing optional web frameworks.
"""

from __future__ import annotations

import importlib.util
import sys
import sysconfig
from datetime import datetime, timezone
from typing import Any


_stdlib_calendar = None
_stdlib_calendar_path = f"{sysconfig.get_path('stdlib')}/calendar.py"
_CALENDAR_DATA_EXPORTS = {
    "day_info_from_calendar",
    "fetch_calendar_month",
    "fetch_calendar_today",
    "format_calendar_events",
    "gregorian_to_jalali",
    "is_jalali_leap_year",
    "jalali_to_gregorian",
    "jalali_month_length",
    "shift_jalali_month",
    "today_from_calendar",
    "valid_jalali_date",
}
_stdlib_calendar_spec = importlib.util.spec_from_file_location(
    "_stdlib_calendar",
    _stdlib_calendar_path,
)
if _stdlib_calendar_spec and _stdlib_calendar_spec.loader:
    _stdlib_calendar = importlib.util.module_from_spec(_stdlib_calendar_spec)
    sys.modules[_stdlib_calendar_spec.name] = _stdlib_calendar
    _stdlib_calendar_spec.loader.exec_module(_stdlib_calendar)
    for _name in dir(_stdlib_calendar):
        if _name.startswith("__") and _name not in {"__all__", "__doc__"}:
            continue
        globals().setdefault(_name, getattr(_stdlib_calendar, _name))


def timegm(tuple_value: tuple[int, ...]) -> int:
    if _stdlib_calendar and hasattr(_stdlib_calendar, "timegm"):
        return _stdlib_calendar.timegm(tuple_value)
    return int(datetime(*tuple_value[:6], tzinfo=timezone.utc).timestamp())


def __getattr__(name: str) -> Any:
    if name in _CALENDAR_DATA_EXPORTS:
        try:
            from options.backend import calendar_data
        except ImportError as exc:
            raise AttributeError(f"module 'calendar' has no attribute {name!r}") from exc
        value = getattr(calendar_data, name)
        globals()[name] = value
        return value
    if _stdlib_calendar and hasattr(_stdlib_calendar, name):
        return getattr(_stdlib_calendar, name)
    raise AttributeError(f"module 'calendar' has no attribute {name!r}")


try:
    from options.backend.calendar_data import (
        day_info_from_calendar,
        fetch_calendar_month,
        fetch_calendar_today,
        format_calendar_events,
        gregorian_to_jalali,
        is_jalali_leap_year,
        jalali_to_gregorian,
        jalali_month_length,
        shift_jalali_month,
        today_from_calendar,
        valid_jalali_date,
    )
except ImportError:
    # Keep stdlib compatibility even when the package is not on PYTHONPATH.
    pass
