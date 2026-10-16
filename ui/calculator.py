"""Currency-aware reserve inputs and recommendations for Portfolio’s long-term targets."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from math import ceil, isfinite
from pathlib import Path
import tkinter as tk
from tkinter import font, ttk

from portfolio.calculator import CalculatorSettings, Expense, MonthlyCost, ReturnAssumptions, huf_value, recommend, return_weights
from portfolio.storage import load_settings, save_settings
from portfolio.models import CASH, STOCK_MARKET, TREASURY, currency_code, parse_currency_amount
from ui.reporting import format_amount, format_percent, plain_number
from ui.sorting import TreeSort
from ui.theme import ALTERNATE_BACKGROUND, MUTED_TEXT
from ui.widgets import AutoScrollbar, PercentEntry, RoundedEntry
from ui.table import DataTable


class CalculatorView(ttk.Frame):
    def __init__(self, parent, *, get_total, show_projection, currency, get_allocation,
                 get_rates, currency_choices, on_change):
        super().__init__(parent, padding=12)
        self._get_allocation, self._on_change = get_allocation, on_change
        self._get_rates, self._currency_choices = get_rates, currency_choices
        self._get_total, self._currency = get_total, currency
        self.dirty, self._enabled, self._loading = False, True, False
        self._directory, self._original, self._load_error = None, None, ''
        self._autosave_id, self._save_error = None, ''
        self._base_rate, self._rate_error, self._rate_date = None, '', ''
        self._income_amount, self._income_currency = 0., 'HUF'
        self._income_display_currency = currency.get()
        self._income_edited, self._income_available, self._changing_currency = False, True, False
        self._inputs = {name: tk.StringVar(self) for name in
                        ('monthly_income',
                         'emergency_months', 'cash_months', 'horizon_years', 'equities', 'gold', 'bonds', 'mak')}
        self._auto_rate = tk.BooleanVar(self, value=True)
        self.portfolio_value = tk.StringVar(self)
        self.message = tk.StringVar(self)
        self.monthly_spending, self.monthly_savings = tk.StringVar(self), tk.StringVar(self)
        self.overall_return = tk.StringVar(self)
        self.return_weights = {name: tk.StringVar(self) for name in ('Equities', 'Bonds', 'Gold', TREASURY)}
        self._return_rows, self._return_weight_values = {}, {}
        self._return_sort, self._return_descending = None, False
        self._entries, self._values, self._summary_values = [], {}, {}
        self._build(show_projection)
        self.message.trace_add('write', self._show_message)
        self._set_inputs(CalculatorSettings())
        for name, variable in self._inputs.items():
            variable.trace_add('write', lambda *_, name=name: self._input_changed(name))
        self._auto_rate.trace_add('write', self._rate_mode_changed)
        self.refresh()

    def load(self, directory):
        self._cancel_autosave()
        self._directory = Path(directory)
        self._load_error = self._save_error = ''
        try:
            settings, self._original = load_settings(self._directory)
        except (ValueError, OSError) as error:
            settings, self._original = CalculatorSettings(), None
            self._load_error = f'Cannot load calculator settings: {error}'
            try:
                self._original = (self._directory / 'calculator.json').read_bytes()
            except OSError:
                pass
        self._set_inputs(settings)
        self.dirty = False
        self.refresh()

    def save(self):
        """Flush pending valid edits, including before closing or changing directories."""
        self._cancel_autosave()
        if not self.dirty:
            return True
        try:
            if self._directory is None:
                raise ValueError('Open a portfolio directory before saving calculator settings.')
            self._original = save_settings(self.settings(), self._directory, self._original)
        except (ValueError, OSError) as error:
            self._save_error = f'Cannot save calculator settings: {error}'
            self.refresh()
            return False
        self.dirty = False
        self._load_error = self._save_error = ''
        self.refresh()
        return True

    def destroy(self):
        self._cancel_autosave()
        super().destroy()

    def set_base_rate(self, rate, source_label):
        self._base_rate, self._rate_error = rate, ''
        self._rate_date = source_label
        self._update_auto_rate()
        self.refresh()

    def set_rate_error(self, message):
        self._rate_error = message
        self.refresh()

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        state = 'normal' if enabled else 'disabled'
        for widget in (*self._entries, self.projection_button, self._auto_rate_box):
            widget.configure(state=state)
        self._currency_box.configure(state='readonly' if enabled else 'disabled')
        self._update_income_state()
        self._update_rate_state()
        self.monthly_costs.set_enabled(enabled)
        self.expenses.set_enabled(enabled)

    @property
    def needs_base_rate(self):
        return self._auto_rate.get() and self._base_rate is None

    @property
    def rate_description(self):
        if self._base_rate is None:
            return 'MNB base rate unavailable'
        description = f'MNB base rate: {format_percent(self._base_rate)}'
        if self._rate_date:
            description += f' · {self._rate_date}'
        return description + (' · cached' if self._rate_error else '')

    @property
    def required_currencies(self):
        income_currency = self._income_display_currency if self._income_edited else self._income_currency
        return {'HUF', income_currency,
                *(row['currency'] for table in (self.monthly_costs, self.expenses) for row in table.get_rows())} - {''}

    def change_currency(self):
        """Keep a pending income edit in its original unit before changing the display."""
        if self._changing_currency:
            return False
        currency = self._currency.get()
        if currency == self._income_display_currency:
            return True
        try:
            amount, native_currency = self._income()
        except ValueError as error:
            self._changing_currency = True
            try:
                self._currency.set(self._income_display_currency)
            finally:
                self._changing_currency = False
            self.message.set(f'{error} Complete the income edit before changing currency.')
            return False
        self._income_amount, self._income_currency = amount, native_currency
        self._income_edited = False
        self._income_display_currency = currency
        self._income_entry.set_integer_only(currency == 'HUF')
        self._refresh_income()
        return True

    def planning_settings(self):
        """Resolve rates for planning; the plan supplies actual Portfolio wealth separately."""
        if self._load_error:
            raise ValueError(self._load_error)
        settings = self.settings()
        if settings.returns.mak is None:
            if self._base_rate is None:
                raise ValueError(self._rate_error or 'Fetching MNB base rate…')
            settings = replace(settings, returns=replace(settings.returns, mak=self._base_rate))
        return settings

    def refresh(self):
        if not self.change_currency():
            return
        self._refresh_income()
        self._clear_result()
        self.portfolio_value.set('—')
        currency = self._currency.get()
        self.allocation_summary.heading('amount', text=f'Amount ({currency})')
        self._funding_panel.configure(text=f'Expense funding and current allocation · {currency}')
        try:
            total = self._get_total()
            rates = self._get_rates()
            display_rate = huf_value(1, self._currency.get(), rates)
            self.portfolio_value.set(format_amount(total / display_rate))
            allocation = self._get_allocation()
            self._return_weight_values = return_weights(allocation)
            for category, weight in self._return_weight_values.items():
                self.return_weights[category].set(format_percent(weight))
            self._sort_return_rows()
            settings = self.settings()
            self.monthly_spending.set(format_amount(settings.monthly_spending(rates) / display_rate))
            self.monthly_savings.set(format_amount(settings.monthly_savings(rates) / display_rate))
            result = recommend(self.planning_settings(), date.today(), allocation, total=total, rates=rates)
        except ValueError as error:
            message = str(error)
            if not self._income_available:
                message += f' Income conversion unavailable; select {self._income_currency} to edit income.'
            self.message.set(self._save_error or message)
            return
        for name, amount, share in ((STOCK_MARKET, result.stock, result.stock_percent),
                                     (TREASURY, result.mak, result.mak_percent),
                                     (CASH, result.cash, result.cash_percent)):
            self.allocation_summary.insert('', 'end', iid=name,
                                           values=(name, format_percent(share), format_amount(amount / display_rate)))
            self._summary_values[name] = dict(allocation=name, share=share, amount=amount)
        self._summary_sorting.apply()
        self.overall_return.set(f'Overall annual return: {format_percent(result.expected_return)}')
        for row in result.rows:
            amounts = (row.future_amount, row.income_funded, row.stock, row.mak, row.cash)
            values = (row.name, *('—' if value is None else format_amount(value / display_rate) for value in amounts))
            item = self.breakdown.insert('', 'end', values=values)
            self._values[item] = dict(purpose=row.name, future=row.future_amount, amount=row.amount,
                                     income=row.income_funded, stock=row.stock, mak=row.mak, cash=row.cash)
        self._sorting.apply()
        self.message.set(self._save_error or (f'Unfunded reserves: {format_amount(ceil(result.shortfall / Decimal(str(display_rate))))} {currency}. '
                         'Cash, then MÁK are funded first; remaining money goes to the stock market.'
                         if result.shortfall else self._rate_error))

    def settings(self):
        income, income_currency = self._income()
        return CalculatorSettings(monthly_costs=tuple(self._monthly_cost(row) for row in self.monthly_costs.get_rows()),
                                  monthly_income=income, income_currency=income_currency,
                                  emergency_months=self._number('emergency_months', int),
                                  cash_months=self._number('cash_months', int),
                                  horizon_years=self._number('horizon_years', int),
                                  returns=self.return_assumptions(),
                                  expenses=tuple(self._expense(row) for row in self.expenses.get_rows()))

    def return_assumptions(self):
        return ReturnAssumptions(**{name: None if name == 'mak' and self._auto_rate.get() else self._number(name)
                                   for name in ('equities', 'gold', 'bonds', 'mak')})

    def _build(self, show_projection):
        self.columnconfigure(0, weight=1)
        self.rowconfigure((2, 3), weight=1)
        ttk.Style(self).configure('Calculator.TEntry', padding=(5, 0))
        ttk.Style(self).configure('Calculator.TCombobox', padding=(4, 1))
        self._build_controls(show_projection)
        self._build_settings()
        self._build_cost_tables()
        self._build_results()
        self._message_label = ttk.Label(self, textvariable=self.message, wraplength=950)
        self._message_label.grid(row=4, column=0, sticky='w', pady=(3, 0))
        ttk.Label(self, text='Totals use the selected currency. Inputs save automatically. Returns are nominal HUF estimates; exchange rates stay fixed.',
                  foreground=MUTED_TEXT, wraplength=950).grid(row=5, column=0, sticky='w', pady=(2, 0))

    def _build_controls(self, show_projection):
        controls = ttk.Frame(self)
        controls.grid(row=0, column=0, sticky='ew', pady=(0, 6))
        ttk.Label(controls, text='Portfolio value').grid(row=0, column=0, padx=(0, 8))
        ttk.Label(controls, textvariable=self.portfolio_value, style='Heading.TLabel').grid(
            row=0, column=1, padx=(0, 8))
        self._currency_box = ttk.Combobox(controls, textvariable=self._currency, state='readonly',
                                          width=4, style='Calculator.TCombobox', values=self._currency_choices(),
                                          postcommand=lambda: self._currency_box.configure(values=self._currency_choices()))
        self._currency_box.grid(row=0, column=2, padx=(0, 16))
        self.projection_button = ttk.Button(controls, text='Portfolio projection…', command=show_projection)
        self.projection_button.grid(row=0, column=3)

    def _build_settings(self):
        settings = ttk.Frame(self)
        settings.grid(row=1, column=0, sticky='ew', pady=(0, 6))
        for column, weight in enumerate((4, 4, 3)):
            settings.columnconfigure(column, weight=weight, uniform='settings')
        budget = self._panel(settings, 'Monthly budget and reserves', 0)
        ttk.Label(budget, text='Monthly income').grid(row=0, column=0, sticky='w', pady=2)
        self._entry(budget, 'monthly_income', 10).grid(row=0, column=1, sticky='ew', padx=6, pady=2)
        for row, (label, variable) in enumerate((('Monthly spending', self.monthly_spending),
                                                ('Monthly savings', self.monthly_savings)), 1):
            ttk.Label(budget, text=label).grid(row=row, column=0, sticky='w', pady=2)
            ttk.Label(budget, textvariable=variable, anchor='e', padding=(5, 0)).grid(
                row=row, column=1, sticky='ew', padx=6, pady=2)
        for row in range(3):
            ttk.Label(budget, textvariable=self._currency).grid(row=row, column=2, sticky='w', pady=2)
        for row, (name, label) in enumerate((('emergency_months', 'Emergency reserve'),
                                            ('cash_months', 'Bank cash buffer')), 3):
            ttk.Label(budget, text=label).grid(row=row, column=0, sticky='w', pady=2)
            self._entry(budget, name, 5).grid(row=row, column=1, sticky='e', padx=6, pady=2)
            ttk.Label(budget, text='months').grid(row=row, column=2, sticky='w', pady=2)
        self._build_returns(settings)
        horizon = self._panel(settings, 'Years to 100% stock market', 2)
        self._entry(horizon, 'horizon_years', 5).grid(row=0, column=0, sticky='w', pady=2)
        ttk.Label(horizon, text='years').grid(row=0, column=1, sticky='w', padx=6)
        ttk.Label(horizon, text='At cash deadline → 0%\nThis many years before → 100%',
                  foreground=MUTED_TEXT).grid(row=1, column=0, columnspan=2, sticky='w', pady=(8, 0))

    def _build_returns(self, parent):
        panel = self._panel(parent, 'Expected annual returns', 1)
        panel.configure(padding=(8, 4))
        panel.columnconfigure((0, 1, 2), weight=1)
        for column, title in enumerate(('Category', 'Annual return', 'Target weight')):
            ttk.Button(panel, text=title, padding=(3, 0),
                       command=lambda column=column: self._sort_return_rows(column)).grid(
                row=0, column=column, sticky='ew', padx=1, pady=(0, 2))
        for row, (name, variable) in enumerate(self.return_weights.items(), 1):
            key = 'mak' if name == TREASURY else name.lower()
            label = ttk.Label(panel, text='Bond' if name == 'Bonds' else name)
            entry = self._entry(panel, key, 7)
            weight = ttk.Label(panel, textvariable=variable, anchor='e')
            for column, widget in enumerate((label, entry, weight)):
                widget.grid(row=row, column=column, sticky='ew' if column == 0 else 'e', padx=4)
            self._return_rows[name] = (label, entry, weight)
            if name == TREASURY:
                self._mak_entry = entry
        self._auto_rate_box = ttk.Checkbutton(panel, text='Use MNB base rate', variable=self._auto_rate)
        self._auto_rate_box.grid(row=5, column=0, columnspan=3, sticky='w', pady=(2, 0))

    def _sort_return_rows(self, column=None):
        if column is not None:
            self._return_descending = self._return_sort == column and not self._return_descending
            self._return_sort = column
        if self._return_sort is None:
            return

        def key(name):
            if self._return_sort == 0:
                return name.casefold()
            if self._return_sort == 2:
                return self._return_weight_values.get(name)
            try:
                value = self._number('mak' if name == TREASURY else name.lower())
                return value if isfinite(value) else None
            except ValueError:
                return None

        values = [(key(name), name) for name in self._return_rows]
        ordered = sorted((pair for pair in values if pair[0] is not None), reverse=self._return_descending)
        ordered.extend(pair for pair in values if pair[0] is None)
        for row, (_, name) in enumerate(ordered, 1):
            for widget in self._return_rows[name]:
                widget.grid_configure(row=row)

    def _build_overall(self, parent):
        panel = self._panel(parent, 'Recommended overall allocations', 0)
        panel.configure(padding=(10, 4))
        panel.columnconfigure(0, weight=1)
        self.allocation_summary = ttk.Treeview(panel, columns=('allocation', 'share', 'amount'),
                                               show='headings', height=3)
        for name, label, width in (('allocation', 'Allocation', 130), ('share', 'Share', 75),
                                   ('amount', 'Amount', 140)):
            self.allocation_summary.heading(name, text=label)
            self.allocation_summary.column(name, width=width, minwidth=width,
                                            anchor='w' if name == 'allocation' else 'e')
        self.allocation_summary.grid(row=0, column=0, sticky='ew')
        self._summary_sorting = TreeSort(self.allocation_summary, numeric_columns=('share', 'amount'),
                                         value=lambda item, column: self._summary_values[item][column])
        ttk.Label(panel, textvariable=self.overall_return, style='Heading.TLabel').grid(
            row=1, column=0, sticky='w', pady=(8, 0))

    def _build_cost_tables(self):
        tables = ttk.Frame(self)
        tables.grid(row=2, column=0, sticky='nsew', pady=(0, 6))
        tables.columnconfigure((0, 1), weight=1, uniform='costs')
        tables.rowconfigure(0, weight=1)
        for column, title, name, dated in ((0, 'Monthly costs', 'monthly_costs', False),
                                           (1, 'Planned expenses', 'expenses', True)):
            panel = self._panel(tables, title, column)
            panel.rowconfigure(0, weight=1)
            panel.columnconfigure(0, weight=1)
            columns = [('name', 'Expense' if dated else 'Cost', 150),
                       ('amount', 'Amount', 100), ('currency', 'Currency', 80)]
            if dated:
                columns.append(('due', 'Due date', 110))
            table = DataTable(panel, columns, height=3, choices={'currency': self._currency_choices},
                              rounded_columns=('amount',), date_columns=('due',) if dated else (),
                              field_rows=(('name',), ('amount', 'currency')) + ((('due',),) if dated else ()),
                              validate=self._validate_expense if dated else self._validate_monthly_cost,
                              on_change=self._changed, dialog_title='expense' if dated else 'monthly cost')
            table.grid(row=0, column=0, sticky='nsew')
            setattr(self, name, table)

    def _build_results(self):
        tables = ttk.Frame(self)
        tables.grid(row=3, column=0, sticky='nsew')
        tables.columnconfigure(0, weight=2, uniform='results')
        tables.columnconfigure(1, weight=3, uniform='results')
        tables.rowconfigure(0, weight=1)
        self._build_overall(tables)
        panel = self._panel(tables, 'Expense funding and current allocation', 1)
        self._funding_panel = panel
        panel.rowconfigure(0, weight=1)
        panel.columnconfigure(0, weight=1)
        self.breakdown = ttk.Treeview(panel, columns=('purpose', 'future', 'income', 'stock', 'mak', 'cash'),
                                      show='headings', height=4)
        heading_font = font.Font(root=self, font=ttk.Style(self).lookup('Treeview.Heading', 'font'))
        for name, label, width in (('purpose', 'Purpose', 190), ('future', 'Due amount', 100),
                                   ('income', 'Savings', 90), ('stock', 'Stock market', 115),
                                   ('mak', 'MÁK', 85), ('cash', 'Cash', 85)):
            self.breakdown.heading(name, text=label)
            width = max(width, heading_font.measure(label + ' ▼') + 16)
            self.breakdown.column(name, width=width, minwidth=width, anchor='w' if name == 'purpose' else 'e')
        self.breakdown.grid(row=0, column=0, sticky='nsew')
        for orient, command, row, column, sticky in (('vertical', self.breakdown.yview, 0, 1, 'ns'),
                                                    ('horizontal', self.breakdown.xview, 1, 0, 'ew')):
            scrollbar = AutoScrollbar(panel, orient=orient, command=command)
            scrollbar.grid(row=row, column=column, sticky=sticky)
            self.breakdown.configure(**{f'{"y" if orient == "vertical" else "x"}scrollcommand': scrollbar.set})
        self.breakdown.tag_configure('alternate', background=ALTERNATE_BACKGROUND)
        self._sorting = TreeSort(self.breakdown, numeric_columns=('future', 'income', 'stock', 'mak', 'cash'),
                                 value=lambda item, column: self._values[item].get(column, ''),
                                 on_sort=self._shade_rows)

    def _panel(self, parent, title, column):
        panel = ttk.LabelFrame(parent, text=title, padding=(10, 8))
        panel.grid(row=0, column=column, sticky='nsew', padx=(0 if column == 0 else 6, 6))
        return panel

    def _entry(self, parent, name, width):
        options = dict(textvariable=self._inputs[name], width=width, justify='right', style='Calculator.TEntry')
        if name == 'monthly_income':
            entry = RoundedEntry(parent, integer_only=True, **options)
            self._income_entry = entry
        elif name in ('emergency_months', 'cash_months', 'horizon_years'):
            entry = ttk.Entry(parent, **options)
        else:
            entry = PercentEntry(parent, **options)
        entry.bind('<FocusOut>', lambda _event: self.save(), add='+')
        self._entries.append(entry)
        return entry

    def _set_inputs(self, settings):
        self._loading = True
        try:
            self._income_amount, self._income_currency = settings.monthly_income, settings.income_currency
            self._income_display_currency, self._income_edited = self._currency.get(), False
            for name, variable in self._inputs.items():
                if name == 'monthly_income':
                    continue
                if name in ('equities', 'gold', 'bonds', 'mak'):
                    value = getattr(settings.returns, name)
                    if name == 'mak' and value is None:
                        value = self._base_rate
                else:
                    value = getattr(settings, name)
                variable.set('' if value is None else plain_number(value))
            self._auto_rate.set(settings.returns.mak is None)
            self.monthly_costs.set_rows([{'name': cost.name, 'amount': plain_number(cost.amount),
                                         'currency': cost.currency} for cost in settings.monthly_costs])
            self.expenses.set_rows([{'name': expense.name, 'amount': plain_number(expense.amount),
                                     'currency': expense.currency, 'due': expense.due.isoformat()} for expense in settings.expenses])
        finally:
            self._loading = False
        self._update_rate_state()
        self._income_entry.set_integer_only(self._income_display_currency == 'HUF')
        self._refresh_income()

    def _rate_mode_changed(self, *_):
        if not self._loading:
            self._update_auto_rate()
            self._update_rate_state()
            self._changed()

    def _update_auto_rate(self):
        if self._auto_rate.get():
            self._loading = True
            self._inputs['mak'].set('' if self._base_rate is None else plain_number(self._base_rate))
            self._loading = False

    def _update_rate_state(self):
        self._mak_entry.configure(state='normal' if self._enabled and not self._auto_rate.get() else 'disabled')

    def _income(self):
        if self._income_edited:
            try:
                amount = parse_currency_amount(self._inputs['monthly_income'].get(), self._income_display_currency)
            except ValueError as error:
                raise ValueError(f'Monthly income: {error}') from error
            if amount < 0:
                raise ValueError('Monthly income must be at least 0.')
            return amount, self._income_display_currency
        return self._income_amount, self._income_currency

    def _refresh_income(self):
        if self._income_edited:
            return
        try:
            amount = self._income_amount
            if amount and self._income_currency != self._income_display_currency:
                rates = self._get_rates()
                amount = (huf_value(amount, self._income_currency, rates)
                          / huf_value(1, self._income_display_currency, rates))
            text, self._income_available = plain_number(amount), True
        except ValueError:
            text, self._income_available = '—', False
        self._loading = True
        try:
            if self._inputs['monthly_income'].get() != text:
                self._inputs['monthly_income'].set(text)
        finally:
            self._loading = False
        self._update_income_state()

    def _update_income_state(self):
        self._income_entry.configure(
            state='normal' if self._enabled and self._income_available else 'disabled')

    def _input_changed(self, name):
        if self._loading:
            return
        if name == 'monthly_income':
            self._income_edited = True
        self._changed()

    def _changed(self, *_):
        if not self._loading:
            self.dirty = True
            self._load_error = ''
            self._on_change()
            self.refresh()
            self._cancel_autosave()
            self._autosave_id = self.after(500, self.save)

    def _cancel_autosave(self):
        if self._autosave_id is not None:
            self.after_cancel(self._autosave_id)
            self._autosave_id = None

    def _clear_result(self):
        self.breakdown.delete(*self.breakdown.get_children())
        self._values.clear()
        self.allocation_summary.delete(*self.allocation_summary.get_children())
        self._summary_values.clear()
        for variable in self.return_weights.values():
            variable.set('—')
        self._return_weight_values.clear()
        self.overall_return.set('Overall annual return: —')
        self.monthly_spending.set('—')
        self.monthly_savings.set('—')

    def _show_message(self, *_):
        self._message_label.grid() if self.message.get() else self._message_label.grid_remove()

    def _number(self, name, parse=float):
        try:
            return parse(self._inputs[name].get())
        except ValueError as error:
            label = name.replace('_', ' ').capitalize()
            raise ValueError(f'{label}: enter {"a whole number" if parse is int else "a number"}.') from error

    def _monthly_cost(self, row):
        currency = currency_code(row['currency'])
        return MonthlyCost(row['name'].strip(), parse_currency_amount(row['amount'], currency), currency)

    def _validate_monthly_cost(self, row, _old):
        cost = self._monthly_cost(row)
        return {'name': cost.name, 'amount': plain_number(cost.amount), 'currency': cost.currency}

    def _expense(self, row):
        try:
            due = date.fromisoformat(row['due'].strip())
        except ValueError as error:
            raise ValueError('Enter the due date as YYYY-MM-DD.') from error
        currency = currency_code(row['currency'])
        amount = parse_currency_amount(row['amount'], currency)
        return Expense(row['name'].strip(), amount, due, currency)

    def _validate_expense(self, row, _old):
        expense = self._expense(row)
        return {'name': expense.name, 'amount': plain_number(expense.amount),
                'currency': expense.currency, 'due': expense.due.isoformat()}

    def _shade_rows(self):
        for index, item in enumerate(self.breakdown.get_children()):
            self.breakdown.item(item, tags=('alternate',) if index % 2 else ())
