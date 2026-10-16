"""Read-only native tables for a plan and the inputs used to create it."""

from decimal import Decimal
from math import fsum
import tkinter as tk
from tkinter import ttk

from portfolio.models import CASH, TREASURY, current_values
from ui.reporting import format_amount, format_percent
from ui.links import open_cell_link, quote_url
from ui.sorting import TreeSort
from ui.theme import ALTERNATE_BACKGROUND
from ui.widgets import AutoScrollbar


class PlanView(ttk.Frame):
    """Show executable transactions, allocations, and native currency balances."""

    def __init__(self, parent):
        super().__init__(parent)
        self._values, self._sorters = {}, {}
        self.status = tk.StringVar()
        ttk.Label(self, textvariable=self.status, wraplength=1000).pack(fill='x', pady=(0, 8))
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill='both', expand=True)
        self.transactions = self._table('Transactions', (
            ('action', 'Action', 65), ('asset', 'Asset', 125), ('quantity', 'Quantity', 100),
            ('unit', 'Unit', 65), ('price', 'Unit price', 125), ('paid', 'Paid', 135),
            ('received', 'Received', 135), ('ending', 'Ending holding', 140)))
        self.transactions.bind('<Double-1>', self._double_click)
        self.allocations = self._table('Allocations', (
            ('current', 'Current value', 130), ('planned', 'End value', 130),
            ('current_percent', 'Current', 100), ('planned_percent', 'End', 100),
            ('target', 'Target', 105)), hierarchy=True)
        self.cash = self._table('Cash', (
            ('currency', 'Currency', 100), ('current', 'Current amount', 150),
            ('planned', 'End amount', 150), ('change', 'Change', 150),
            ('target', 'Target', 130)))

    def clear(self, message=''):
        for tree in (self.transactions, self.allocations, self.cash):
            tree.delete(*tree.get_children())
        self._values.clear()
        self.status.set(message)

    def show_plan(self, plan, portfolio, market, context=''):
        self.clear()
        self._show_transactions(plan)
        current = current_values(portfolio.holdings, portfolio.manual_holdings, portfolio.cash, market)
        self._show_allocations(plan, portfolio, current)
        self._show_cash(plan, portfolio)
        message = f'Planned wealth: {_money(plan.wealth, plan.base_currency)}'
        if not self.transactions.get_children():
            message += ' · No transactions.'
        else:
            actions = [self.transactions.set(item, 'action') for item in self.transactions.get_children()]
            message += ' · ' + ' · '.join(f'{label}: {actions.count(action)}' for action, label in
                                        (('SELL', 'Sales'), ('FX', 'Conversions'), ('BUY', 'Purchases')))
        for sorter in self._sorters.values():
            sorter.apply()
        self.status.set(f'{message}\n{context}' if context else message)

    def _double_click(self, event):
        tree = self.transactions
        if open_cell_link(event, tree, {'asset': lambda item: quote_url(tree.set(item, 'asset'))
                                       if tree.set(item, 'unit') == 'shares' else None}):
            return 'break'

    def _table(self, title, columns, *, hierarchy=False):
        frame = ttk.Frame(self.notebook)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.notebook.add(frame, text=title)
        tree = ttk.Treeview(frame, columns=[key for key, _, _ in columns],
                            show='tree headings' if hierarchy else 'headings', selectmode='browse')
        tree.heading('#0', text='Allocation')
        tree.column('#0', width=175, minwidth=110)
        for key, label, width in columns:
            tree.heading(key, text=label)
            tree.column(key, width=width, minwidth=60,
                        anchor='w' if key in {'action', 'asset', 'unit', 'currency'} else 'e')
        tree.grid(row=0, column=0, sticky='nsew')
        vertical = AutoScrollbar(frame, orient='vertical', command=tree.yview)
        horizontal = AutoScrollbar(frame, orient='horizontal', command=tree.xview)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        tree.tag_configure('alternate', background=ALTERNATE_BACKGROUND)
        self._sorters[tree] = TreeSort(
            tree, numeric_columns=[key for key, _, _ in columns
                                   if key not in {'action', 'asset', 'unit', 'currency'}],
            value=lambda item, column: self._values[tree, item].get(column, tree.item(item, 'text')),
            on_sort=lambda: _shade_rows(tree))
        return tree

    def _insert(self, tree, values, raw, *, parent='', **options):
        item = tree.insert(parent, 'end', values=values, **options)
        self._values[tree, item] = dict(zip(tree['columns'], raw))
        return item

    def _show_transactions(self, plan):
        endings = {holding.currency: holding.amount for holding in plan.final_manual_holdings}
        for buying in (False, True):
            if buying:
                for conversion in plan.conversions:
                    if conversion.from_amount or conversion.to_amount:
                        self._insert(self.transactions, (
                            'FX', f'{conversion.from_currency} → {conversion.to_currency}', '', '', '',
                            _money(conversion.from_amount, conversion.from_currency),
                            _money(conversion.to_amount, conversion.to_currency), ''), (
                            'FX', f'{conversion.from_currency} → {conversion.to_currency}', None, '', None,
                            conversion.from_amount, conversion.to_amount, None))
            for trade in plan.trades:
                if trade.shares and (trade.shares > 0) == buying:
                    self._transaction(buying, trade.ticker, abs(trade.shares), 'shares', trade.price,
                                      abs(trade.shares) * trade.price, trade.currency,
                                      plan.final_shares[trade.ticker])
            for trade in plan.manual_trades:
                if trade.amount and (trade.amount > 0) == buying:
                    amount = abs(Decimal(str(trade.amount)))
                    ending = Decimal(str(endings[trade.currency]))
                    self._transaction(buying, TREASURY, amount, trade.currency, None, amount,
                                      trade.currency, ending)

    def _transaction(self, buying, asset, quantity, unit, price, amount, currency, ending):
        money = _money(amount, currency)
        action = 'BUY' if buying else 'SELL'
        self._insert(self.transactions, (
            action, asset, format_amount(quantity), unit,
            f'{format_amount(price, 0 if currency == "HUF" else 2)} {currency}' if price is not None else '',
            money if buying else '', '' if buying else money, f'{format_amount(ending)} {unit}'), (
            action, asset, quantity, unit, price, amount if buying else None,
            None if buying else amount, ending))

    def _show_allocations(self, plan, portfolio, current):
        for key, name in (('current', 'Current'), ('planned', 'End')):
            self.allocations.heading(key, text=f'{name} ({plan.base_currency})')
        wealth = fsum(current.values())
        targets = portfolio.allocation
        totals = targets.totals
        planned = plan.allocation_values
        for parent in sorted(totals, key=lambda name: (-totals[name], name)):
            weights = targets.weights[parent]
            names = {name for branch, name in current.keys() | planned.keys() if branch == parent}
            names.update(weights)
            children = []
            for name in sorted(names, key=lambda name: (-weights.get(name, 0), name)):
                values = (current.get((parent, name), 0), planned.get((parent, name), 0),
                          weights.get(name, 0))
                if any(values):
                    children.append((name, values))
            before = fsum(values[0] for _, values in children)
            after = fsum(values[1] for _, values in children)
            if children or before or after or totals[parent]:
                root = self._allocation('', parent, before, after, totals[parent], wealth, plan.wealth)
                for name, (before, after, target) in children:
                    self._allocation(root, name, before, after, target, wealth, plan.wealth)

    def _allocation(self, parent, name, before, after, target, wealth, planned_wealth):
        current_percent = 100 * before / wealth if wealth else 0
        planned_percent = 100 * after / planned_wealth if planned_wealth else 0
        return self._insert(self.allocations, (
            format_amount(before), format_amount(after), format_percent(current_percent),
            format_percent(planned_percent), format_percent(target)),
            (before, after, current_percent, planned_percent, target), parent=parent, text=name, open=True)

    def _show_cash(self, plan, portfolio):
        targets = portfolio.allocation.weights[CASH]
        for currency in sorted(portfolio.cash.keys() | plan.cash.keys() | targets.keys()):
            before, after = portfolio.cash.get(currency, 0), plan.cash.get(currency, 0)
            target = targets.get(currency, 0)
            if before or after or target:
                self._insert(self.cash, (currency, format_amount(before), format_amount(after),
                                        format_amount(after - before), format_percent(target)),
                             (currency, before, after, after - before, target))


def _money(value, currency):
    return f'{format_amount(value)} {currency}'


def _shade_rows(tree, parent=''):
    for index, item in enumerate(tree.get_children(parent)):
        tree.item(item, tags=('alternate',) if index % 2 else ())
        _shade_rows(tree, item)
