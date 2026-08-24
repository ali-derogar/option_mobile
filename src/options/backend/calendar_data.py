"""Calendar data helpers backed by Time.ir."""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Dict, List


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


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    gdm = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    jy = 0 if gy <= 1600 else 979
    gy -= 621 if gy <= 1600 else 1600
    gy2 = gy + 1 if gm > 2 else gy
    days = (
        (365 * gy)
        + ((gy2 + 3) // 4)
        - ((gy2 + 99) // 100)
        + ((gy2 + 399) // 400)
        - 80
        + gd
        + gdm[gm - 1]
    )
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    jm = 1 + (days // 31) if days < 186 else 7 + ((days - 186) // 30)
    jd = 1 + (days % 31) if days < 186 else 1 + ((days - 186) % 30)
    return jy, jm, jd


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> tuple[int, int, int]:
    jy += 1595
    days = -355668 + (365 * jy) + ((jy // 33) * 8) + (((jy % 33) + 3) // 4) + jd
    days += ((jm - 1) * 31) if jm < 7 else (((jm - 7) * 30) + 186)
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        gy += 100 * ((days - 1) // 36524)
        days = (days - 1) % 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    leap = gy % 4 == 0 and (gy % 100 != 0 or gy % 400 == 0)
    month_lengths = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 1
    for month_length in month_lengths:
        if gd <= month_length:
            break
        gd -= month_length
        gm += 1
    return gy, gm, gd


def shift_jalali_month(year: int, month: int, delta: int) -> tuple[int, int]:
    month += delta
    while month < 1:
        year -= 1
        month += 12
    while month > 12:
        year += 1
        month -= 12
    return year, month


def fetch_calendar_month(year: int, month: int, day: int = 0, base1: int = 0, base2: int = 1, base3: int = 2) -> Dict[str, Any]:
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:146.0) Gecko/20100101 Firefox/146.0",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.5",
        "x-api-key": "ZAVdqwuySASubByCed5KYuYMzb9uB2f7",
        "Origin": "https://www.time.ir",
        "Connection": "keep-alive",
        "Referer": "https://www.time.ir/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "Priority": "u=0",
    }
    params = {
        "year": str(year),
        "month": str(month),
        "day": str(day),
        "base1": str(base1),
        "base2": str(base2),
        "base3": str(base3),
    }

    request = Request(f"https://api.time.ir/v1/event/fa/events/calendar?{urlencode(params)}", headers=headers)
    with urlopen(request, timeout=10.0) as response:
        return json.loads(response.read().decode("utf-8"))


def format_calendar_events(calendar_data: Dict[str, Any], year: int, month: int, day: int = 0) -> Dict[str, Any]:
    data = calendar_data.get("data") if isinstance(calendar_data, dict) else None
    if not isinstance(data, dict):
        return {
            "year": year,
            "month": month,
            "day": day,
            "events": [],
            "holidays": [],
            "days": [],
            "total_events": 0,
            "total_holidays": 0,
        }

    events = []
    holidays = []
    for event in data.get("event_list", []):
        if not isinstance(event, dict):
            continue
        jalali_year = _api_int(event.get("jalali_year"), year)
        jalali_month = _api_int(event.get("jalali_month"), month)
        jalali_day = _api_int(event.get("jalali_day"))
        gregorian_year = _api_int(event.get("gregorian_year"))
        gregorian_month = _api_int(event.get("gregorian_month"))
        gregorian_day = _api_int(event.get("gregorian_day"))
        hijri_year = _api_int(event.get("hijri_year"))
        hijri_month = _api_int(event.get("hijri_month"))
        hijri_day = _api_int(event.get("hijri_day"))
        formatted_event = {
            "id": event.get("id"),
            "title": event.get("title", ""),
            "description": event.get("body", ""),
            "is_holiday": _api_bool(event.get("is_holiday")),
            "jalali_date": f"{jalali_year}-{jalali_month:02d}-{jalali_day:02d}",
            "gregorian_date": f"{gregorian_year}-{gregorian_month:02d}-{gregorian_day:02d}",
            "hijri_date": f"{hijri_year}-{hijri_month:02d}-{hijri_day:02d}",
            "jalali_day_title": event.get("jalali_day_title", ""),
            "gregorian_day_title": event.get("gregorian_day_title", ""),
            "date_string": event.get("date_string", ""),
            "base": event.get("base", 0),
        }
        events.append(formatted_event)
        if formatted_event["is_holiday"]:
            holidays.append(formatted_event)

    events_by_day: Dict[int, List[Dict[str, Any]]] = {}
    for event in events:
        try:
            event_day = int(event["jalali_date"].split("-")[2])
        except (ValueError, IndexError):
            continue
        events_by_day.setdefault(event_day, []).append(event)

    days = []
    for day_info in data.get("day_list", []):
        if not isinstance(day_info, dict):
            continue
        day_number = _api_int(day_info.get("index_in_base1") or day_info.get("day"))
        if not day_number:
            continue
        day_events = events_by_day.get(day_number, [])
        days.append({
            "day": day_number,
            "is_holiday": _api_bool(day_info.get("is_holiday")) or any(event["is_holiday"] for event in day_events),
            "is_weekend": _api_bool(day_info.get("is_weekend")),
            "is_today": _api_bool(day_info.get("is_today")),
            "enabled": day_info.get("enabled", True),
            "events": day_events,
        })

    return {
        "year": year,
        "month": month,
        "day": day,
        "events": events,
        "holidays": holidays,
        "days": days,
        "total_events": len(events),
        "total_holidays": len(holidays),
    }


def day_info_from_calendar(calendar_data: Dict[str, Any], year: int, month: int, day: int) -> Dict[str, Any]:
    formatted = format_calendar_events(calendar_data, year, month, day)
    day_info = next((item for item in formatted.get("days", []) if item.get("day") == day), None)
    day_events = [
        event for event in formatted.get("events", [])
        if event.get("jalali_date") == f"{year}-{month:02d}-{day:02d}"
    ]
    return {
        "jalali_date": f"{year}-{month:02d}-{day:02d}",
        "events": day_events,
        "is_holiday": any(_api_bool(event.get("is_holiday")) for event in day_events) or bool(day_info and _api_bool(day_info.get("is_holiday"))),
        "is_weekend": bool(day_info and _api_bool(day_info.get("is_weekend"))),
        "is_today": bool(day_info and _api_bool(day_info.get("is_today"))),
        "enabled": day_info.get("enabled", True) if day_info else True,
    }


def today_from_calendar(calendar_data: Dict[str, Any], year: int, month: int) -> Dict[str, Any] | None:
    formatted = format_calendar_events(calendar_data, year, month)
    today = next((item for item in formatted.get("days", []) if item.get("is_today")), None)
    if not today:
        return None
    day = _api_int(today.get("day"))
    if not valid_jalali_date(year, month, day):
        return None
    gy, gm, gd = jalali_to_gregorian(year, month, day)
    return {
        "jalali_date": f"{year}-{month:02d}-{day:02d}",
        "gregorian_date": f"{gy}-{gm:02d}-{gd:02d}",
        "year": year,
        "month": month,
        "day": day,
        "is_holiday": bool(today.get("is_holiday")),
        "is_weekend": bool(today.get("is_weekend")),
        "events": today.get("events", []),
        "source": "time.ir",
    }


def fetch_calendar_today(reference_date: date, month_window: int = 2) -> Dict[str, Any]:
    approx_year, approx_month, _ = gregorian_to_jalali(
        reference_date.year,
        reference_date.month,
        reference_date.day,
    )
    for delta in range(-month_window, month_window + 1):
        year, month = shift_jalali_month(approx_year, approx_month, delta)
        today = today_from_calendar(fetch_calendar_month(year, month), year, month)
        if today:
            return today
    gy, gm, gd = reference_date.year, reference_date.month, reference_date.day
    jy, jm, jd = gregorian_to_jalali(gy, gm, gd)
    return {
        "jalali_date": f"{jy}-{jm:02d}-{jd:02d}",
        "gregorian_date": f"{gy}-{gm:02d}-{gd:02d}",
        "year": jy,
        "month": jm,
        "day": jd,
        "is_holiday": False,
        "is_weekend": False,
        "events": [],
        "source": "fallback",
    }
