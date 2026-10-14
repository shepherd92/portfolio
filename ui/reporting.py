"""Human-readable recommendations shared by the GUI and command line."""

from collections import defaultdict
from decimal import Decimal
from math import fsum

from portfolio.models import CASH, STOCK_MARKET, TREASURY


def format_number(value, spec=',.2f'):
    """Group numeric display text with narrow nonbreaking spaces."""
    return format(value, spec).replace(',', '\u202f')


def format_amount(value, places=2):
    """Keep tiny nonzero execution amounts visible in both tables and exports."""
    text = format_number(value, f',.{places}f')
    return f'{value:.6g}' if value and Decimal(text.replace('\u202f', '')) == 0 else text


def current_values(holdings, manual_holdings, cash, market, *, parent=None):
    """Value holdings by allocation leaf, without requiring valid target ratios.

    A selected parent only prices that branch; zero positions need no quote
    or exchange rate. Signed cash balances remain signed.
    """
    if parent not in (None, STOCK_MARKET, TREASURY, CASH):
        raise ValueError(f'Unknown allocation category: {parent!r}')
    parts = defaultdict(list)
    if parent in (None, STOCK_MARKET):
        for holding in holdings:
            value = holding.shares * market.price(holding.ticker) if holding.shares else 0.
            parts[STOCK_MARKET, holding.group].append(value)
    if parent in (None, TREASURY):
        for holding in manual_holdings:
            value = market.value(holding.amount, holding.currency) if holding.amount else 0.
            parts[TREASURY, holding.currency].append(value)
    if parent in (None, CASH):
        for currency, amount in cash.items():
            parts[CASH, currency].append(market.value(amount, currency) if amount else 0.)
    return {key: fsum(values) for key, values in parts.items()}


def format_plan(plan, targets):
    manual_endings = {holding.currency: holding.amount for holding in plan.final_manual_holdings}
    lines = [f"Planned wealth: {format_number(plan.wealth)} {plan.base_currency}",
             "Execute sells, then currency conversions, then buys (estimated prices)."]
    for action, trades in (("SELL", [t for t in plan.trades if t.shares < 0]),
                           ("BUY", [t for t in plan.trades if t.shares > 0])):
        if action == "BUY":
            for fx in plan.conversions:
                lines.append(f"FX    {format_amount(fx.from_amount)} {fx.from_currency} -> "
                             f"{format_amount(fx.to_amount)} {fx.to_currency}")
        for trade in trades:
            lines.append(f"{action:4}  {abs(trade.shares):6d} {trade.ticker:12} "
                         f"@ {format_amount(trade.price, 4)} {trade.currency}; "
                         f"ending shares {plan.final_shares[trade.ticker]}")
        for trade in plan.manual_trades:
            if (trade.amount > 0) == (action == 'BUY'):
                ending = manual_endings[trade.currency]
                amount = abs(Decimal(str(trade.amount)))
                ending = Decimal(str(ending))
                lines.append(f"{action:4}  {format_number(amount)} {trade.currency} {TREASURY} "
                             f"(manual asset); ending value {format_number(ending)} {trade.currency}")
    if not (plan.trades or plan.manual_trades or plan.conversions):
        lines.append("No trades.")
    lines.append("\nAllocation basis: total wealth, including cash")
    lines.extend(_allocation_lines(plan, targets))
    lines.append("\nCash balances:")
    lines.extend(f"  {format_number(amount)} {currency}" for currency, amount in plan.cash.items()
                 if amount or targets.children[CASH].get(currency, 0))
    return "\n".join(lines)


def _allocation_lines(plan, targets):
    lines = ["\nCategory / child             Value    Total %   Target %   Within target %"]
    for parent, target in targets.top.items():
        values = {name: plan.allocation_values.get((parent, name), 0.)
                  for branch, name in plan.allocation_values if branch == parent}
        value = fsum(values.values())
        if not value and not target and not any(targets.children[parent].values()):
            continue
        actual = 100 * value / plan.wealth if plan.wealth else 0.
        lines.append(f"{parent:24} {format_number(value, '12,.2f')} {actual:9.2f}% {target:9.2f}%")
        for child in sorted(set(values) | set(targets.children[parent]),
                            key=lambda name: (-targets.children[parent].get(name, 0), name)):
            amount = values.get(child, 0.)
            actual = 100 * amount / plan.wealth if plan.wealth else 0.
            local = targets.children[parent].get(child, 0.)
            effective = target * local / 100
            if not amount and not local:
                continue
            lines.append(f"  {child:22} {format_number(amount, '12,.2f')} {actual:9.2f}% {effective:9.2f}% {local:17.2f}%")
    return lines
