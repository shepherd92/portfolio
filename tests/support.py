"""Shared fixtures and independent allocation oracles for planning tests."""

from fractions import Fraction
import itertools

from portfolio.models import AllocationTargets, CASH, STOCK_MARKET, TREASURY, Portfolio


def portfolio_fixture(holdings, weights, cash, manual=()):
    """Declare fixture weights in total-portfolio percentages for readability."""
    stocks = {key: value for key, value in weights.items() if key not in (TREASURY, CASH)}
    currencies = {holding.currency for holding in manual} or ({'EUR'} if weights.get(TREASURY) else set())
    allocation = AllocationTargets({
        STOCK_MARKET: stocks,
        TREASURY: {currency: weights.get(TREASURY, 0) / len(currencies) for currency in currencies},
        CASH: {'EUR': weights.get(CASH, 0)}})
    return Portfolio(holdings, allocation, cash, manual)


def exhaustive_objectives(portfolio, market, allow_selling, plan=None):
    """Score public quantities using exact rational arithmetic, without planner helpers."""
    units = [((STOCK_MARKET, h.group), h.shares, _rational(market.quotes[h.ticker].price)
              * _rational(market.rates[market.quotes[h.ticker].currency]),
              h.is_main and portfolio.allocation.weights[STOCK_MARKET][h.group] > 0)
             for h in portfolio.holdings]
    units += [((TREASURY, h.currency), h.cents, _rational(market.rates[h.currency]) / 100,
               portfolio.allocation.weights[TREASURY].get(h.currency, 0) > 0)
              for h in portfolio.manual_holdings]
    wealth = sum(owned * price for _, owned, price, _ in units)
    wealth += sum(_rational(amount) * _rational(market.rates[currency])
                  for currency, amount in portfolio.cash.items())

    targets = {(category, name): _rational(target) / 100
               for category, weights in portfolio.allocation.weights.items()
               for name, target in weights.items() if category != CASH}
    cash_target = sum(_rational(target) / 100 for target in portfolio.allocation.weights[CASH].values())

    def score(quantities):
        values = dict.fromkeys(targets, Fraction(0))
        for (key, _, price, _), quantity in zip(units, quantities):
            values[key] = values.get(key, Fraction(0)) + price * quantity
        cash = wealth - sum(values.values())
        if cash < 0:
            return None
        cash_error = abs(cash - wealth * cash_target)
        tracking_error = sum(abs(value - wealth * targets.get(key, 0))
                             for key, value in values.items()) + cash_error
        turnover = sum(price * abs(quantity - owned)
                       for (_, owned, price, _), quantity in zip(units, quantities))
        return tracking_error, cash_error, turnover

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
