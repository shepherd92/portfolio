"""Human-readable recommendations shared by the GUI and command line."""

from decimal import Decimal, InvalidOperation
from dataclasses import dataclass
from math import fsum, ulp

from portfolio.models import CASH, TREASURY


def plain_number(value):
    """Show the full finite value without exponent notation, including in editors."""
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return str(value)
    if not amount.is_finite() or not -324 <= amount.adjusted() <= 308:
        return str(value)  # Keep an invalid draft available for normal validation.
    return format(amount, 'f')


def format_number(value, spec=',.0f'):
    """Group numeric display text with narrow nonbreaking spaces."""
    return format(value, spec).replace(',', '\u202f')


def format_amount(value, places=0):
    """Round display text only, using fixed-point notation and grouped digits."""
    text = format_number(Decimal(str(value)), f',.{places}f')
    return text.removeprefix('-') if Decimal(text.replace('\u202f', '')) == 0 else text


def format_percent(value):
    return f"{format_amount(value, 2).rstrip('0').rstrip('.')}%"


@dataclass(frozen=True)
class TransactionLedger:
    purchases: float
    proceeds: float
    cash_change: float
    paid: float
    received: float
    balanced: bool

    @property
    def display_paid(self):
        return max(self.purchases, self.proceeds) if self.balanced else self.paid

    @property
    def display_received(self):
        return self.display_paid if self.balanced else self.received

    @property
    def display_cash_change(self):
        return self.purchases - self.proceeds if self.balanced else self.cash_change


def accounting_tolerance(values):
    """Allow arithmetic roundoff, never more than 0.00001 currency units."""
    return min(1e-5, max(1e-12, 4 * fsum(ulp(value) for value in values)))


def transaction_ledger(plan, portfolio, market):
    """Reconcile actual cash and trade legs in the plan's base currency.

    Preserve raw totals; normalize display-only roundoff around half-unit ties.
    Both FX legs count. The cash adjustment is bookkeeping, not another trade.
    """
    base_rate = market.value(1, plan.base_currency)
    value = lambda amount, currency: market.value(amount, currency) / base_rate
    costs = [*(value(t.shares * t.price, t.currency) for t in plan.trades),
             *(value(t.amount, t.currency) for t in plan.manual_trades)]
    purchases = [*(cost for cost in costs if cost > 0),
                 *(value(fx.from_amount, fx.from_currency) for fx in plan.conversions)]
    proceeds = [*(-cost for cost in costs if cost < 0),
                *(value(fx.to_amount, fx.to_currency) for fx in plan.conversions)]
    opening = [value(amount, currency) for currency, amount in portfolio.cash.items()]
    ending = [value(amount, currency) for currency, amount in plan.cash.items()]
    cash_change = fsum(opening) - fsum(ending)
    paid = fsum([*purchases, max(0., -cash_change)])
    received = fsum([*proceeds, max(0., cash_change)])
    roundoff = accounting_tolerance((*purchases, *proceeds, *opening, *ending, paid, received))
    return TransactionLedger(fsum(purchases), fsum(proceeds), cash_change, paid, received,
                             abs(paid - received) <= roundoff)


def format_plan(plan, targets):
    manual_endings = {holding.currency: holding.amount for holding in plan.final_manual_holdings}
    lines = [f"Planned wealth: {format_amount(plan.wealth)} {plan.base_currency}",
             "Execute sells, then currency conversions, then buys (estimated prices)."]
    for action, trades in (("SELL", [t for t in plan.trades if t.shares < 0]),
                           ("BUY", [t for t in plan.trades if t.shares > 0])):
        if action == "BUY":
            for fx in plan.conversions:
                lines.append(f"FX    {format_amount(fx.from_amount)} {fx.from_currency} -> "
                             f"{format_amount(fx.to_amount)} {fx.to_currency}")
        for trade in trades:
            lines.append(f"{action:4}  {format_amount(abs(trade.shares)):>6} {trade.ticker:12} "
                         f"@ {format_amount(trade.price, 0 if trade.currency == 'HUF' else 2)} {trade.currency}; "
                         f"ending shares {format_amount(plan.final_shares[trade.ticker])}")
        for trade in plan.manual_trades:
            if (trade.amount > 0) == (action == 'BUY'):
                ending = manual_endings[trade.currency]
                amount = abs(Decimal(str(trade.amount)))
                ending = Decimal(str(ending))
                lines.append(f"{action:4}  {format_amount(amount)} {trade.currency} {TREASURY} "
                             f"(manual asset); ending value {format_amount(ending)} {trade.currency}")
    if not (plan.trades or plan.manual_trades or plan.conversions):
        lines.append("No trades.")
    lines.append("\nAllocation basis: total wealth, including cash")
    lines.extend(_allocation_lines(plan, targets))
    lines.append("\nCash balances:")
    lines.extend(f"  {format_amount(amount)} {currency}" for currency, amount in plan.cash.items()
                 if amount or targets.weights[CASH].get(currency, 0))
    return "\n".join(lines)


def _allocation_lines(plan, targets):
    lines = [f"\nCategory / child          End ({plan.base_currency})          End       Target"]
    for parent, target in targets.totals.items():
        weights = targets.weights[parent]
        values = {name: plan.allocation_values.get((parent, name), 0.)
                  for branch, name in plan.allocation_values if branch == parent}
        value = fsum(values.values())
        if not any(values.values()) and not target:
            continue
        actual = 100 * value / plan.wealth if plan.wealth else 0.
        lines.append(f"{parent:24} {format_amount(value):>12} {format_percent(actual):>12} {format_percent(target):>12}")
        for child in sorted(set(values) | set(weights),
                            key=lambda name: (-weights.get(name, 0), name)):
            amount = values.get(child, 0.)
            actual = 100 * amount / plan.wealth if plan.wealth else 0.
            weight = weights.get(child, 0.)
            if not amount and not weight:
                continue
            lines.append(f"  {child:22} {format_amount(amount):>12} {format_percent(actual):>12} {format_percent(weight):>12}")
    return lines
