"""Whole-share and monetary trade recommendations, without I/O or mutation."""

from dataclasses import dataclass
from decimal import Decimal
from math import fsum
from time import monotonic

import numpy as np
from scipy.optimize import Bounds, LinearConstraint

from portfolio.models import (CASH, STOCK_MARKET, TREASURY, MAX_SHARES, Conversion, Holding,
                    ManualHolding, ManualTrade, Plan, Trade, number)
from portfolio.solver import milp


DEFAULT_TIME_LIMIT = 300.  # Seconds for the entire optimization, including retries.


class PlanningError(ValueError):
    """No verified optimal plan could be produced."""


def rebalance(portfolio, market, *, allow_selling=False, time_limit=DEFAULT_TIME_LIMIT):
    """Minimize parent error, child error, cash-target error, then traded value.

    Hierarchical targets prioritize top-level error before effective leaf error.
    Currency targets distribute remaining cash through FX. Main tickers and
    manual holdings may increase; signed opening cash must be funded. Every
    solver stage must finish optimally within one shared time_limit in seconds.
    """
    if not isinstance(allow_selling, bool):
        raise ValueError("allow_selling must be a boolean")
    time_limit = number(time_limit, 'Time limit', minimum=0)
    if time_limit == 0:
        raise ValueError('Time limit must be greater than zero')
    deadline = monotonic() + time_limit
    market.validate_portfolio(portfolio)
    positions, prices, owned, wealth = _value_positions(portfolio, market)
    final = _optimize(portfolio, positions, prices, owned, wealth, allow_selling, deadline) if positions else ()
    plan = _build_plan(portfolio, market, positions, prices, wealth, final)
    _remaining_time(deadline)
    return plan


def _remaining_time(deadline):
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise PlanningError('Planning time limit reached. Increase the time limit and try again.')
    return remaining


@dataclass(frozen=True)
class _Position:
    """One executable unit is a listed share or a manual currency hundredth."""

    asset: Holding | ManualHolding
    units: int
    price: float  # native-currency value of one executable unit
    currency: str
    can_buy: bool

    @property
    def leaf(self):
        return ((STOCK_MARKET, self.asset.group) if isinstance(self.asset, Holding)
                else (TREASURY, self.currency))


def _tolerance(prices, wealth):
    # Never admit another executable unit. HUF hundredths need enough slack
    # for base-currency accounting roundoff, even for multimillion-HUF values.
    unit_limit = float(prices.min()) * .01 if len(prices) else 1e-5
    return min(1e-5, max(1e-8, wealth * 1e-12), unit_limit)


def _manual_amount(units):
    amount = units / 100
    if Decimal(str(amount)) * 100 != units:
        raise PlanningError("Manual value is too large to represent to two decimals")
    return amount


def _value_positions(portfolio, market):
    positions = []
    targets = portfolio.allocation.effective
    for holding in portfolio.required_holdings:
        quote = market.quotes[holding.ticker]
        key = (STOCK_MARKET, holding.group)
        positions.append(_Position(holding, holding.shares, quote.price,
                                   quote.currency, holding.is_main and targets[key] > 0))
    for holding in portfolio.required_manual_holdings:
        key = (TREASURY, holding.currency)
        positions.append(_Position(holding, holding.cents, .01, holding.currency, targets.get(key, 0) > 0))
    prices = np.array([market.value(position.price, position.currency) for position in positions])
    if np.any(prices <= 0):
        raise PlanningError("Converted trading units must have positive value")
    owned = np.array([position.units for position in positions], dtype=float)
    try:
        cash = fsum(market.value(value, currency) for currency, value in portfolio.cash.items())
        with np.errstate(over='raise', invalid='raise'):
            wealth = fsum([*(prices * owned), cash])
    except (OverflowError, FloatingPointError) as error:
        raise PlanningError("Portfolio value is too large to calculate") from error
    if not np.isfinite(wealth) or wealth < 0:
        raise PlanningError("Holdings and cash must have a nonnegative net value")
    return tuple(positions), prices, owned, wealth


def _target_expressions(portfolio, positions, prices, wealth):
    """Return each target's linear value expression, desired value, and priority."""
    allocation = portfolio.allocation
    expressions = []
    for branch, target in allocation.top.items():
        coefficients = prices if branch == CASH else np.array([
            price if position.leaf[0] == branch else 0.
            for position, price in zip(positions, prices)])
        desired = wealth * ((1 - target / 100) if branch == CASH else target / 100)
        expressions.append((coefficients, desired, 'cash' if branch == CASH else 'top'))
    targets = dict.fromkeys((position.leaf for position in positions), 0.)
    targets.update(allocation.effective)
    for key, target in targets.items():
        if key[0] == CASH:
            weight = allocation.children[CASH][key[1]] / 100
            coefficients, desired = prices * weight, wealth * weight * (1 - allocation.top[CASH] / 100)
        else:
            coefficients = np.array([price if position.leaf == key else 0.
                                     for position, price in zip(positions, prices)])
            desired = wealth * target / 100
        expressions.append((coefficients, desired, 'leaf'))
    return expressions


