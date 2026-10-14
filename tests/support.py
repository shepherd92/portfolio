"""Shared fixtures and independent allocation oracles for planning tests."""

from fractions import Fraction
import itertools
import math

from portfolio.models import AllocationTargets, CASH, STOCK_MARKET, TREASURY, Portfolio


def portfolio_fixture(holdings, weights, cash, manual=()):
    """Declare fixture weights in total-portfolio percentages for readability."""
    stocks = {key: value for key, value in weights.items() if key not in (TREASURY, CASH)}
    stock_weight = math.fsum(stocks.values())
    currencies = {holding.currency for holding in manual} or ({'EUR'} if weights.get(TREASURY) else set())
    allocation = AllocationTargets(
        {STOCK_MARKET: stock_weight, TREASURY: weights.get(TREASURY, 0), CASH: weights.get(CASH, 0)},
        {STOCK_MARKET: {key: value * 100 / stock_weight if stock_weight else value for key, value in stocks.items()},
         TREASURY: {currency: 100 / len(currencies) for currency in currencies}, CASH: {'EUR': 100}})
    return Portfolio(holdings, allocation, cash, manual)


def allocation_objectives(portfolio, values, wealth):
    """Independently score top-level, effective-leaf, and cash target errors."""
    allocation = portfolio.allocation
    total = {parent: sum(value for (branch, _), value in values.items() if branch == parent)
             for parent in allocation.top}
    top_error = sum(abs(total[parent] - wealth * target / 100) for parent, target in allocation.top.items())
    leaf_error = sum(abs(values.get(key, 0) - wealth * target / 100)
                     for key, target in allocation.effective.items())
    leaf_error += sum(abs(value) for key, value in values.items() if key not in allocation.effective)
    return top_error, leaf_error, abs(total[CASH] - wealth * allocation.top[CASH] / 100)


def exhaustive_objectives(portfolio, snapshot, allow_selling, plan=None):
    """Score public quantities using exact rational arithmetic, without planner helpers."""
    units = [((STOCK_MARKET, h.group), h.shares, _rational(snapshot.quotes[h.ticker].price)
              * _rational(snapshot.rates[snapshot.quotes[h.ticker].currency]),
              h.is_main and portfolio.allocation.effective[STOCK_MARKET, h.group] > 0)
             for h in portfolio.holdings]
    units += [((TREASURY, h.currency), h.cents, _rational(snapshot.rates[h.currency]) / 100,
               portfolio.allocation.effective.get((TREASURY, h.currency), 0) > 0)
              for h in portfolio.manual_holdings]
    wealth = sum(owned * price for _, owned, price, _ in units)
    wealth += sum(_rational(amount) * _rational(snapshot.rates[currency])
                  for currency, amount in portfolio.cash.items())

    def score(quantities):
        values = dict.fromkeys(portfolio.allocation.effective, Fraction(0))
        for (key, _, price, _), quantity in zip(units, quantities):
            values[key] = values.get(key, Fraction(0)) + price * quantity
        cash = wealth - sum(values.values())
        if cash < 0:
            return None
        values.update({(CASH, currency): cash * _rational(weight) / 100
                       for currency, weight in portfolio.allocation.children[CASH].items()})
        targets = portfolio.allocation
        top_values = {parent: sum(value for (branch, _), value in values.items() if branch == parent)
                      for parent in targets.top}
        top_error = sum(abs(value - wealth * _rational(targets.top[parent]) / 100)
                        for parent, value in top_values.items())
        leaf_error = sum(abs(value - wealth * _rational(targets.effective.get(key, 0)) / 100)
                         for key, value in values.items())
        cash_error = abs(cash - wealth * _rational(targets.top[CASH]) / 100)
        turnover = sum(price * abs(quantity - owned)
                       for (_, owned, price, _), quantity in zip(units, quantities))
        return top_error, leaf_error, cash_error, turnover

    if plan is not None:
        quantities = tuple(plan.final_shares[h.ticker] for h in portfolio.holdings)
        quantities += tuple(h.cents for h in plan.final_manual_holdings)
        return score(quantities)
    minimum_cost = 0 if allow_selling else sum(owned * price for _, owned, price, _ in units)
    ranges = [range(0 if allow_selling else owned,
                    (int((wealth - minimum_cost + (0 if allow_selling else owned * price)) // price)
                     if main else owned) + 1)
              for _, owned, price, main in units]
    candidates = [value for quantities in itertools.product(*ranges)
                  if (value := score(quantities)) is not None]
    return min(candidates) if candidates else None


def _rational(value):
    return Fraction(str(value))
