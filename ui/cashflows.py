"""Editable income and outgoing schedules in one table."""

from datetime import date
import tkinter as tk
from tkinter import ttk

from portfolio.calculator import CashFlow
from portfolio.models import currency_code, parse_currency_amount
from ui.dates import MonthEntry
from ui.help import HELP, add_help, info_label
from ui.reporting import format_amount, format_percent, plain_number
from ui.table import DataTable
from ui.widgets import PercentEntry, RoundedEntry


_FREQUENCIES = {'once': 'Once', 'monthly': 'Monthly', 'annual': 'Annually'}
_GROWTH_MODES = {'fixed': 'Fixed amount', 'inflation': 'Follow inflation', 'custom': 'Custom rate'}
_AMOUNT_BASES = ('Future payment amount', "Today's money")


class CashFlowTable(DataTable):
    def __init__(self, parent, *, currency_choices, on_change, extra_columns=(), get_inflation=None):
        self._currency_choices = currency_choices
        self._get_inflation = get_inflation or (lambda: 0)
        columns = [('kind', 'Type', 80), ('name', 'Name', 150), ('amount', 'Amount', 95),
                   ('currency', 'Currency', 90), ('growth_mode', 'Growth / amount basis', 175),
                   *extra_columns, ('frequency', 'Schedule', 85), ('start', 'Begins', 105), ('ending', 'Ends', 115)]
        super().__init__(parent, columns, height=4, rounded_columns=('amount',),
                         validate=self._validate, on_change=on_change, dialog_title='cash flow',
                         field_rows=(('kind',), ('name',), ('amount', 'currency'), ('frequency',),
                                     ('start',), ('ending',), ('count', 'end'),
                                     ('growth_mode', 'appreciation_percent'), ('preview',)))

    def cashflows(self, kind):
        return tuple(self._cashflow(row) for row in self.get_rows() if row['kind'] == kind)

    def set_cashflows(self, incomes, outgoings):
        self.set_rows([self._row(flow, kind) for kind, flows in (('Income', incomes), ('Outgoing', outgoings))
                       for flow in flows])

    def display_value(self, row, key):
        if key == 'growth_mode':
            if row['frequency'] == 'once':
                return f'Prices as of {row["price_month"]}' if row.get('price_month') else _AMOUNT_BASES[0]
            mode = row['growth_mode']
            return f'Custom {format_percent(row["appreciation_percent"])}' if mode == 'custom' else _GROWTH_MODES[mode]
        if key == 'frequency':
            return _FREQUENCIES[row[key]]
        if key == 'ending':
            if row['frequency'] == 'once':
                return '—'
            if row.get('count'):
                return f'{row["count"]} payments'
            return row.get('end') or 'Ongoing'
        return super().display_value(row, key)

    def sort_value(self, row, key):
        return self.display_value(row, key) if key in ('growth_mode', 'frequency', 'ending') else super().sort_value(row, key)

    def _normalize(self, row):
        return super()._normalize({'appreciation_percent': '0', 'growth_mode': 'fixed', 'price_month': '', **row})

    def _edit_fields(self, form, old):
        initial = dict(kind='Outgoing', start=date.today().isoformat()[:7], currency='HUF',
                       appreciation_percent='0', growth_mode='fixed', price_month='')
        initial.update(old or {})
        initial['frequency'] = _FREQUENCIES[initial.get('frequency', 'once')]
        initial['growth_mode'] = _GROWTH_MODES[initial['growth_mode']]
        initial['amount_basis'] = _AMOUNT_BASES[bool(initial['price_month'])]
        initial['ending'] = 'Number of payments' if initial.get('count') else 'End month' if initial.get('end') else 'Ongoing'
        specifications = (('kind', 'Type', 0, 0, 2), ('name', 'Name', 1, 0, 2),
                          ('amount', 'Amount', 2, 0, 1), ('currency', 'Currency', 2, 1, 1),
                          ('frequency', 'Schedule', 3, 0, 2), ('start', 'Begins', 4, 0, 2),
                          ('ending', 'Ends', 5, 0, 2), ('count', 'Number of payments', 6, 0, 2),
                          ('end', 'End month', 6, 0, 2), ('growth_mode', 'Annual change', 7, 0, 1),
                          ('appreciation_percent', 'Custom annual rate', 7, 1, 1),
                          ('amount_basis', 'Amount expressed in', 7, 0, 1),
                          ('price_month', 'Prices as of', 7, 1, 1))
        choices = {'kind': ('Income', 'Outgoing'), 'currency': self._currency_choices(),
                   'frequency': tuple(_FREQUENCIES.values()), 'growth_mode': tuple(_GROWTH_MODES.values()),
                   'amount_basis': _AMOUNT_BASES, 'ending': ('Ongoing', 'Number of payments', 'End month')}
        variables, editors, labels = {}, {}, {}
        for key, title, row, column, span in specifications:
            variable = variables[key] = tk.StringVar(form, value=initial.get(key, ''))
            label = labels[key] = info_label(form, title, HELP['cashflow_' + key] if 'cashflow_' + key in HELP else HELP[key])
            label.grid(row=2 * row, column=column, columnspan=span, sticky='w', pady=(6, 2))
            if key in choices:
                editor = ttk.Combobox(form, textvariable=variable, values=choices[key], state='readonly', width=24)
            else:
                entry = (MonthEntry if key in ('start', 'end', 'price_month') else RoundedEntry if key == 'amount'
                         else PercentEntry if key == 'appreciation_percent' else ttk.Entry)
                editor = entry(form, textvariable=variable, width=32 if span == 2 else 20)
            editors[key] = editor
            editor.grid(row=2 * row + 1, column=column, columnspan=span, sticky='ew',
                        padx=(0, 12) if span == 1 and column == 0 else 0, pady=(0, 6))
            add_help(editor, label.tooltip.text)
        preview = ttk.Label(form, wraplength=430)
        preview.grid(row=16, column=0, columnspan=2, sticky='w', pady=(0, 6))

        def update(*_):
            recurring = variables['frequency'].get() != 'Once'
            real_amount = variables['amount_basis'].get() == _AMOUNT_BASES[1]
            if not recurring and real_amount and not variables['price_month'].get():
                variables['price_month'].set(date.today().isoformat()[:7])
            for key, visible in (('ending', recurring),
                                 ('count', recurring and variables['ending'].get() == 'Number of payments'),
                                 ('end', recurring and variables['ending'].get() == 'End month'),
                                 ('growth_mode', recurring),
                                 ('appreciation_percent', recurring and variables['growth_mode'].get() == _GROWTH_MODES['custom']),
                                 ('amount_basis', not recurring), ('price_month', not recurring and real_amount)):
                for widget in (labels[key], editors[key]):
                    widget.grid() if visible else widget.grid_remove()
            editors['amount'].set_integer_only(variables['currency'].get() == 'HUF')
            preview.grid_remove()
            if not recurring and real_amount:
                try:
                    row = {key: variable.get() for key, variable in variables.items()}
                    row.update(frequency='once', count='', end='', appreciation_percent='0')
                    flow = self._cashflow(row)
                    nominal = flow.amount_on(flow.start, self._get_inflation())
                    preview.configure(text=f'Future payment: {format_amount(nominal)} {flow.currency} in {flow.start:%Y-%m}')
                except ValueError:
                    preview.configure(text='Complete the amount and months to preview the future payment.')
                preview.grid()

        for key in ('frequency', 'ending', 'currency', 'growth_mode', 'amount_basis', 'price_month', 'amount', 'start', 'name'):
            variables[key].trace_add('write', update)
        update()
        return variables, editors['name']

    def _validate(self, row, old):
        if row['kind'] not in ('Income', 'Outgoing'):
            raise ValueError('Choose Income or Outgoing.')
        frequencies = {label: value for value, label in _FREQUENCIES.items()}
        row['frequency'] = frequencies.get(row['frequency'], row['frequency'])
        if row['frequency'] not in _FREQUENCIES:
            raise ValueError('Choose Once, Monthly or Annually.')
        recurring = row['frequency'] != 'once'
        if not recurring or row.get('growth_mode', 'fixed') not in ('custom', _GROWTH_MODES['custom']):
            row['appreciation_percent'] = (old or {}).get('appreciation_percent', '0')
        end_mode = row.get('ending')
        row['count'] = row.get('count', '') if recurring and end_mode == 'Number of payments' else ''
        row['end'] = row.get('end', '') if recurring and end_mode == 'End month' else ''
        if recurring and end_mode == 'Number of payments' and not row['count'].strip():
            raise ValueError('Enter the number of payments.')
        if recurring and end_mode == 'End month' and not row['end'].strip():
            raise ValueError('Choose an end month.')
        if not recurring and row.get('amount_basis') == _AMOUNT_BASES[1] and not row.get('price_month'):
            raise ValueError('Choose the month whose prices the amount uses.')
        return self._row(self._cashflow(row), row['kind'])

    @staticmethod
    def _cashflow(row):
        currency = currency_code(row['currency'])
        try:
            count = int(row['count']) if row.get('count') else None
        except ValueError as error:
            raise ValueError('Number of payments must be a whole number.') from error
        modes = {label: key for key, label in _GROWTH_MODES.items()}
        mode = modes.get(row.get('growth_mode'), row.get('growth_mode', 'fixed')) if row['frequency'] != 'once' else 'fixed'
        try:
            appreciation = float(str(row.get('appreciation_percent', 0)).rstrip('%').strip())
        except ValueError as error:
            raise ValueError('Custom annual rate must be a valid percentage.') from error
        price_month = row.get('price_month') if row['frequency'] == 'once' and row.get('amount_basis') != _AMOUNT_BASES[0] else None
        return CashFlow(row['name'].strip(), parse_currency_amount(row['amount'], currency),
                        MonthEntry.validate_month(row['start']), currency, row['frequency'], count,
                        MonthEntry.validate_month(row['end']) if row.get('end') else None, appreciation,
                        growth_mode=mode, price_month=MonthEntry.validate_month(price_month) if price_month else None)

    @staticmethod
    def _row(flow, kind):
        return dict(kind=kind, name=flow.name, amount=plain_number(flow.amount), currency=flow.currency,
                    frequency=flow.frequency, start=flow.start.isoformat()[:7],
                    count='' if flow.count is None else str(flow.count),
                    end='' if flow.end is None else flow.end.isoformat()[:7], ending='',
                    appreciation_percent=plain_number(flow.appreciation_percent), growth_mode=flow.growth_mode,
                    price_month='' if flow.price_month is None else flow.price_month.isoformat()[:7])