def _constraints(portfolio, positions, prices, owned, wealth):
    # Variables: final executable units, group errors, and absolute unit changes.
    expressions = _target_expressions(portfolio, positions, prices, wealth)
    count, group_count = len(positions), len(expressions)
    size = count * 2 + group_count
    rows, limits = [], []

    def constrain(coefficients, upper_limit):
        rows.append(coefficients)
        limits.append(upper_limit)

    budget = np.zeros(size)
    budget[:count] = prices
    constrain(budget, wealth)
    for group_index, (coefficients, target_value, _) in enumerate(expressions):
        for sign in (1, -1):
            row = np.zeros(size)
            row[:count] = sign * coefficients
            row[count + group_index] = -1
            constrain(row, sign * target_value)
    for index, shares in enumerate(owned):
        for sign in (1, -1):
            row = np.zeros(size)
            row[index] = sign
            row[count + group_count + index] = -1
            constrain(row, sign * shares)

    tracking, cash_error, turnover, top_error = (np.zeros(size) for _ in range(4))
    for index, (_, _, priority) in enumerate(expressions):
        tracking[count + index] = priority == 'leaf'
        top_error[count + index] = priority in ('top', 'cash')
        cash_error[count + index] = priority == 'cash'
    turnover[count + group_count:] = prices
    return rows, limits, (top_error, tracking, cash_error, turnover)


def _optimize(portfolio, positions, prices, owned, wealth, allow_selling, deadline):
    count = len(positions)
    tolerance = _tolerance(prices, wealth)
    lower = np.zeros(count) if allow_selling else owned
    # Division can round a large affordable holding down by one unit. Retain
    # that known feasible bound; the shared budget still constrains total value.
    upper = np.array([max(np.floor(min((wealth + tolerance) / price, MAX_SHARES)),
                          position.units if position.units * price <= wealth else 0)
                      if position.can_buy else position.units
                      for position, price in zip(positions, prices)])
    if np.any(lower > upper):
        raise PlanningError("Available cash cannot fund this plan with selling disabled")
    rows, limits, objectives = _constraints(portfolio, positions, prices, owned, wealth)
    auxiliary_count = len(objectives[0]) - count
    lower_bounds = np.r_[lower, np.zeros(auxiliary_count)]
    upper_bounds = np.r_[upper, np.full(auxiliary_count, np.inf)]
    reference = None
    for objective in objectives:
        solution = _solve(objective, lower_bounds, upper_bounds, count, rows, limits, tolerance,
                          deadline=deadline, reference=reference)
        rows.append(objective)
        limits.append(float(objective @ solution) + tolerance)
        reference = solution[:count]

    final = tuple(int(q) for q in solution[:count])
    for position, units in zip(positions, final):
        if (units < 0 or (not allow_selling and units < position.units)
                or (not position.can_buy and units > position.units)):
            raise PlanningError("Proposed trades violate holding bounds or permissions")
    return final


def _solve(objective, lower, upper, count, rows, limits, tolerance, *, deadline, reference=None):
    matrix = np.asarray(rows)
    offsets = [np.zeros(len(objective))]
    if reference is not None:
        offsets.insert(0, np.r_[reference, np.zeros(len(objective) - count)])
    # Later objectives use changes from the preceding feasible holdings to
    # keep tight locks accurate with large native-currency quantities. Retry
    # with absolute holdings if needed; both forms express the same constraints.
    for offset in offsets:
        shifted_limits = [fsum([limit, *(-row * offset)])
                          for row, limit in zip(matrix, limits)]
        for presolve in (True, False):
            remaining = _remaining_time(deadline)
            result = milp(objective, integrality=np.r_[np.ones(count), np.zeros(len(objective) - count)],
                          bounds=Bounds(lower - offset, upper - offset),
                          constraints=LinearConstraint(matrix, -np.inf, shifted_limits),
                          options={"mip_rel_gap": 0., "mip_abs_gap": 0., "presolve": presolve,
                                   "mip_feasibility_tolerance": 1e-10,
                                   "time_limit": remaining})
            _remaining_time(deadline)
            if result.status not in (2, 4):
                break
        if result.status not in (2, 4):
            break
    if result.status == 1:
        raise PlanningError('Planning time limit reached. Increase the time limit and try again.')
    if (result.status != 0 or result.x is None or len(result.x) != len(objective)
            or not np.all(np.isfinite(result.x))):
        raise PlanningError(f"Optimizer could not prove an optimal plan: {result.message}")
    shifted_values = result.x.copy()
    shifted_values[:count] = np.rint(shifted_values[:count])
    values = shifted_values + offset
    if (np.max(np.abs(shifted_values[:count] - result.x[:count])) > 1e-5
            or np.any(values < lower - tolerance) or np.any(values > upper + tolerance)
            or np.any(matrix @ values > np.asarray(limits) + tolerance)):
        raise PlanningError("Optimizer returned an invalid executable-unit plan")
    return values


