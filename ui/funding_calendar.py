"""One monthly view of scheduled cash flow and the calculator's savings assignments."""

from datetime import date
from math import fsum
import tkinter as tk
from tkinter import ttk

from portfolio.calculator import funding_calendar, month_after
from ui.dates import MonthEntry
from ui.help import HELP, info_label
from ui.reporting import format_amount, format_percent
from ui.sorting import TreeSort
from ui.theme import ALTERNATE_BACKGROUND
from ui.widgets import AutoScrollbar


class FundingCalendar(ttk.Frame):
    def __init__(self, parent, *, month, currency, on_change, on_select):
        super().__init__(parent)
        self._month, self._currency = month, currency
        self._on_select = on_select
        self._months = tk.StringVar(self, value='12')
        self._values, self._totals, self._expense_rows, self._budget_details = {}, {}, {}, {}
        self._opened, self._selected = {month.get(): True}, month.get()
        self._result, self._conversion = None, 1
        self.detail = tk.StringVar(self)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        controls = ttk.Frame(self)
        controls.grid(row=0, column=0, sticky='ew', pady=(0, 8))
        info_label(controls, 'Monthly funding calendar', HELP['funding_calendar']).pack(side='left', padx=(0, 18))
        ttk.Label(controls, text='From').pack(side='left', padx=(0, 5))
        self.month_entry = MonthEntry(controls, textvariable=month)
        self.month_entry.pack(side='left')
        ttk.Label(controls, text='Months').pack(side='left', padx=(14, 5))
        ttk.Combobox(controls, textvariable=self._months, values=('12', '24', '60', '120'),
                     width=4, state='readonly').pack(side='left')
        ttk.Label(controls, textvariable=currency).pack(side='right')
        columns = ('income', 'outgoings', 'net', 'ratio', 'savings', 'committed', 'unassigned')
        self.tree = ttk.Treeview(self, columns=columns, show='tree headings', style='Calculator.Treeview')
        for key, title, width in (('#0', 'Month / detail', 235), ('income', 'Income', 88),
                                   ('outgoings', 'Outgoings', 88), ('net', 'Net', 88),
                                   ('ratio', 'Saving ratio', 88), ('savings', 'Savings', 88),
                                   ('committed', 'Committed', 92), ('unassigned', 'Unassigned', 92)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=width, anchor='w' if key == '#0' else 'e')
        self.tree.grid(row=1, column=0, sticky='nsew')
        for orient, command, row, column, sticky in (('vertical', self.tree.yview, 1, 1, 'ns'),
                                                    ('horizontal', self.tree.xview, 2, 0, 'ew')):
            scrollbar = AutoScrollbar(self, orient=orient, command=command)
            scrollbar.grid(row=row, column=column, sticky=sticky)
            self.tree.configure(**{f'{"y" if orient == "vertical" else "x"}scrollcommand': scrollbar.set})
        self.tree.tag_configure('alternate', background=ALTERNATE_BACKGROUND)
        self._sorting = TreeSort(self.tree, numeric_columns=columns,
                                 value=lambda item, key: self._values[item].get(key), on_sort=self._shade)
        self.tree.bind('<<TreeviewSelect>>', self._select)
        self._detail_label = ttk.Label(self, textvariable=self.detail, wraplength=900, padding=(4, 8))
        self._detail_label.grid(row=3, column=0, sticky='ew')
        self._detail_label.grid_remove()
        self.bind('<Configure>', lambda event: self._detail_label.configure(wraplength=max(200, event.width - 16)))
        self._months.trace_add('write', lambda *_: on_change())

    @property
    def start_month(self):
        return MonthEntry.validate_month(self._month.get())

    @property
    def end_month(self):
        end = month_after(self.start_month, int(self._months.get()) - 1)
        if end is None:
            raise ValueError('The calendar range extends beyond the supported dates.')
        return end

    def clear(self):
        if self.tree.get_children():
            pending = list(self.tree.get_children())
            while pending:
                item = pending.pop()
                children = self.tree.get_children(item)
                if children:
                    self._opened[item] = self.tree.item(item, 'open')
                    pending.extend(children)
            self._selected = next(iter(self.tree.selection()), self._selected)
        self.tree.delete(*self.tree.get_children())
        self._values.clear()
        self._totals.clear()
        self._expense_rows.clear()
        self._budget_details.clear()
        self._result = None
        self.detail.set('')
        self._detail_label.grid_remove()

    def show_cashflows(self, settings, rates, conversion):
        """Keep actual monthly totals readable while quotes or returns are unavailable."""
        self.clear()
        self._conversion = conversion
        self.tree.heading('committed', text='Committed')
        day, end = self.start_month, self.end_month
        while day <= end:
            self._month_row(day, settings.monthly_totals(day.year, day.month, rates))
            day = month_after(day, 1)
            if day is None:
                break
        self._restore()

    def show_recommendation(self, settings, result, rates, conversion):
        self.clear()
        self._result, self._conversion = result, conversion
        self.tree.heading('committed', text='Planned' if result.shortfall else 'Committed')
        start, end = self.start_month, self.end_month
        rows = {row.day: row for row in funding_calendar(settings, date.today(), result, rates)}
        day = start
        while day <= end:
            row = rows.get(day)
            totals = row.totals if row else settings.monthly_totals(day.year, day.month, rates)
            item = self._month_row(day, totals, row.assignment if row else None, resolved=True)
            if row:
                self._funding_rows(item, row)
            day = month_after(day, 1)
            if day is None:
                break
        self._restore()

    def _month_row(self, day, totals, assignment=None, *, resolved=False):
        item = day.isoformat()[:7]
        self._totals[item] = totals
        current = day <= date.today().replace(day=1)
        title = item + (' · already held' if current else '')
        values = dict(income=totals.income, outgoings=totals.outgoings, net=totals.net,
                      ratio=totals.saving_percent)
        if resolved:
            values.update(savings=assignment.available if assignment else 0.,
                          committed=assignment.buffer_reserved + fsum(amount for _, amount in assignment.expenses)
                          if assignment else 0., unassigned=assignment.uncommitted if assignment else 0.)
        self._insert('', item, title, **values)
        scheduled = item + '/scheduled'
        self._insert(item, scheduled, 'Scheduled cash flow')
        self._insert(scheduled, item + '/scheduled/recurring', 'Recurring', income=totals.recurring_income,
                     outgoings=totals.recurring_outgoings, net=totals.recurring_income - totals.recurring_outgoings,
                     ratio=totals.saving_percent)
        self._insert(scheduled, item + '/scheduled/once', 'Once', income=totals.single_income, outgoings=totals.single_outgoings,
                     net=totals.single_income - totals.single_outgoings)
        return item

    def _funding_rows(self, parent, row):
        if row.day > date.today():
            paid = fsum(covered for _, _, covered in row.budget.obligations)
            money = lambda value: format_amount(value / self._conversion)
            available = row.assignment.available if row.assignment else 0.
            self._budget_details[parent] = (
                f'Savings budget: max(0, income {money(row.budget.gross_income)} − living costs '
                f'{money(row.budget.living_costs)}) − linked payments covered {money(paid)} '
                f'= {money(available)} {self._currency.get()}. '
                'Ongoing annual costs are spread over 12 months; Scheduled net uses actual payment months.')
            budget = parent + '/budget'
            self._insert(parent, budget, 'Savings budget', income=row.budget.gross_income,
                         outgoings=row.budget.living_costs + paid, savings=available)
            self._insert(budget, parent + '/living', 'Ongoing living costs',
                         outgoings=row.budget.living_costs)
            for number, (name, amount, covered) in enumerate(row.budget.obligations):
                item = f'{parent}/obligation/{number}'
                self._insert(budget, item, f'Paid from income · {name}', outgoings=covered)
                self._budget_details[item] = (f'{name}: required {money(amount)} · covered from income '
                                             f'{money(covered)} · remaining for reserves {money(amount - covered)} '
                                             f'{self._currency.get()}.')
            committed = row.assignment.available - row.assignment.uncommitted if row.assignment else 0.
            allocation = parent + '/allocation'
            self._insert(parent, allocation, 'Savings allocation', savings=available, committed=committed,
                         unassigned=row.assignment.uncommitted if row.assignment else 0.)
            if row.assignment and row.assignment.buffer_reserved:
                self._insert(allocation, parent + '/buffer', 'Fixed-buffer additions',
                             committed=row.assignment.buffer_reserved)
            for index, amount in row.assignment.expenses if row.assignment else ():
                expense = self._result.expenses[index]
                item = f'{parent}/expense/{index}'
                self._insert(allocation, item, f'{expense.payment.name} · by {expense.deadline:%Y-%m}', committed=amount)
                self._expense_rows[item] = index
        for kind, indices in (('Cash ready', row.deadlines), ('Payment due', row.due_expenses)):
            for index in indices:
                expense = self._result.expenses[index]
                item = f'{parent}/{kind}/{index}'
                original = expense.deadline if kind == 'Cash ready' else expense.payment.due
                label = ('Cash-ready deadline passed' if kind == 'Cash ready' else 'Overdue') if original < row.day else kind
                self._insert(parent, item, f'{label} · {expense.payment.name} · {original:%Y-%m}')
                self._expense_rows[item] = index

    def _insert(self, parent, item, label, **values):
        self._values[item] = {'#0': label, **values}
        displayed = []
        for key in self.tree['columns']:
            value = values.get(key)
            displayed.append('—' if value is None and key == 'ratio' and key in values else '' if value is None else
                             format_percent(value) if key == 'ratio' else format_amount(value / self._conversion))
        self.tree.insert(parent, 'end', iid=item, text=label, values=displayed, open=self._opened.get(item, '/' in item))

    def _restore(self):
        self._sorting.apply()
        if self._selected and self.tree.exists(self._selected):
            self.tree.selection_set(self._selected)
            self.tree.focus(self._selected)
            self._select()

    def _select(self, _event=None):
        item = next(iter(self.tree.selection()), '')
        if not item:
            return
        month = item[:7]
        if month in self._totals:
            self._on_select(MonthEntry.validate_month(month), self._totals[month])
        index = self._expense_rows.get(item)
        if self._result is None or index is None:
            self.detail.set(self._budget_details.get(item, self._budget_details.get(month, '')))
            self._detail_label.grid() if self.detail.get() else self._detail_label.grid_remove()
            return
        evidence = self._result.expenses[index]
        payment = evidence.payment
        allocation = self._result.rows[index + 2]
        amount = lambda value: format_amount(value / self._conversion)
        principal = fsum(value for _, value in evidence.contributions)
        self.detail.set(f'{payment.name} · cash ready {evidence.deadline:%Y-%m} · payment {payment.due:%Y-%m}\n'
                        f'Reserved today ({self._currency.get()}): Cash {amount(allocation.cash)} · MÁK HUF {amount(allocation.mak)} · '
                        f'Stock market {amount(allocation.stock)}\n'
                        f'Future savings ({self._currency.get()}): principal {amount(principal)} + return {amount(evidence.income_funded - principal)} '
                        f'= {amount(evidence.income_funded)} at the cash deadline; '
                        f'today’s reserve credit {amount(evidence.released_reserve)} {self._currency.get()}.')
        self._detail_label.grid()

    def _shade(self):
        for index, item in enumerate(self.tree.get_children()):
            tags = ('alternate',) if index % 2 else ()
            self.tree.item(item, tags=tags)
            pending = list(self.tree.get_children(item))
            while pending:
                child = pending.pop()
                self.tree.item(child, tags=tags)
                pending.extend(self.tree.get_children(child))
