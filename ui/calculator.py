"""Currency-aware reserve inputs and recommendations for Portfolio’s long-term targets."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from math import ceil, isfinite
from pathlib import Path
import tkinter as tk
from tkinter import font, ttk

from portfolio.calculator import CalculatorSettings, ReturnAssumptions, huf_value, recommend, return_weights
from portfolio.storage import load_settings, save_settings
from portfolio.models import CASH, STOCK_MARKET, TREASURY
from ui.reporting import format_amount, format_percent, plain_number
from ui.sorting import TreeSort
from ui.help import HELP, add_help, info_label
from ui.theme import ALTERNATE_BACKGROUND
from ui.widgets import AutoScrollbar, PercentEntry
from ui.cashflows import CashFlowTable
from ui.dates import MonthEntry


class CalculatorView(ttk.Frame):
    def __init__(self, parent, *, get_total, return_parents, currency, get_allocation,
                 get_rates, currency_choices, on_change, link_settings=None, open_source=None):
        super().__init__(parent, padding=(8, 4))
        self._get_allocation, self._on_change = get_allocation, on_change
        self._get_rates, self._currency_choices = get_rates, currency_choices
        self._get_total, self._currency = get_total, currency
        self._link_settings = link_settings or (lambda settings: settings)
        self._open_source = open_source
        self._linked_values = {}
        self.dirty, self._enabled, self._loading = False, True, False
        self._directory, self._original, self._load_error = None, None, ''
        self._autosave_id, self._save_error = None, ''
        self._base_rate, self._rate_error, self._rate_date = None, '', ''
        self._inputs = {name: tk.StringVar(self) for name in
                        ('emergency_months', 'cash_months', 'horizon_years', 'equities', 'gold', 'bonds', 'mak')}
        self._month = tk.StringVar(self, value=date.today().isoformat()[:7])
        self._monthly_values = {}
        self._auto_rate = tk.BooleanVar(self, value=True)
        self.portfolio_value = tk.StringVar(self)
        self.message = tk.StringVar(self)
        self.overall_return = tk.StringVar(self)
        self._saving_ratio = tk.StringVar(self, value='—')
        self.return_weights = {name: tk.StringVar(self) for name in ('Equities', 'Bonds', 'Gold')}
        self._return_rows, self._return_weight_values = {}, {}
        self._return_sort, self._return_descending = None, False
        self._entries, self._values, self._summary_values = [], {}, {}
        self._build()
        self._build_returns(return_parents)
        self.message.trace_add('write', self._show_message)
        self._set_inputs(CalculatorSettings())
        for variable in self._inputs.values():
            variable.trace_add('write', self._changed)
        self._auto_rate.trace_add('write', self._rate_mode_changed)
        self._month.trace_add('write', self._refresh_monthly_summary)
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
        for widget in (*self._entries, self._auto_rate_box):
            widget.configure(state=state)
        self._update_rate_state()
        self.cashflows.set_enabled(enabled)

    @property
    def uses_base_rate(self):
        return self._auto_rate.get()

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
        return {'HUF', *(row['currency'] for row in self.cashflows.get_rows())} - {''}

    def planning_settings(self):
        """Resolve rates for planning; the plan supplies actual Portfolio wealth separately."""
        if self._load_error:
            raise ValueError(self._load_error)
        settings = self.settings()
        if settings.returns.mak is None:
            if self._base_rate is None:
                raise ValueError(self._rate_error or 'Fetching MNB base rate…')
            settings = replace(settings, returns=replace(settings.returns, mak=self._base_rate))
        return self._link_settings(settings)

    def refresh(self):
        self._clear_result()
        self.portfolio_value.set('—')
        self._refresh_monthly_summary()
        currency = self._currency.get()
        self.allocation_summary.heading('amount', text=f'Amount ({currency})')
        self._funding_panel.configure(text=f'Single expense funding · {currency}')
        self._funding_panel.help_label.label.configure(text=f'Single expense funding · {currency}')
        try:
            total = self._get_total()
            rates = self._get_rates()
            display_rate = huf_value(1, self._currency.get(), rates)
            self.portfolio_value.set(format_amount(total / display_rate))
            allocation = self._get_allocation()
            self._return_weight_values = return_weights(allocation)
            for category, variable in self.return_weights.items():
                variable.set(format_percent(self._return_weight_values[category]))
            self._sort_return_rows()
            settings = self.settings()
            result = recommend(self.planning_settings(), date.today(), allocation, total=total, rates=rates)
        except ValueError as error:
            self.message.set(self._save_error or str(error))
            return
        for name, amount, share in ((STOCK_MARKET, result.stock, result.stock_percent),
                                     (TREASURY, result.mak, result.mak_percent),
                                     (CASH, result.cash, result.cash_percent)):
            self.allocation_summary.insert('', 'end', iid=name,
                                           values=(name, format_percent(share), format_amount(amount / display_rate)))
            self._summary_values[name] = dict(allocation=name, share=share, amount=amount)
        self._summary_sorting.apply()
        self.overall_return.set(format_percent(result.expected_return))
        self._show_funding(result.rows, settings.outgoings, display_rate)
        self.message.set(self._save_error or (f'Unfunded reserves: {format_amount(ceil(result.shortfall / Decimal(str(display_rate))))} {currency}. '
                         'Cash, then MÁK are funded first; remaining money goes to the stock market.'
                         if result.shortfall else self._rate_error))

    def settings(self):
        return CalculatorSettings(incomes=self.cashflows.cashflows('Income'), outgoings=self.cashflows.cashflows('Outgoing'),
                                  emergency_months=self._number('emergency_months', int),
                                  cash_months=self._number('cash_months', int),
                                  horizon_years=self._number('horizon_years', int),
                                  returns=self.return_assumptions())

    def return_assumptions(self):
        return ReturnAssumptions(**{name: None if name == 'mak' and self._auto_rate.get() else self._number(name)
                                   for name in ('equities', 'gold', 'bonds', 'mak')})

    def _build(self):
        self.columnconfigure((0, 1), weight=1, uniform='calculator')
        self.rowconfigure(1, weight=1)
        ttk.Style(self).configure('Calculator.TEntry', padding=(5, 0))
        ttk.Style(self).configure('Calculator.Treeview', rowheight=24)
        ttk.Style(self).configure('Calculator.Treeview.Heading', padding=(6, 0))
        self._build_settings()
        self._build_cashflows()
        self._build_overall()
        self._build_portfolio_summary()
        self._message_label = ttk.Label(self, textvariable=self.message, wraplength=950)
        self._message_label.grid(row=3, column=0, columnspan=2, sticky='w', pady=(3, 0))

    def _build_settings(self):
        reserves = self._panel('Settings', 0, 0)
        reserves.columnconfigure(0, weight=1)
        for row, (name, label, unit) in enumerate((('emergency_months', 'Emergency reserve', 'months'),
                                                   ('cash_months', 'Bank cash buffer', 'months'),
                                                   ('horizon_years', '100% stock market', 'years'))):
            info_label(reserves, label, HELP[name]).grid(row=row, column=0, sticky='w', pady=5)
            self._entry(reserves, name, 5).grid(row=row, column=1, padx=12, pady=5)
            ttk.Label(reserves, text=unit).grid(row=row, column=2, sticky='w', pady=5)

    def _build_monthly_summary(self, parent):
        panel = self._panel('Monthly cash flow', 0, 0, parent=parent)
        panel.configure(padding=(8, 4))
        panel.columnconfigure(0, weight=1)
        period = ttk.Frame(panel)
        info_label(period, 'Monthly cash flow', HELP['monthly_totals']).pack(side='left', padx=(0, 10))
        self._month_box = MonthEntry(period, textvariable=self._month, width=8)
        self._month_box.entry.configure(style='Calculator.TEntry')
        self._month_box.button.configure(padding=(3, 0))
        self._month_box.pack(side='left')
        ttk.Label(period, textvariable=self._currency).pack(side='left', padx=(8, 0))
        panel.configure(labelwidget=period)
        add_help(self._month_box, HELP['monthly_totals'])
        self.monthly_summary = ttk.Treeview(panel, columns=('purpose', 'recurring', 'single', 'total'),
                                           show='headings', height=3, style='Calculator.Treeview')
        for key, title, width in (('purpose', '', 80), ('recurring', 'Recurring', 85),
                                  ('single', 'Single', 70), ('total', 'Total', 85)):
            self.monthly_summary.heading(key, text=title)
            self.monthly_summary.column(key, width=width, minwidth=width,
                                        anchor='w' if key == 'purpose' else 'e')
        self.monthly_summary.grid(row=0, column=0, sticky='ew')
        self._monthly_sorting = TreeSort(self.monthly_summary, numeric_columns=('recurring', 'single', 'total'),
                                         value=lambda item, column: self._monthly_values[item][column])

    def _refresh_monthly_summary(self, *_):
        self.monthly_summary.delete(*self.monthly_summary.get_children())
        self.linked_payments.delete(*self.linked_payments.get_children())
        self._linked_values.clear()
        self._monthly_values.clear()
        self._saving_ratio.set('—')
        try:
            rates = self._get_rates()
            conversion = huf_value(1, self._currency.get(), rates)
            month = MonthEntry.validate_month(self._month.get())
            settings = self._link_settings(self.settings())
            totals = settings.monthly_totals(month.year, month.month, rates)
            self._refresh_linked_payments(settings, month)
        except ValueError:
            return
        if totals.saving_percent is not None:
            self._saving_ratio.set(format_percent(totals.saving_percent))
        for name, recurring, single in (('Income', totals.recurring_income, totals.single_income),
                                         ('Outgoings', totals.recurring_outgoings, totals.single_outgoings),
                                         ('Net', totals.recurring_income - totals.recurring_outgoings,
                                          totals.single_income - totals.single_outgoings)):
            amounts = dict(purpose=name, recurring=recurring, single=single, total=recurring + single)
            item = self.monthly_summary.insert('', 'end', iid=name,
                                               values=(name, *(format_amount(amounts[key] / conversion)
                                                               for key in ('recurring', 'single', 'total'))))
            self._monthly_values[item] = amounts
        self._monthly_sorting.apply()

    def _build_returns(self, parents):
        """Bind Portfolio's editors directly to the settings saved by this view."""
        stock = self._panel('Expected annual returns', 0, 0, parent=parents[STOCK_MARKET])
        stock.configure(padding=(8, 4))
        stock.columnconfigure((0, 1, 2), weight=1)
        for column, title in enumerate(('Category', 'Annual return', 'Target weight')):
            ttk.Button(stock, text=title, padding=(3, 0),
                       command=lambda column=column: self._sort_return_rows(column)).grid(
                row=0, column=column, sticky='ew', padx=1, pady=(0, 2))
        for row, name in enumerate(self.return_weights, 1):
            label = info_label(stock, 'Bond' if name == 'Bonds' else name, HELP[name.lower()])
            entry = self._entry(stock, name.lower(), 7)
            weight = ttk.Label(stock, textvariable=self.return_weights[name], anchor='e')
            add_help(weight, HELP['return_weight'])
            for column, widget in enumerate((label, entry, weight)):
                widget.grid(row=row, column=column, sticky='ew' if column == 0 else 'e', padx=4)
            self._return_rows[name] = (label, entry, weight)

        mak = ttk.Frame(parents[TREASURY], padding=(8, 4))
        mak.grid(row=0, column=0, sticky='ew', pady=(0, 6))
        info_label(mak, 'Expected annual return', HELP['mak']).grid(row=0, column=0, sticky='w')
        self._mak_entry = self._entry(mak, 'mak', 7)
        self._mak_entry.grid(row=0, column=1, padx=(12, 20))
        self._auto_rate_box = ttk.Checkbutton(mak, text='Use MNB base rate', variable=self._auto_rate)
        self._auto_rate_box.grid(row=0, column=2, sticky='w')
        add_help(self._auto_rate_box, HELP['auto_rate'])
        self.return_panels = {STOCK_MARKET: stock, TREASURY: mak}
        self._return_messages = []
        for branch in (STOCK_MARKET, TREASURY):
            message = ttk.Label(parents[branch], textvariable=self.message, wraplength=950)
            message.grid(row=1, column=0, sticky='w', pady=(0, 4))
            self._return_messages.append(message)

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
                value = self._number(name.lower())
                return value if isfinite(value) else None
            except ValueError:
                return None

        values = [(key(name), name) for name in self._return_rows]
        ordered = sorted((pair for pair in values if pair[0] is not None), reverse=self._return_descending)
        ordered.extend(pair for pair in values if pair[0] is None)
        for row, (_, name) in enumerate(ordered, 1):
            for widget in self._return_rows[name]:
                widget.grid_configure(row=row)

    def _build_overall(self):
        panel = self._panel('Recommended overall allocations', 2, 0, 'overall_allocation')
        panel.grid_configure(columnspan=2, padx=0)
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

    def _build_portfolio_summary(self):
        panel = self._summary_panel = self._panel('Portfolio summary', 0, 1)
        panel.columnconfigure(1, weight=1)
        for row, (label, variable, help_key) in enumerate((
                ('Portfolio value', self.portfolio_value, 'portfolio_value'),
                ('Overall annual return', self.overall_return, 'overall_return'),
                ('Monthly saving ratio', self._saving_ratio, 'saving_ratio'))):
            info_label(panel, label, HELP[help_key]).grid(row=row, column=0, sticky='w')
            ttk.Label(panel, textvariable=variable, anchor='e').grid(
                row=row, column=1, sticky='e', padx=(12, 4))
        ttk.Label(panel, textvariable=self._currency).grid(row=0, column=2, sticky='w')

    def _build_cashflows(self):
        panel = self._panel('Income and outgoings', 1, 0, 'cashflows')
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(0, weight=1)
        notebook = ttk.Notebook(panel)
        notebook.grid(row=0, column=0, sticky='nsew')
        self.cashflows = CashFlowTable(notebook, currency_choices=self._currency_choices, on_change=self._changed)
        notebook.add(self.cashflows, text='Entered here')
        linked = ttk.Frame(notebook)
        notebook.add(linked, text='Linked payments')
        linked.columnconfigure(0, weight=1)
        linked.rowconfigure(1, weight=1)
        period = ttk.Frame(linked)
        period.grid(row=0, column=0, sticky='ew', pady=3)
        info_label(period, 'Month', 'Payments from Portfolio properties and pensions, and the Loans tab. Double-click to edit their source. '
                   'These already contribute to monthly totals and recommendations; do not enter them again.').pack(side='left', padx=4)
        MonthEntry(period, textvariable=self._month, width=8).pack(side='left')
        columns = ('name', 'type', 'amount', 'currency')
        self.linked_payments = ttk.Treeview(linked, columns=columns, show='headings', height=4,
                                           style='Calculator.Treeview')
        for key, title, width in (('name', 'Source / payment', 190), ('type', 'Type', 80),
                                  ('amount', 'Amount', 95), ('currency', 'Currency', 65)):
            self.linked_payments.heading(key, text=title)
            self.linked_payments.column(key, width=width, minwidth=60, anchor='e' if key == 'amount' else 'w')
        self.linked_payments.grid(row=1, column=0, sticky='nsew')
        scrollbar = AutoScrollbar(linked, orient='vertical', command=self.linked_payments.yview)
        scrollbar.grid(row=1, column=1, sticky='ns')
        self.linked_payments.configure(yscrollcommand=scrollbar.set)
        self._linked_sorting = TreeSort(self.linked_payments, numeric_columns=('amount',),
                                        value=lambda item, key: self._linked_values[item][key])
        self.linked_payments.bind('<Double-1>', self._open_linked_payment)
        self.linked_payments.bind('<Return>', self._open_linked_payment)
        results = ttk.Frame(self)
        results.grid(row=1, column=1, sticky='nsew', padx=(6, 0))
        results.columnconfigure(0, weight=1)
        results.rowconfigure((0, 1), weight=1, uniform='monthly_panels')
        self._build_monthly_summary(results)
        self._build_funding(results)

    def _refresh_linked_payments(self, settings, month):
        if not hasattr(settings, 'events_for_month'):
            return
        for event in settings.events_for_month(month.year, month.month):
            values = dict(name=event.name, type=event.direction.capitalize(), amount=event.amount,
                          currency=event.currency, source_id=event.source_id)
            item = self.linked_payments.insert('', 'end', values=(values['name'], values['type'],
                                                                  format_amount(event.amount), event.currency))
            self._linked_values[item] = values
        self._linked_sorting.apply()

    def _open_linked_payment(self, event=None):
        item = self.linked_payments.identify_row(event.y) if event and event.type == tk.EventType.ButtonPress else None
        item = item or next(iter(self.linked_payments.selection()), None)
        if item in self._linked_values and self._open_source:
            self._open_source(self._linked_values[item]['source_id'])

    def _build_funding(self, parent):
        panel = self._panel('Single expense funding', 1, 0, 'expense_funding', parent=parent)
        self._funding_panel = panel
        panel.rowconfigure(0, weight=1)
        panel.columnconfigure(0, weight=1)
        self.breakdown = ttk.Treeview(panel, columns=('income', 'stock', 'mak', 'cash'),
                                      show='tree headings', height=2, style='Calculator.Treeview')
        heading_font = font.Font(root=self, font=ttk.Style(self).lookup('Treeview.Heading', 'font'))
        for name, label, width in (('#0', 'Expense', 135), ('income', 'Future income', 100),
                                   ('stock', 'Stock market', 100), ('mak', 'MÁK', 75), ('cash', 'Cash', 75)):
            self.breakdown.heading(name, text=label)
            width = max(width, heading_font.measure(label + ' ▼') + 16)
            self.breakdown.column(name, width=width, minwidth=width, anchor='w' if name == '#0' else 'e')
        self.breakdown.grid(row=0, column=0, sticky='nsew')
        for orient, command, row, column, sticky in (('vertical', self.breakdown.yview, 0, 1, 'ns'),
                                                    ('horizontal', self.breakdown.xview, 1, 0, 'ew')):
            scrollbar = AutoScrollbar(panel, orient=orient, command=command)
            scrollbar.grid(row=row, column=column, sticky=sticky)
            self.breakdown.configure(**{f'{"y" if orient == "vertical" else "x"}scrollcommand': scrollbar.set})
        self.breakdown.tag_configure('alternate', background=ALTERNATE_BACKGROUND)
        self._sorting = TreeSort(self.breakdown, numeric_columns=('income', 'stock', 'mak', 'cash'),
                                 value=lambda item, column: self._values[item].get('purpose' if column == '#0' else column, ''),
                                 on_sort=self._shade_rows)

    def _panel(self, title, row, column, help_key=None, *, parent=None):
        panel = ttk.LabelFrame(self if parent is None else parent, text=title, padding=(10, 4))
        if help_key:
            panel.help_label = info_label(panel, title, HELP[help_key])
            panel.configure(labelwidget=panel.help_label)
        padding = 0 if parent is not None else (0, 6) if column == 0 else (6, 0)
        panel.grid(row=row, column=column, sticky='nsew', padx=padding, pady=(0, 6))
        return panel

    def _entry(self, parent, name, width):
        options = dict(textvariable=self._inputs[name], width=width, justify='right', style='Calculator.TEntry')
        if name in ('emergency_months', 'cash_months', 'horizon_years'):
            entry = ttk.Entry(parent, **options)
        else:
            entry = PercentEntry(parent, **options)
        entry.bind('<FocusOut>', lambda _event: self.save(), add='+')
        add_help(entry, HELP[name])
        self._entries.append(entry)
        return entry

    def _set_inputs(self, settings):
        self._loading = True
        try:
            for name, variable in self._inputs.items():
                if name in ('equities', 'gold', 'bonds', 'mak'):
                    value = getattr(settings.returns, name)
                    if name == 'mak' and value is None:
                        value = self._base_rate
                else:
                    value = getattr(settings, name)
                variable.set('' if value is None else plain_number(value))
            self._auto_rate.set(settings.returns.mak is None)
            self.cashflows.set_cashflows(settings.incomes, settings.outgoings)
        finally:
            self._loading = False
        self._update_rate_state()

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
        self.overall_return.set('—')

    def _show_message(self, *_):
        for label in (self._message_label, *self._return_messages):
            label.grid() if self.message.get() else label.grid_remove()

    def _number(self, name, parse=float):
        try:
            return parse(self._inputs[name].get())
        except ValueError as error:
            label = name.replace('_', ' ').capitalize()
            raise ValueError(f'{label}: enter {"a whole number" if parse is int else "a number"}.') from error

    def _show_funding(self, rows, outgoings, display_rate):
        fields = (('income', 'income_funded'), ('stock', 'stock'), ('mak', 'mak'), ('cash', 'cash'))
        for row in rows:
            if row.outgoing_index is None or row.outgoing_index < 0 or outgoings[row.outgoing_index].frequency != 'once':
                continue
            amounts = {key: getattr(row, attribute) for key, attribute in fields}
            item = self.breakdown.insert('', 'end', text=row.name,
                                         values=[format_amount(amounts[key] / display_rate)
                                                 for key in self.breakdown['columns']])
            self._values[item] = dict(purpose=row.name, **amounts)
        self._sorting.apply()

    def _shade_rows(self):
        for index, item in enumerate(self.breakdown.get_children()):
            self.breakdown.item(item, tags=('alternate',) if index % 2 else ())