def _build_plan(portfolio, market, positions, prices, wealth, final):
    tolerance = _tolerance(prices, wealth)
    trades, balances = [], dict(portfolio.cash)
    manual_trades = []
    final_manual = {holding.currency: holding for holding in portfolio.manual_holdings}
    final_shares = {holding.ticker: holding.shares for holding in portfolio.holdings}
    allocation_parts = {key: [] for key in portfolio.allocation.effective}
    allocation_parts.update({(TREASURY, holding.currency): [] for holding in portfolio.manual_holdings})
    for position, units, price in zip(positions, final, prices):
        holding = position.asset
        change = units - position.units
        if isinstance(holding, ManualHolding):
            ending = ManualHolding(_manual_amount(units), holding.currency)
            final_manual[holding.currency] = ending
            native_change = _manual_amount(change)
            if change:
                manual_trades.append(ManualTrade(native_change, holding.currency))
        else:
            final_shares[holding.ticker] = units
            native_change = change * position.price
            if change:
                trades.append(Trade(holding.ticker, change, position.price, position.currency))
        balances[position.currency] = balances.get(position.currency, 0.) - native_change
        allocation_parts.setdefault(position.leaf, []).append(units * price)
    allocation_values = {key: fsum(parts) for key, parts in allocation_parts.items()}

    cash_targets = portfolio.allocation.children[CASH]
    balances, conversions = _settle_cash(balances, market, tolerance, cash_targets)
    cash_value = fsum(market.value(value, currency) for currency, value in balances.items())
    if abs(fsum(allocation_values.values()) + cash_value - wealth) > tolerance:
        raise PlanningError("The proposed plan does not conserve portfolio value")
    allocation_values.update({(CASH, currency): market.value(value, currency)
                              for currency, value in balances.items()})
    return Plan(trades=tuple(sorted(trades, key=lambda trade: (trade.shares > 0, trade.ticker))),
                final_shares=final_shares, cash=balances, conversions=conversions,
                wealth=wealth, base_currency=market.base_currency,
                manual_trades=tuple(sorted(manual_trades, key=lambda trade: (trade.amount > 0, trade.currency))),
                final_manual_holdings=tuple(final_manual.values()), allocation_values=allocation_values)


def _settle_cash(balances, market, tolerance, targets):
    # Net trades, then fund the target currency balances and any opening debt.
    desired = dict.fromkeys(balances, 0.)
    if targets:
        for currency, weight in targets.items():
            if weight == 0 and currency not in balances:
                continue
            market.value(0, currency)  # Target-only currencies still need an exchange rate.
            balances.setdefault(currency, 0.)
            desired.setdefault(currency, 0.)
        total = fsum(market.value(value, currency) for currency, value in balances.items())
        if fsum(targets.values()) > 0:
            desired.update({currency: max(0., total) * weight / 100 / market.rates[currency]
                            for currency, weight in targets.items() if currency in balances})
    conversions = []
    for destination in sorted(balances):
        for source in sorted(balances):
            missing = desired[destination] - balances[destination]
            surplus = balances[source] - desired[source]
            if missing <= 0 or surplus <= 0:
                continue
            transfer = min(market.value(missing, destination), market.value(surplus, source))
            from_amount = transfer / market.rates[source]
            to_amount = transfer / market.rates[destination]
            balances[source] -= from_amount
            balances[destination] += to_amount
            conversions.append(Conversion(source, destination, from_amount, to_amount))
    if any(market.value(value, currency) < -tolerance for currency, value in balances.items()):
        raise PlanningError("Sale proceeds and cash cannot fund the requested plan")
    if targets and fsum(targets.values()) > 0 and any(
            abs(market.value(value - desired[currency], currency)) > tolerance
            for currency, value in balances.items()):
        raise PlanningError("Currency conversions did not meet the cash allocation")
    balances = {currency: max(0., value) for currency, value in sorted(balances.items())}
    return balances, tuple(conversions)
