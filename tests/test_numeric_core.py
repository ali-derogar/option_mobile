"""Numerical invariants for option analytics."""

import math

import pandas as pd
import pytest

from options.backend.analysis import sentiment
from options.backend.analysis.sentiment import (
    compute_intrinsic_value,
    compute_moneyness,
)


@pytest.mark.parametrize(
    ("option_type", "strike", "underlying", "expected"),
    [
        ("call", 100, 120, "ITM"),
        ("call", 100, 80, "OTM"),
        ("call", "100", "100", "ATM"),
        ("put", 100, 80, "ITM"),
        ("put", 100, 120, "OTM"),
        ("put", "100.5", "100.5", "ATM"),
        (None, 100, 120, "unknown"),
        ("call", None, 120, "unknown"),
        ("call", 100, "bad", "unknown"),
        ("call", 100, math.inf, "unknown"),
        ("put", math.nan, 100, "unknown"),
        ("call", "۱۲٬۰۰۰", "۱۲٬۵۰۰", "ITM"),
        ("put", "12,000", "11,500", "ITM"),
    ],
)
def test_compute_moneyness_boundaries(option_type, strike, underlying, expected) -> None:
    assert compute_moneyness(option_type, strike, underlying) == expected


@pytest.mark.parametrize(
    ("option_type", "strike", "underlying", "expected"),
    [
        ("call", 100, 120, 20.0),
        ("call", 100, 80, 0.0),
        ("call", 100, 100, 0.0),
        ("put", 100, 80, 20.0),
        ("put", 100, 120, 0.0),
        ("put", "100.5", "120.25", 0.0),
        ("call", "100.5", "120.25", 19.75),
        ("put", 10_000_000_000, 9_999_999_999, 1.0),
        ("bad", 100, 120, None),
        ("call", None, 120, None),
        ("put", 100, math.nan, None),
        ("call", "۱۲٬۰۰۰", "۱۲٬۵۰۰", 500.0),
        ("put", "12,000", "11,500", 500.0),
    ],
)
def test_compute_intrinsic_value_is_exact_for_known_cases(
    option_type,
    strike,
    underlying,
    expected,
) -> None:
    result = compute_intrinsic_value(option_type, strike, underlying)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_internal_ratio_handles_zero_and_missing_values() -> None:
    assert sentiment._ratio(10, 2) == 5.0
    assert sentiment._ratio(10, 0) is None
    assert sentiment._ratio(None, 2) is None
    assert sentiment._ratio(10, None) is None


def test_internal_sum_ignores_invalid_numbers_without_fabricating_values() -> None:
    df = pd.DataFrame({"value": [10, "2.5", None, math.nan, math.inf, "bad"]})

    assert sentiment._sum(df, "value") == pytest.approx(12.5)
    assert sentiment._sum(pd.DataFrame(), "value") is None
    assert sentiment._sum(df, "missing") is None
    assert sentiment._sum_values([None, math.nan, math.inf]) is None


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (105, 100, True),
        (104.999, 100, False),
        (1, 0, True),
        (0, 0, False),
        (None, 10, False),
        (10, None, False),
    ],
)
def test_advancing_threshold_is_stable(a, b, expected) -> None:
    assert sentiment._is_advancing(a, b) is expected


def test_intrinsic_value_basic_properties_over_a_grid() -> None:
    values = [0, 1, 50.5, 100, 10_000]
    for strike in values:
        previous_call = None
        previous_put = None
        for underlying in values:
            call_value = compute_intrinsic_value("call", strike, underlying)
            put_value = compute_intrinsic_value("put", strike, underlying)

            assert call_value is not None and call_value >= 0
            assert put_value is not None and put_value >= 0
            assert call_value == pytest.approx(max(underlying - strike, 0.0))
            assert put_value == pytest.approx(max(strike - underlying, 0.0))

            if previous_call is not None:
                assert call_value >= previous_call
            if previous_put is not None:
                assert put_value <= previous_put
            previous_call = call_value
            previous_put = put_value
