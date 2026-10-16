"""Whole-share and monetary trade recommendations, without I/O or mutation."""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from math import ceil, fsum
from time import monotonic

import numpy as np
from scipy.optimize import Bounds, LinearConstraint

from portfolio.models import (CASH, STOCK_MARKET, TREASURY, MAX_SHARES, Conversion, Holding,
                    ManualHolding, ManualTrade, Plan, Trade, allocation_name, number)
from portfolio.solver import milp


PLANNING_TIMEOUT_SECONDS = 60 * 60  # Shared by every optimization stage and retry.


class PlanningError(ValueError):
    """No verified optimal plan could be produced."""


def rebalance(portfolio, market, *, allow_selling=False, minimum_values=None):
    """Minimize allocation error, cash-target error, then traded value.

    Every asset-class target is a percentage of the whole portfolio.
    Currency targets distribute remaining cash through FX. Main tickers and
    manual holdings may increase; signed opening cash must be funded. Every
    solver stage must finish optimally within one fixed 60-minute deadline.
    Optional cash and MÁK minimum_values protect absolute base-currency reserves.
    """
    if not isinstance(allow_selling, bool):
        raise ValueError("allow_selling must be a boolean")
    deadline = monotonic() + PLANNING_TIMEOUT_SECONDS
    minimums = _reserve_values(minimum_values, market)
    market.validate_portfolio(portfolio)
    positions, prices, owned, wealth = _value_positions(portfolio, market, minimums)
    cash_minimum = fsum(value for (parent, _), value in minimums.items() if parent == CASH)
    tolerance = _tolerance(prices, wealth)
    if fsum(minimums.values()) > wealth + tolerance:
        raise PlanningError("Protected reserves exceed the portfolio value")
    final = (_optimize(portfolio, positions, prices, owned, wealth, allow_selling,
                       deadline, minimums, cash_minimum) if positions else ())
    plan = _build_plan(portfolio, market, positions, prices, wealth, final, minimums)
    if any(plan.allocation_values.get(leaf, 0.) < value - tolerance
           for leaf, value in minimums.items()):
        raise PlanningError("The proposed plan does not fund protected reserves")
    _remaining_time(deadline)
    return plan


def _reserve_values(values, market):
    if values is None:
        return {}
    if not isinstance(values, Mapping):
        raise ValueError("Protected reserves must map allocation leaves to values")
    minimums = {}
    for leaf, value in values.items():
        if not isinstance(leaf, tuple) or len(leaf) != 2 or leaf[0] not in (CASH, TREASURY):
            raise ValueError("Only Cash and MÁK reserves can be protected")
        parent, currency = leaf[0], allocation_name(*leaf)
        if (parent, currency) in minimums:
            raise ValueError("Duplicate protected reserve")
        value = number(value, "Protected reserve", minimum=0)
        if value:
            market.value(0., currency)
            minimums[parent, currency] = value
    return minimums


def _remaining_time(deadline):
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise PlanningError('Planning exceeded the 60-minute limit. Try again or simplify the portfolio.')
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


def _value_positions(portfolio, market, minimums):
    positions = []
    targets = portfolio.allocation.weights
    for holding in portfolio.required_holdings:
        quote = market.quotes[holding.ticker]
        positions.append(_Position(holding, holding.shares, quote.price,
                                   quote.currency, holding.is_main and targets[STOCK_MARKET][holding.group] > 0))
    manual = {holding.currency: holding for holding in portfolio.required_manual_holdings}
    for parent, currency in minimums:
        if parent == TREASURY:
            manual.setdefault(currency, ManualHolding(0., currency))
    for holding in manual.values():
        positions.append(_Position(holding, holding.cents, .01, holding.currency,
                                   targets[TREASURY].get(holding.currency, 0) > 0
                                   or (TREASURY, holding.currency) in minimums))
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


def _target_expressions(portfolio, positions, prices, wealth, minimums):
    """Express noncash targets and aggregate cash in base-currency value.

    With free FX, cash-currency error is absolute total-cash error plus a
    constant. Floors above a currency target shift its effective cash target.
    """
    expressions = []
    for category, name in dict.fromkeys(position.leaf for position in positions):
        key = category, name
        target = portfolio.allocation.weights[category].get(name, 0.)
        coefficients = np.array([price if position.leaf == key else 0.
                                 for position, price in zip(positions, prices)])
        expressions.append((coefficients, wealth * target / 100, False))
    cash_targets = portfolio.allocation.weights[CASH]
    excess = fsum(max(0., value - wealth * cash_targets.get(currency, 0.) / 100)
                  for (parent, currency), value in minimums.items() if parent == CASH)
    cash_target = wealth * portfolio.allocation.totals[CASH] / 100 + excess
    expressions.append((prices, wealth - cash_target, True))
    return expressions


def _constraints(portfolio, positions, prices, owned, wealth, minimums, cash_minimum):
    # Variables: final executable units, group errors, and absolute unit changes.
    expressions = _target_expressions(portfolio, positions, prices, wealth, minimums)
    count, group_count = len(positions), len(expressions)
    size = count * 2 + group_count
    rows, limits = [], []

    def constrain(coefficients, upper_limit):
        rows.append(coefficients)
        limits.append(upper_limit)

    budget = np.zeros(size)
    budget[:count] = prices
    constrain(budget, max(0., wealth - cash_minimum))
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

    tracking, cash_error, turnover = (np.zeros(size) for _ in range(3))
    for index, (_, _, is_cash) in enumerate(expressions):
        tracking[count + index] = 1
        cash_error[count + index] = is_cash
    turnover[count + group_count:] = prices
    return rows, limits, (tracking, cash_error, turnover)


