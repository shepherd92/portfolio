"""Combine protected HUF reserves with the portfolio's long-term targets."""

from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_FLOOR
from math import fsum
from types import MappingProxyType
from typing import Mapping

from portfolio.calculator import Recommendation, recommend
from portfolio.models import AllocationTargets, CASH, STOCK_MARKET, TREASURY, Portfolio, current_values


@dataclass(frozen=True)
class FundingAllocation:
    portfolio: Portfolio
    recommendation: Recommendation
    minimum_values: Mapping[tuple[str, str], float]


def prepare_allocation(portfolio, market, settings, as_of):
    """Derive disposable plan targets without changing the long-term targets or calculator inputs.

    Values and minimums use the market's base currency. HUF FX is required
    even when the original portfolio has no HUF holdings or targets.
    """
    huf_rate = market.value(1, 'HUF')
    wealth = fsum(current_values(portfolio.holdings, portfolio.manual_holdings,
                                portfolio.cash, market).values())
    rates = {currency: market.value(1, currency) / huf_rate for currency in settings.currencies}
    recommendation = recommend(settings, as_of, portfolio.allocation, total=wealth / huf_rate, rates=rates)
    if not wealth:
        return FundingAllocation(portfolio, recommendation, MappingProxyType({}))
    reserves = recommendation.rows[:-1]
    reserved_stock = fsum(row.stock for row in reserves)
    reserved_mak = fsum(row.mak for row in reserves)
    # Manual holdings trade in hundredths. Keep the untradeable remainder in
    # protected cash so that fully reserved portfolios still have a feasible plan.
    tradable_mak = float((Decimal(str(reserved_mak)) * 100).to_integral_value(rounding=ROUND_FLOOR) / 100)
    reserved_cash = fsum(row.cash for row in reserves) + (reserved_mak - tradable_mak)
    values = _target_values(portfolio.allocation, recommendation.rows[-1].amount, reserved_stock)
    for parent, amount in ((TREASURY, tradable_mak), (CASH, reserved_cash)):
        if amount:
            values[parent]['HUF'] = values[parent].get('HUF', 0) + amount
    weights = {parent: {name: value / recommendation.total * 100 for name, value in leaves.items()}
               for parent, leaves in values.items()}
    effective = AllocationTargets(weights, portfolio.allocation.categories)
    minimums = {(parent, 'HUF'): market.value(amount, 'HUF')
                for parent, amount in ((CASH, reserved_cash), (TREASURY, tradable_mak)) if amount}
    return FundingAllocation(replace(portfolio, allocation=effective), recommendation,
                             MappingProxyType(minimums))


def _target_values(allocation, long_term, reserved_stock):
    values = {parent: {name: long_term * (weight / 100) for name, weight in leaves.items()}
              for parent, leaves in allocation.weights.items()}
    market_weight = allocation.totals[STOCK_MARKET]
    if market_weight:
        for name, weight in allocation.weights[STOCK_MARKET].items():
            values[STOCK_MARKET][name] += reserved_stock * (weight / market_weight)
    return values
