"""Read-only native tables for a plan and the inputs used to create it."""

from decimal import Decimal
from math import fsum
import tkinter as tk
from tkinter import ttk

from portfolio.models import CASH, TREASURY
from ui.reporting import current_values, format_amount
from ui.widgets import AutoScrollbar


class PlanView(ttk.Frame):
    """Show executable transactions, allocations, and native currency balances."""

    def __init__(self, parent):
        super().__init__(parent)
        self.status = tk.StringVar()
        ttk.Label(self, textvariable=self.status, wraplength=1000).pack(fill='x', pady=(0, 8))
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill='both', expand=True)
        self.transactions = self._table('Transactions', (
            ('action', 'Action', 65), ('asset', 'Asset', 125), ('quantity', 'Quantity', 100),
            ('unit', 'Unit', 65), ('price', 'Unit price', 125), ('paid', 'Paid', 135),
            ('received', 'Received', 135), ('ending', 'Ending holding', 140)))
        self.allocations = self._table('Allocations', (
            ('current', 'Current value', 130), ('planned', 'Planned value', 130),
            ('current_percent', 'Current %', 100), ('planned_percent', 'Planned %', 100),
            ('target', 'Target total %', 105), ('local_target', 'Within parent %', 120)), hierarchy=True)
        self.cash = self._table('Cash', (
            ('currency', 'Currency', 100), ('current', 'Current amount', 150),
            ('planned', 'Planned amount', 150), ('change', 'Change', 150),
            ('target', 'Within Cash %', 130)))

    def clear(self, message=''):
        for tree in (self.transactions, self.allocations, self.cash):
            tree.delete(*tree.get_children())
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
        for tree in (self.transactions, self.allocations, self.cash):
            tree.tag_configure('alternate', background='#e8edf2')
            for index, item in enumerate(tree.get_children()):
                tree.item(item, tags=('alternate',) if index % 2 else ())
        self.status.set(f'{message}\n{context}' if context else message)

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
        return tree

    def _show_transactions(self, plan):
        endings = {holding.currency: holding.amount for holding in plan.final_manual_holdings}
        for buying in (False, True):
            if buying:
                for conversion in plan.conversions:
                    if conversion.from_amount or conversion.to_amount:
                        self.transactions.insert('', 'end', values=(
                            'FX', f'{conversion.from_currency} → {conversion.to_currency}', '', '', '',
                            _money(conversion.from_amount, conversion.from_currency),
                            _money(conversion.to_amount, conversion.to_currency), ''))
            for trade in plan.trades:
                if trade.shares and (trade.shares > 0) == buying:
                    self._transaction(buying, trade.ticker, str(abs(trade.shares)), 'shares',
                                      f'{format_amount(trade.price, 4)} {trade.currency}',
                                      abs(trade.shares) * trade.price, trade.currency,
                                      f'{plan.final_shares[trade.ticker]} shares')
            for trade in plan.manual_trades:
                if trade.amount and (trade.amount > 0) == buying:
                    amount = abs(Decimal(str(trade.amount)))
                    ending = Decimal(str(endings[trade.currency]))
                    self._transaction(buying, TREASURY, format_amount(amount), trade.currency, '', amount,
                                      trade.currency, _money(ending, trade.currency))

    def _transaction(self, buying, asset, quantity, unit, price, amount, currency, ending):
        money = _money(amount, currency)
        self.transactions.insert('', 'end', values=(
            'BUY' if buying else 'SELL', asset, quantity, unit, price,
            money if buying else '', '' if buying else money, ending))

    def _show_allocations(self, plan, portfolio, current):
        for key, name in (('current', 'Current'), ('planned', 'Planned')):
            self.allocations.heading(key, text=f'{name} ({plan.base_currency})')
        wealth = fsum(current.values())
        targets = portfolio.allocation
        planned = plan.allocation_values
        for parent in sorted(targets.top, key=lambda name: (-targets.top[name], name)):
            names = {name for branch, name in current.keys() | planned.keys() if branch == parent}
            names.update(targets.children[parent])
            children = []
            for name in sorted(names, key=lambda name: (-targets.children[parent].get(name, 0), name)):
                local = targets.children[parent].get(name, 0)
                values = (current.get((parent, name), 0), planned.get((parent, name), 0),
                          targets.top[parent] * local / 100, local)
                if any(values):
                    children.append((name, values))
            before = fsum(values[0] for _, values in children)
            after = fsum(values[1] for _, values in children)
            if children or before or after or targets.top[parent]:
                root = self._allocation('', parent, before, after, targets.top[parent], None, wealth, plan.wealth)
                for name, (before, after, target, local) in children:
                    self._allocation(root, name, before, after, target, local, wealth, plan.wealth)

    def _allocation(self, parent, name, before, after, target, local, wealth, planned_wealth):
        return self.allocations.insert(parent, 'end', text=name, open=True, values=(
            format_amount(before), format_amount(after), format_amount(100 * before / wealth if wealth else 0),
            format_amount(100 * after / planned_wealth if planned_wealth else 0),
            format_amount(target), '' if local is None else format_amount(local)))

    def _show_cash(self, plan, portfolio):
        targets = portfolio.allocation.children[CASH]
        for currency in sorted(portfolio.cash.keys() | plan.cash.keys() | targets.keys()):
            before, after = portfolio.cash.get(currency, 0), plan.cash.get(currency, 0)
            target = targets.get(currency, 0)
            if before or after or target:
                self.cash.insert('', 'end', values=(currency, format_amount(before), format_amount(after),
                                                   format_amount(after - before), format_amount(target)))


def _money(value, currency):
    return f'{format_amount(value)} {currency}'
