try:
    from fastapi import APIRouter, HTTPException
except ImportError:  # The main app uses Starlette and can still import the helpers below.
    APIRouter = None
    HTTPException = Exception
import importlib.util
import json
import sys
import sysconfig
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

router = APIRouter() if APIRouter else None
_stdlib_calendar = None
_stdlib_calendar_path = f"{sysconfig.get_path('stdlib')}/calendar.py"
_stdlib_calendar_spec = importlib.util.spec_from_file_location("_stdlib_calendar", _stdlib_calendar_path)
if _stdlib_calendar_spec and _stdlib_calendar_spec.loader:
    _stdlib_calendar = importlib.util.module_from_spec(_stdlib_calendar_spec)
    sys.modules[_stdlib_calendar_spec.name] = _stdlib_calendar
    _stdlib_calendar_spec.loader.exec_module(_stdlib_calendar)
    for _name in dir(_stdlib_calendar):
        if _name.startswith("__") and _name not in {"__doc__", "__all__"}:
            continue
        globals().setdefault(_name, getattr(_stdlib_calendar, _name))


def _api_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def _api_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(str(value).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")))
    except (TypeError, ValueError):
        return default


def is_jalali_leap_year(year: int) -> bool:
    breaks = [-61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181, 1210, 1635, 2060, 2097, 2192, 2262, 2324, 2394, 2456, 3178]
    leap_j = -14
    jp = breaks[0]
    jump = 0
    for jm in breaks[1:]:
        jump = jm - jp
        if year < jm:
            break
        leap_j += (jump // 33) * 8 + (jump % 33) // 4
        jp = jm
    n = year - jp
    leap_j += (n // 33) * 8 + ((n % 33) + 3) // 4
    if jump % 33 == 4 and jump - n == 4:
        leap_j += 1
    if jump - n < 6:
        n = n - jump + ((jump + 4) // 33) * 33
    leap = ((n + 1) % 33 - 1) % 4
    if leap == -1:
        leap = 4
    return leap == 0


def jalali_month_length(year: int, month: int) -> int:
    if month <= 6:
        return 31
    if month <= 11:
        return 30
    return 30 if is_jalali_leap_year(year) else 29


def valid_jalali_date(year: int, month: int, day: int) -> bool:
    return 1 <= month <= 12 and 1 <= day <= jalali_month_length(year, month)


def timegm(tuple_value):
    """Compatibility shim because this file shadows Python's stdlib calendar module."""
    if _stdlib_calendar and hasattr(_stdlib_calendar, "timegm"):
        return _stdlib_calendar.timegm(tuple_value)
    return int(datetime(*tuple_value[:6], tzinfo=timezone.utc).timestamp())


def __getattr__(name: str) -> Any:
    if _stdlib_calendar and hasattr(_stdlib_calendar, name):
        return getattr(_stdlib_calendar, name)
    raise AttributeError(f"module 'calendar' has no attribute {name!r}")


def fetch_calendar_month(year: int, month: int, day: int = 0, base1: int = 0, base2: int = 1, base3: int = 2) -> Dict[str, Any]:
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen

    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:146.0) Gecko/20100101 Firefox/146.0',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'en-US,en;q=0.5',
        'x-api-key': 'ZAVdqwuySASubByCed5KYuYMzb9uB2f7',
        'Origin': 'https://www.time.ir',
        'Connection': 'keep-alive',
        'Referer': 'https://www.time.ir/',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-site',
        'Priority': 'u=0',
    }

    params = {
        'year': str(year),
        'month': str(month),
        'day': str(day),
        'base1': str(base1),
        'base2': str(base2),
        'base3': str(base3),
    }

    url = f"https://api.time.ir/v1/event/fa/events/calendar?{urlencode(params)}"
    request = Request(url, headers=headers)
    with urlopen(request, timeout=10.0) as response:
        payload = response.read().decode("utf-8")
    return json.loads(payload)


def format_calendar_events(calendar_data: Dict[str, Any], year: int, month: int, day: int = 0) -> Dict[str, Any]:
    data = calendar_data.get('data') if isinstance(calendar_data, dict) else None
    if not isinstance(data, dict):
        return {
            'year': year,
            'month': month,
            'day': day,
            'events': [],
            'holidays': [],
            'days': [],
            'total_events': 0,
            'total_holidays': 0,
        }

    events = []
    holidays = []
    event_list = data.get('event_list', [])

    for event in event_list:
        if not isinstance(event, dict):
            continue
        jalali_year = _api_int(event.get('jalali_year'), year)
        jalali_month = _api_int(event.get('jalali_month'), month)
        jalali_day = _api_int(event.get('jalali_day'))
        gregorian_year = _api_int(event.get('gregorian_year'))
        gregorian_month = _api_int(event.get('gregorian_month'))
        gregorian_day = _api_int(event.get('gregorian_day'))
        hijri_year = _api_int(event.get('hijri_year'))
        hijri_month = _api_int(event.get('hijri_month'))
        hijri_day = _api_int(event.get('hijri_day'))
        formatted_event = {
            'id': event.get('id'),
            'title': event.get('title', ''),
            'description': event.get('body', ''),
            'is_holiday': _api_bool(event.get('is_holiday')),
            'jalali_date': f"{jalali_year}-{jalali_month:02d}-{jalali_day:02d}",
            'gregorian_date': f"{gregorian_year}-{gregorian_month:02d}-{gregorian_day:02d}",
            'hijri_date': f"{hijri_year}-{hijri_month:02d}-{hijri_day:02d}",
            'jalali_day_title': event.get('jalali_day_title', ''),
            'gregorian_day_title': event.get('gregorian_day_title', ''),
            'date_string': event.get('date_string', ''),
            'base': event.get('base', 0),
        }
        events.append(formatted_event)

        if formatted_event['is_holiday']:
            holidays.append(formatted_event)

    days = []
    events_by_day: Dict[int, List[Dict[str, Any]]] = {}
    for event in events:
        try:
            event_day = int(event['jalali_date'].split("-")[2])
        except (ValueError, IndexError):
            continue
        events_by_day.setdefault(event_day, []).append(event)

    for day_info in data.get('day_list', []):
        if not isinstance(day_info, dict):
            continue
        day_number = _api_int(day_info.get('index_in_base1') or day_info.get('day'))
        if not day_number:
            continue
        day_events = events_by_day.get(day_number, [])
        days.append({
            'day': day_number,
            'is_holiday': _api_bool(day_info.get('is_holiday')) or any(event['is_holiday'] for event in day_events),
            'is_weekend': _api_bool(day_info.get('is_weekend')),
            'is_today': _api_bool(day_info.get('is_today')),
            'enabled': day_info.get('enabled', True),
            'events': day_events,
        })

    return {
        'year': year,
        'month': month,
        'day': day,
        'events': events,
        'holidays': holidays,
        'days': days,
        'total_events': len(events),
        'total_holidays': len(holidays),
    }


def day_info_from_calendar(calendar_data: Dict[str, Any], year: int, month: int, day: int) -> Dict[str, Any]:
    formatted = format_calendar_events(calendar_data, year, month, day)
    day_info = next((item for item in formatted.get('days', []) if item.get('day') == day), None)
    day_events = [
        event for event in formatted.get('events', [])
        if event.get('jalali_date') == f"{year}-{month:02d}-{day:02d}"
    ]
    return {
        'jalali_date': f"{year}-{month:02d}-{day:02d}",
        'events': day_events,
        'is_holiday': any(_api_bool(event.get('is_holiday')) for event in day_events) or bool(day_info and _api_bool(day_info.get('is_holiday'))),
        'is_weekend': bool(day_info and _api_bool(day_info.get('is_weekend'))),
        'is_today': bool(day_info and _api_bool(day_info.get('is_today'))),
        'enabled': day_info.get('enabled', True) if day_info else True,
    }


if router:
    @router.get("/calendar/{year}/{month}")
    async def get_calendar_month(
        year: int,
        month: int,
        day: int = 0,
        base1: int = 0,
        base2: int = 1,
        base3: int = 2
    ) -> Dict[str, Any]:
        try:
            return fetch_calendar_month(year, month, day, base1, base2, base3)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")

    @router.get("/calendar/{year}/{month}/events")
    async def get_calendar_events(year: int, month: int, day: int = 0) -> Dict[str, Any]:
        try:
            calendar_data = await get_calendar_month(year, month, day)
            return format_calendar_events(calendar_data, year, month, day)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to process events: {str(e)}")

    @router.get("/calendar/{year}/{month}/{day}/info")
    async def get_day_info(year: int, month: int, day: int) -> Dict[str, Any]:
        try:
            calendar_data = await get_calendar_month(year, month, day)
            return day_info_from_calendar(calendar_data, year, month, day)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to get day info: {str(e)}")

    @router.get("/calendar/current")
    async def get_current_month_calendar() -> Dict[str, Any]:
        try:
            from jdatetime import datetime as jdatetime_dt
            now = jdatetime_dt.now()
            year = now.year
            month = now.month
        except ImportError:
            year = 1404
            month = 11
        return await get_calendar_events(year, month)

    @router.get("/holidays/{year}/{month}/{day}")
    async def get_holiday_legacy(year: int, month: int, day: int) -> Dict[str, Any]:
        try:
            day_info = await get_day_info(year, month, day)
            holiday_event = next(
                (event for event in day_info.get('events', []) if event.get('is_holiday')),
                None
            )
            if holiday_event:
                return {
                    "date": day_info['jalali_date'],
                    "title": holiday_event['title'],
                    "holiday": True,
                    "description": holiday_event.get('description', '')
                }
            if day_info.get('is_holiday'):
                return {
                    "date": day_info['jalali_date'],
                    "title": "تعطیل",
                    "holiday": True,
                    "description": ""
                }
            return {
                "date": day_info['jalali_date'],
                "title": "",
                "holiday": False
            }
        except Exception:
            return {
                "date": f"{year}-{month:02d}-{day:02d}",
                "title": "",
                "holiday": False
            }
