"""Finite aggregate arithmetic shared by the dashboard analyses."""

from collections.abc import Iterable
from math import fsum, isfinite


def finite_sum(values: Iterable[float]) -> float | None:
    try:
        result = fsum(values)
    except (OverflowError, ValueError):
        return None
    return result if isfinite(result) else None


def finite_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    result = numerator / denominator
    return result if isfinite(result) else None
