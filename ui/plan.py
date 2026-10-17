"""Read-only native tables for a plan and the inputs used to create it."""

from decimal import Decimal
from math import fsum
import tkinter as tk
from tkinter import font, ttk

from portfolio.models import CASH, TREASURY, current_values
from ui.calculation_view import CalculationView
from ui.reporting import format_amount, format_percent, transaction_ledger
from ui.links import open_cell_link, quote_url
from ui.sorting import TreeSort
from ui.theme import ALTERNATE_BACKGROUND, MUTED_TEXT, ROW_BACKGROUND, TEXT
from ui.widgets import AutoScrollbar
from ui.help import HELP, info_label


class PlanView(ttk.Frame):
    """Show executable transactions, balances, and their calculation walkthrough."""

    def __init__(self, parent):
        super().__init__(parent)
        self._values, self._sorters = {}, {}
        self._total_font = font.Font(self, font=ttk.Style(self).lookup('Treeview', 'font') or 'TkDefaultFont')
        self._total_font.configure(weight='bold')
        self.status = tk.StringVar()
        self._empty_labels = []
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.notebook = ttk.Notebook(self, style='Portfolio.TNotebook')
        self.notebook.grid(row=0, column=0, sticky='nsew')
        self.summary = ttk.Label(self, wraplength=1000)
        self.controls = ttk.Frame(self)
        self.controls.grid(row=2, column=0, sticky='ew', pady=(8, 0))
        self.table_help = info_label(self.controls, 'How to read this table', HELP['transactions'])
        self.table_help.pack(side='left')
        self.notebook.bind('<<NotebookTabChanged>>', self._update_help)
        self.transactions = self._table('Transactions', (
            ('action', 'Action', 65), ('asset', 'Asset', 170), ('quantity', 'Quantity', 100),
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
        self.calculations = self._calculation_page()

    def clear(self, message=''):
        for tree in (self.transactions, self.allocations, self.cash):
            tree.delete(*tree.get_children())
        self._values.clear()
        self.calculations.clear()
        self.status.set(message)
        self.summary.grid_remove()
        for label in self._empty_labels:
            label.place(relx=.5, rely=.5, anchor='center')

    def show_plan(self, plan, portfolio, market, context='', *, calculations=None, warning=''):
        self.clear()
        for label in self._empty_labels:
            label.place_forget()
        self.calculations.show(calculations, context=context)
        self._show_transactions(plan, portfolio, market)
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
        if warning:
            message += f' · {warning}'
        self.status.set(f'{message}\n{context}' if context else message)
        self.summary.configure(text=message)
        self.summary.grid(row=1, column=0, sticky='ew', pady=(8, 0))

    def _update_help(self, _event=None):
        keys = ('transactions', 'allocations', 'plan_cash', 'plan_calculations')
        index = self.notebook.index('current')
        self.table_help.tooltip.hide()
        self.table_help.tooltip.text = HELP[keys[index]]
        self.table_help.label.configure(text='About these calculations' if index == 3 else 'How to read this table')

    def _double_click(self, event):
        tree = self.transactions
        if open_cell_link(event, tree, {'asset': lambda item: quote_url(tree.set(item, 'asset'))
                                       if tree.set(item, 'unit') == 'shares' else None}):
            return 'break'

    def _table(self, title, columns, *, hierarchy=False):
        frame = ttk.Frame(self.notebook)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        self.notebook.add(frame, text=title)
        tree = ttk.Treeview(frame, columns=[key for key, _, _ in columns],
                            show='tree headings' if hierarchy else 'headings', selectmode='browse')
        tree.heading('#0', text='Allocation')
        tree.column('#0', width=175, minwidth=110)
        for key, label, width in columns:
            tree.heading(key, text=label)
            tree.column(key, width=width, minwidth=60,
                        anchor='w' if key in {'action', 'asset', 'unit', 'currency'} else 'e')
        tree.grid(row=1, column=0, sticky='nsew')
        vertical = AutoScrollbar(frame, orient='vertical', command=tree.yview)
        horizontal = AutoScrollbar(frame, orient='horizontal', command=tree.xview)
        vertical.grid(row=1, column=1, sticky='ns')
        horizontal.grid(row=2, column=0, sticky='ew')
        rule = tk.Frame(tree, background=TEXT, height=2) if title == 'Transactions' else None
        def scroll_vertical(first, last):
            vertical.set(first, last)
            if rule is not None:
                _position_total_rule(tree, rule)
        tree.configure(yscrollcommand=scroll_vertical, xscrollcommand=horizontal.set)
        self._empty_state(tree)
        if rule is not None:
            self._transaction_rule = rule
            tree.bind('<Configure>', lambda _event: _position_total_rule(tree, rule))
        tree.tag_configure('alternate', background=ALTERNATE_BACKGROUND)
        tree.tag_configure('total', font=self._total_font)
        self._sorters[tree] = TreeSort(
            tree, numeric_columns=[key for key, _, _ in columns
                                   if key not in {'action', 'asset', 'unit', 'currency'}],
            value=lambda item, column: self._values[tree, item].get(column, tree.item(item, 'text')),
            on_sort=lambda: _shade_rows(tree, rule=rule))
        return tree

    def _calculation_page(self):
        page = CalculationView(self.notebook)
        self.notebook.add(page, text='Calculations')
        self._empty_state(page.canvas)
        return page

    def _empty_state(self, parent):
        label = tk.Label(parent, textvariable=self.status, background=ROW_BACKGROUND,
                         foreground=MUTED_TEXT, font=('DejaVu Sans', 10), wraplength=460)
        self._empty_labels.append(label)

    def _insert(self, tree, values, raw, *, parent='', **options):
        item = tree.insert(parent, 'end', values=values, **options)
        self._values[tree, item] = dict(zip(tree['columns'], raw))
        return item

    def _show_transactions(self, plan, portfolio, market):
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
                    amount = abs(trade.shares) * trade.price
                    self._transaction(buying, trade.ticker, abs(trade.shares), 'shares', trade.price,
                                      amount, trade.currency, plan.final_shares[trade.ticker])
            for trade in plan.manual_trades:
                if trade.amount and (trade.amount > 0) == buying:
                    amount = abs(Decimal(str(trade.amount)))
                    ending = Decimal(str(endings[trade.currency]))
                    self._transaction(buying, TREASURY, amount, trade.currency, None, amount,
                                      trade.currency, ending)
        if not self.transactions.get_children():
            return
        ledger = transaction_ledger(plan, portfolio, market)
        cash_change = ledger.cash_change
        if cash_change:
            buying = cash_change < 0
            amount = abs(ledger.display_cash_change)
            if format_amount(amount) != '0':
                asset = 'Cash balance retained' if buying else 'Cash balance used'
                self._insert(self.transactions, (
                    'CASH', asset, '', '', '', _money(amount, plan.base_currency) if buying else '',
                    '' if buying else _money(amount, plan.base_currency), ''), (
                    'CASH', asset, None, '', None, abs(cash_change) if buying else None,
                    None if buying else abs(cash_change), None))
        self._insert(self.transactions, (
            'TOTAL', plan.base_currency, '', '', '', _money(ledger.display_paid, plan.base_currency),
            _money(ledger.display_received, plan.base_currency), ''),
            ('TOTAL', plan.base_currency, None, '', None, ledger.paid, ledger.received, None), tags=('total',))

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
            before = fsum(current.get((parent, name), 0) for name in names)
            after = fsum(planned.get((parent, name), 0) for name in names)
            for name in sorted(names, key=lambda name: (-weights.get(name, 0), name)):
                values = (current.get((parent, name), 0), planned.get((parent, name), 0),
                          weights.get(name, 0))
                if _visible_allocation(*values, wealth, plan.wealth):
                    children.append((name, values))
            if children or _visible_allocation(before, after, totals[parent], wealth, plan.wealth):
                root = self._allocation('', parent, before, after, totals[parent], wealth, plan.wealth)
                for name, (before, after, target) in children:
                    self._allocation(root, name, before, after, target, wealth, plan.wealth)
        end_value = fsum(planned.values())
        self._insert(self.allocations, (format_amount(wealth), format_amount(end_value), '', '', ''),
                     (wealth, end_value, None, None, None), text='Total', tags=('total',))

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


def _visible_allocation(before, after, target, wealth, planned_wealth):
    """Hide display-only zeroes while retaining every requested allocation target."""
    return (bool(target) or format_amount(before) != '0' or format_amount(after) != '0'
            or format_percent(100 * before / wealth if wealth else 0) != '0%'
            or format_percent(100 * after / planned_wealth if planned_wealth else 0) != '0%')


def _shade_rows(tree, parent='', rule=None):
    rows = []
    for item in tree.get_children(parent):
        if 'total' in tree.item(item, 'tags'):
            tree.move(item, parent, 'end')
        else:
            rows.append(item)
    for index, item in enumerate(rows):
        tags = tuple(tag for tag in tree.item(item, 'tags') if tag != 'alternate')
        tree.item(item, tags=tags + (('alternate',) if index % 2 else ()))
        _shade_rows(tree, item)
    if rule is not None:
        _position_total_rule(tree, rule)


def _position_total_rule(tree, rule):
    totals = [item for item in tree.get_children() if 'total' in tree.item(item, 'tags')]
    bounds = tree.bbox(totals[-1]) if totals else ()
    if bounds and 0 <= bounds[1] < tree.winfo_height():
        rule.place(x=0, y=bounds[1], relwidth=1)
        rule.lift()
    else:
        rule.place_forget()
