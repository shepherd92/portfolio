"""Summarize owned assets and debt without changing the trading portfolio."""

from bisect import bisect_left, bisect_right
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from math import fsum
from types import MappingProxyType

from portfolio.calculator import huf_value
from portfolio.models import (CASH, STOCK_MARKET, TREASURY, AllocationTargets, MarketData,
                              currency_code, current_values, number)
from portfolio.wealth import WealthProjection, WealthSettings


@dataclass(frozen=True)
class CurrentOverview:
    """Base-currency gross assets, signed investments and separate liabilities."""

    allocations: Mapping[str, float]
    investment_total: float
    assets_total: float
    loans: float
    cash_deficit: float
    net_worth: float

    def __post_init__(self):
        object.__setattr__(self, 'allocations', MappingProxyType(dict(self.allocations)))

    def target_values(self, allocation):
        """Keep fixed assets and reweight positive investment assets for charts.

        Both pies retain the same gross value. Loans and cash deficits stay
        outside them; this comparison does not predict post-trade balances.
        """
        if not isinstance(allocation, AllocationTargets):
            raise ValueError('Provide validated allocation targets.')
        totals = allocation.totals
        weight = fsum(totals.values())
        gross_investments = fsum(self.allocations[parent] for parent in totals)
        return {**self.allocations,
                **{parent: gross_investments * (value / weight) for parent, value in totals.items()}}


def current_overview(holdings, manual, cash, market, wealth):
    """Value today's assets for display, independently of allocation targets.

    Each positive cash balance is an asset; each negative balance is a cash
    deficit. Loans never become pie slices. Property values and funded pensions
    affect net worth, while the signed investment total stays tradable-only.
    """
    if not isinstance(market, MarketData) or not isinstance(wealth, WealthSettings):
        raise ValueError('Provide current market data and validated other assets.')
    try:
        investment = current_values(holdings, manual, cash, market)
        parts = {name: [] for name in (STOCK_MARKET, TREASURY, CASH, 'Properties', 'Pension funds')}
        for (parent, _), value in investment.items():
            parts[parent].append(max(0., value))
        debts = []
        for balance in wealth.current_balances():
            value = market.value(balance.amount, balance.currency) if balance.amount else 0.
            if balance.category == 'Loans':
                debts.append(value)
            else:
                name = 'Pension funds' if balance.category == 'Pensions' else balance.category
                parts[name].append(value)
        allocations = {name: number(fsum(values), f'{name} value', minimum=0) for name, values in parts.items()}
        investments = number(fsum(investment.values()), 'Investment portfolio value')
        assets = number(fsum(allocations.values()), 'Total asset value', minimum=0)
        loans = number(fsum(debts), 'Loan liabilities', minimum=0)
        deficit = number(fsum(-value for (parent, _), value in investment.items() if parent == CASH and value < 0),
                         'Cash deficit', minimum=0)
        net_worth = number(fsum((assets, -loans, -deficit)), 'Net worth')
        return CurrentOverview(allocations, investments, assets, loans, deficit, net_worth)
    except OverflowError as error:
        raise ValueError('Asset summary exceeds the supported numeric range.') from error


def net_worth_series(wealth, as_of, dates, liquid_totals, rates, currency, *, before_payments=None, projection=None):
    """Return display-currency assets, positive liabilities and signed net worth.

    ``liquid_totals`` already uses ``currency``. Repeated dates may show the
    state before and after that month's payments; ``before_payments`` identifies
    those states. The external projection assumes scheduled transfers are paid,
    so call this only after validating that the liquid projection can fund them.
    """
    if not isinstance(wealth, WealthSettings) or type(as_of) is not date:
        raise ValueError('Provide validated external assets and their opening date.')
    dates, liquid_totals = tuple(dates), tuple(liquid_totals)
    before = tuple(False for _ in dates) if before_payments is None else tuple(before_payments)
    if len(liquid_totals) != len(dates) or len(before) != len(dates):
        raise ValueError('Net-worth dates, balances and payment markers must have matching lengths.')
    if any(type(day) is not date or day < as_of for day in dates):
        raise ValueError('Net-worth dates cannot precede the opening balance date.')
    if any(type(value) is not bool for value in before):
        raise ValueError('Before-payment markers must be true or false.')
    currency = currency_code(currency)
    names = ('Investment portfolio', 'Properties', 'Pensions', 'Loans', 'Net worth')
    result = {name: [] for name in names}
    if not dates:
        return {name: () for name in names}
    conversion = huf_value(1, currency, rates)
    if projection is None:
        projection = wealth.project(as_of, max(dates))
    if not isinstance(projection, WealthProjection) or not projection.snapshots:
        raise ValueError('Provide a validated external-asset projection.')
    snapshots = projection.snapshots
    if snapshots[0].day != as_of or snapshots[-1].day < max(dates):
        raise ValueError('The external-asset projection must cover the requested dates.')
    snapshot_dates = tuple(snapshot.day for snapshot in snapshots)
    factors = {item.id: item.annual_factor for item in wealth.properties}
    factors.update({item.id: 1 + item.annual_return / 100 for item in wealth.pensions})
    factors.update({item.id: 1 + item.annual_interest / 100 for item in wealth.loans})
    try:
        for day, liquid, before_cash in zip(dates, liquid_totals, before):
            liquid = number(liquid, 'Investment portfolio value', minimum=0)
            exact = bisect_left(snapshot_dates, day)
            index = exact if before_cash and exact < len(snapshots) and snapshot_dates[exact] == day else bisect_right(snapshot_dates, day) - 1
            snapshot = snapshots[index]
            years = (day - snapshot.day).days / 365.25
            values = {name: [] for name in names[1:4]}
            for balance in snapshot.balances:
                amount = balance.amount * factors[balance.source_id] ** years if years else balance.amount
                values[balance.category].append(huf_value(amount, balance.currency, rates) / conversion)
            totals = {name: number(fsum(amounts), f'{name} value') for name, amounts in values.items()}
            totals['Investment portfolio'] = liquid
            totals['Net worth'] = number(fsum((liquid, totals['Properties'], totals['Pensions'], -totals['Loans'])),
                                         'Net worth')
            for name in names:
                result[name].append(totals[name])
    except OverflowError as error:
        raise ValueError('Net-worth projection exceeds the supported numeric range.') from error
    return {name: tuple(values) for name, values in result.items()}
