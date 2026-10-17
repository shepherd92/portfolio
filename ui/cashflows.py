"""Editable income and outgoing schedules in one table."""

from datetime import date
import tkinter as tk
from tkinter import ttk

from portfolio.calculator import CashFlow
from portfolio.models import currency_code, parse_currency_amount
from ui.dates import MonthEntry
from ui.help import HELP, add_help, info_label
from ui.reporting import plain_number
from ui.table import DataTable
from ui.widgets import RoundedEntry


_FREQUENCIES = {'once': 'Once', 'monthly': 'Monthly', 'annual': 'Annually'}


class CashFlowTable(DataTable):
    def __init__(self, parent, *, currency_choices, on_change, extra_columns=(), percent_columns=()):
        columns = [('kind', 'Type', 80), ('name', 'Name', 150), ('amount', 'Amount', 95),
                   ('currency', 'Currency', 90), *extra_columns, ('frequency', 'Schedule', 85),
                   ('start', 'Begins', 105), ('ending', 'Ends', 115)]
        super().__init__(parent, columns, height=4, rounded_columns=('amount',),
                         percent_columns=percent_columns,
                         validate=self._validate, on_change=on_change, dialog_title='cash flow',
                         field_rows=(('kind',), ('name',), ('amount', 'currency'), ('frequency',),
                                     ('start',), ('ending',), ('count', 'end')))
        self._currency_choices = currency_choices

    def cashflows(self, kind):
        return tuple(self._cashflow(row) for row in self.get_rows() if row['kind'] == kind)

    def set_cashflows(self, incomes, outgoings):
        self.set_rows([self._row(flow, kind) for kind, flows in (('Income', incomes), ('Outgoing', outgoings))
                       for flow in flows])

    def display_value(self, row, key):
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
        return self.display_value(row, key) if key in ('frequency', 'ending') else super().sort_value(row, key)

    def _edit_fields(self, form, old):
        initial = dict(old or {})
        initial.setdefault('kind', 'Outgoing')
        initial.setdefault('start', date.today().isoformat()[:7])
        initial.setdefault('currency', 'HUF')
        initial['frequency'] = _FREQUENCIES[initial.get('frequency', 'once')]
        initial['ending'] = 'Number of payments' if initial.get('count') else 'End month' if initial.get('end') else 'Ongoing'
        specifications = (('kind', 'Type', 0, 0, 2), ('name', 'Name', 1, 0, 2),
                          ('amount', 'Amount', 2, 0, 1), ('currency', 'Currency', 2, 1, 1),
                          ('frequency', 'Schedule', 3, 0, 2), ('start', 'Begins', 4, 0, 2),
                          ('ending', 'Ends', 5, 0, 2), ('count', 'Number of payments', 6, 0, 2),
                          ('end', 'End month', 6, 0, 2))
        choices = {'kind': ('Income', 'Outgoing'), 'currency': self._currency_choices(),
                   'frequency': tuple(_FREQUENCIES.values()),
                   'ending': ('Ongoing', 'Number of payments', 'End month')}
        variables, editors, labels = {}, {}, {}
        for key, title, row, column, span in specifications:
            variable = variables[key] = tk.StringVar(form, value=initial.get(key, ''))
            label = labels[key] = info_label(form, title, HELP['cashflow_' + key] if 'cashflow_' + key in HELP else HELP[key])
            label.grid(row=2 * row, column=column, columnspan=span, sticky='w', pady=(6, 2))
            if key in choices:
                editor = ttk.Combobox(form, textvariable=variable, values=choices[key], state='readonly', width=24)
            else:
                entry = MonthEntry if key in ('start', 'end') else RoundedEntry if key == 'amount' else ttk.Entry
                editor = entry(form, textvariable=variable, width=32 if span == 2 else 20)
            editors[key] = editor
            editor.grid(row=2 * row + 1, column=column, columnspan=span, sticky='ew',
                        padx=(0, 12) if key == 'amount' else 0, pady=(0, 6))
            add_help(editor, label.tooltip.text)

        def update(*_):
            recurring = variables['frequency'].get() != 'Once'
            for key, visible in (('ending', recurring),
                                 ('count', recurring and variables['ending'].get() == 'Number of payments'),
                                 ('end', recurring and variables['ending'].get() == 'End month')):
                for widget in (labels[key], editors[key]):
                    widget.grid() if visible else widget.grid_remove()
            editors['amount'].set_integer_only(variables['currency'].get() == 'HUF')

        for key in ('frequency', 'ending', 'currency'):
            variables[key].trace_add('write', update)
        update()
        return variables, editors['name']

    def _validate(self, row, _old):
        if row['kind'] not in ('Income', 'Outgoing'):
            raise ValueError('Choose Income or Outgoing.')
        frequencies = {label: value for value, label in _FREQUENCIES.items()}
        row['frequency'] = frequencies.get(row['frequency'], row['frequency'])
        if row['frequency'] not in _FREQUENCIES:
            raise ValueError('Choose Once, Monthly or Annually.')
        recurring = row['frequency'] != 'once'
        end_mode = row.get('ending')
        row['count'] = row.get('count', '') if recurring and end_mode == 'Number of payments' else ''
        row['end'] = row.get('end', '') if recurring and end_mode == 'End month' else ''
        if recurring and end_mode == 'Number of payments' and not row['count'].strip():
            raise ValueError('Enter the number of payments.')
        if recurring and end_mode == 'End month' and not row['end'].strip():
            raise ValueError('Choose an end month.')
        return self._row(self._cashflow(row), row['kind'])

    @staticmethod
    def _cashflow(row):
        currency = currency_code(row['currency'])
        try:
            count = int(row['count']) if row.get('count') else None
        except ValueError as error:
            raise ValueError('Number of payments must be a whole number.') from error
        return CashFlow(row['name'].strip(), parse_currency_amount(row['amount'], currency),
                        MonthEntry.validate_month(row['start']), currency, row['frequency'], count,
                        MonthEntry.validate_month(row['end']) if row.get('end') else None)

    @staticmethod
    def _row(flow, kind):
        return dict(kind=kind, name=flow.name, amount=plain_number(flow.amount), currency=flow.currency,
                    frequency=flow.frequency, start=flow.start.isoformat()[:7],
                    count='' if flow.count is None else str(flow.count),
                    end='' if flow.end is None else flow.end.isoformat()[:7], ending='')
