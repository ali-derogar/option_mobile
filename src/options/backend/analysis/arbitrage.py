"""Gross cash-settlement estimates, independent of storage and market access."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from math import isfinite


@dataclass(frozen=True)
class CashArbitrageEstimate:
    """Amounts are per underlying unit; a candidate is not confirmed settlement."""

    estimated_cash_value_per_unit: float | None = None
    gross_profit_per_unit: float | None = None
    return_percent: float | None = None

    @property
    def is_arbitrage_candidate(self) -> bool:
        return self.gross_profit_per_unit is not None and self.gross_profit_per_unit > 0


def estimate_call_cash_arbitrage(
    *,
    option_type: str | None,
    days_remaining: int,
    pricing_current: bool,
    strike_price: float | None,
    underlying_closing_price: float | None,
    ask_price: float | None,
    ask_volume: float | None,
) -> CashArbitrageEstimate:
    """Estimate today's calls using normalized prices, without fees or rounding.

    The expiry date only scopes this estimate. It does not establish eligibility
    for cash settlement, nor does a quoted offer guarantee execution.
    """
    if option_type != "call" or days_remaining != 0 or not pricing_current:
        return CashArbitrageEstimate()
    if strike_price is None or underlying_closing_price is None or ask_price is None or ask_volume is None:
        return CashArbitrageEstimate()
    inputs = (strike_price, underlying_closing_price, ask_price, ask_volume)
    if any(isinstance(value, bool) or not isfinite(value) or value <= 0 for value in inputs):
        return CashArbitrageEstimate()

    # Binary float subtraction can turn decimal break-even into a tiny positive
    # profit. Calculate in decimal, preserving the input prices without a cutoff
    # that would also discard genuinely small profits.
    strike, closing, premium = (Decimal(str(value)) for value in (strike_price, underlying_closing_price, ask_price))
    with localcontext() as context:
        exponents = [value.adjusted() for value in (strike, closing, premium)]
        context.prec = max(28, max(exponents) - min(exponents) + 20)
        cash_value = max(closing - strike, Decimal(0))
        profit = cash_value - premium
        return_percent = float(profit / premium * 100)
    if not isfinite(return_percent):
        return CashArbitrageEstimate()
    return CashArbitrageEstimate(float(cash_value), float(profit), return_percent)
