"""Boundary checks for the independent gross cash-arbitrage calculation."""

import pytest

from options.backend.analysis.arbitrage import estimate_call_cash_arbitrage


def estimate(**changes):
    return estimate_call_cash_arbitrage(**{
        "option_type": "call", "days_remaining": 0, "pricing_current": True,
        "strike_price": 1000, "underlying_closing_price": 1120,
        "ask_price": 100, "ask_volume": 2, **changes,
    })


def test_cash_estimate_subtracts_premium_and_uses_it_as_return_denominator():
    result = estimate(ask_price=80)
    assert result.estimated_cash_value_per_unit == 120
    assert result.gross_profit_per_unit == 40
    assert result.return_percent == 50
    assert result.is_arbitrage_candidate is True


@pytest.mark.parametrize("changes", [
    {"option_type": "put"}, {"option_type": None},
    {"days_remaining": -1}, {"days_remaining": 1}, {"pricing_current": False},
    {"ask_price": 0}, {"ask_volume": 0}, {"strike_price": True},
    {"underlying_closing_price": None}, {"underlying_closing_price": float("inf")},
    {"ask_price": float("nan")},
])
def test_ineligible_contracts_and_invalid_quotes_have_no_estimate(changes):
    result = estimate(**changes)
    assert result.estimated_cash_value_per_unit is None
    assert result.gross_profit_per_unit is None
    assert result.return_percent is None
    assert result.is_arbitrage_candidate is False


def test_overflowing_return_is_never_reported_as_a_candidate():
    result = estimate(ask_price=5e-324)
    assert result.return_percent is None
    assert result.gross_profit_per_unit is None
    assert result.is_arbitrage_candidate is False


@pytest.mark.parametrize("closing, expected", [(1100, False), (1099, False), (1100.0000001, True)])
def test_candidate_requires_strictly_positive_unrounded_profit(closing, expected):
    assert estimate(underlying_closing_price=closing).is_arbitrage_candidate is expected


@pytest.mark.parametrize("strike, closing, ask", [
    (1000, 1100.2, 100.2), (0.1, 0.4, 0.3), (0.1, 0.3, 0.2),
])
def test_decimal_break_even_never_becomes_a_false_profit_or_loss(strike, closing, ask):
    result = estimate(strike_price=strike, underlying_closing_price=closing, ask_price=ask)
    assert result.gross_profit_per_unit == 0
    assert result.return_percent == 0
    assert result.is_arbitrage_candidate is False