def _optimize(portfolio, positions, prices, owned, wealth, allow_selling, deadline,
              minimums, cash_minimum):
    # Monetary scaling keeps HUF budgets and prices well-conditioned without
    # changing relative allocation errors, turnover, or executable units.
    scale = max(1., float(prices.max()) / 100)
    prices, wealth, cash_minimum = prices / scale, wealth / scale, cash_minimum / scale
    minimums = {leaf: value / scale for leaf, value in minimums.items()}
    count = len(positions)
    tolerance = _tolerance(prices, wealth)
    lower = np.zeros(count) if allow_selling else owned.copy()
    for index, (position, price) in enumerate(zip(positions, prices)):
        minimum = minimums.get(position.leaf, 0.)
        if minimum > MAX_SHARES * price + tolerance:
            raise PlanningError("Protected reserves exceed executable holding limits")
        lower[index] = max(lower[index], ceil(max(0., minimum - tolerance) / price))
    budget = max(0., wealth - cash_minimum)
    if fsum(prices * lower) > budget + tolerance:
        if minimums:
            reason = ("Add cash or allow selling." if not allow_selling
                      else "The required MÁK amounts cannot fit in executable currency hundredths.")
            selling = " with selling disabled" if not allow_selling else ""
            raise PlanningError(f"Protected reserves cannot be funded{selling}. {reason}")
        raise PlanningError("Available cash cannot fund this plan with selling disabled")
    # Division can round a large affordable holding down by one unit. Retain
    # that known feasible bound; the shared budget still constrains total value.
    upper = np.array([max(np.floor(min((budget + tolerance) / price, MAX_SHARES)),
                          position.units if position.units * price <= budget else 0)
                      if position.can_buy else position.units
                      for position, price in zip(positions, prices)])
    if np.any(lower > upper):
        raise PlanningError("Protected reserves exceed executable holding limits" if minimums else
                            "Available cash cannot fund this plan with selling disabled")
    if np.array_equal(lower, upper):
        return tuple(int(units) for units in lower)
    rows, limits, objectives = _constraints(portfolio, positions, prices, owned, wealth, minimums, cash_minimum)
    auxiliary_count = len(objectives[0]) - count
    lower_bounds = np.r_[lower, np.zeros(auxiliary_count)]
    upper_bounds = np.r_[upper, np.full(auxiliary_count, np.inf)]
    reference = lower if minimums else None
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
        raise PlanningError('Planning exceeded the 60-minute limit. Try again or simplify the portfolio.')
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


def _build_plan(portfolio, market, positions, prices, wealth, final, minimums):
    tolerance = _tolerance(prices, wealth)
    trades, balances = [], dict(portfolio.cash)
    manual_trades = []
    final_manual = {holding.currency: holding for holding in portfolio.manual_holdings}
    final_shares = {holding.ticker: holding.shares for holding in portfolio.holdings}
    allocation_parts = {(category, name): []
                        for category, weights in portfolio.allocation.weights.items() for name in weights}
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

    cash_targets = portfolio.allocation.weights[CASH]
    cash_minimums = {currency: value for (parent, currency), value in minimums.items() if parent == CASH}
    balances, conversions = _settle_cash(balances, market, tolerance, cash_targets,
                                         cash_minimums, wealth)
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


def _settle_cash(balances, market, tolerance, targets, minimums, wealth):
    # Net trades, then fund the target currency balances and any opening debt.
    desired = _desired_cash(balances, market, tolerance, targets, minimums, wealth)
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
    if (any(targets.values()) or minimums) and any(
            abs(market.value(value - desired[currency], currency)) > tolerance
            for currency, value in balances.items()):
        raise PlanningError("Currency conversions did not meet the cash allocation")
    balances = {currency: max(0., value) for currency, value in sorted(balances.items())}
    return balances, tuple(conversions)


def _desired_cash(balances, market, tolerance, targets, minimums, wealth):
    desired = dict.fromkeys(balances, 0.)
    target_total = fsum(targets.values())
    if target_total or minimums:
        for currency in targets.keys() | minimums.keys():
            if not targets.get(currency, 0) and currency not in balances and currency not in minimums:
                continue
            market.value(0, currency)  # Target-only currencies still need an exchange rate.
            balances.setdefault(currency, 0.)
            desired.setdefault(currency, 0.)
        total = fsum(market.value(value, currency) for currency, value in balances.items())
        reserved = fsum(minimums.values())
        if total < reserved - tolerance:
            raise PlanningError("Available cash cannot fund protected reserves")
        weights = {currency: max(0., wealth * targets.get(currency, 0.) / 100
                                - minimums.get(currency, 0.)) for currency in balances}
        if not fsum(weights.values()):
            weights = {currency: targets.get(currency, 0.) for currency in balances}
        if not fsum(weights.values()):
            weights = {currency: max(0., market.value(value, currency))
                       for currency, value in balances.items()}
        weight_total = fsum(weights.values())
        remaining = max(0., total - reserved)
        desired.update({currency: (minimums.get(currency, 0.) +
                                  (remaining * weight / weight_total if weight_total else 0.))
                        / market.rates[currency] for currency, weight in weights.items()})
    return desired
