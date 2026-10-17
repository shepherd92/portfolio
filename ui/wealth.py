"""Other assets and liabilities, with their cash flows edited at their source."""

from dataclasses import replace
from datetime import date
from math import fsum
from pathlib import Path
import tkinter as tk
from tkinter import ttk
from uuid import uuid4

from portfolio.calculator import month_after
from portfolio.models import currency_code, parse_currency_amount
from portfolio.wealth import (Loan, Pension, PensionContribution, Property,
                              PropertyCashFlow, WealthSettings)
from portfolio.wealth_storage import load_wealth, save_wealth
from ui.cashflows import CashFlowTable
from ui.charts import PieChart
from ui.dates import MonthEntry
from ui.help import add_help, info_label
from ui.reporting import format_amount, plain_number
from ui.table import DataTable
from ui.widgets import PercentEntry, RoundedEntry


_KINDS = {'pensions': Pension, 'loans': Loan, 'properties': Property}
_LABELS = {'pensions': 'Pension funds', 'loans': 'Loans', 'properties': 'Properties'}
_PENSION_KINDS = {'balance': 'Investment balance', 'income': 'Retirement income'}
_OCCUPANCY = {'always': 'Always', 'rented': 'Rented', 'vacant': 'Vacant'}
_ROLES = {
    'pensions': ('Bank contribution', 'Payroll contribution', 'Employer contribution', 'Pension payment'),
    'loans': ('Repayment',),
    'properties': ('Income', 'Outgoing'),
}
_FIELDS = {
    'pensions': (('name', 'Name', 160), ('kind', 'Type', 135), ('balance', 'Balance', 110),
                 ('currency', 'Currency', 75), ('annual_return', 'Annual return', 105), ('access', 'Access from', 105)),
    'loans': (('name', 'Name', 150), ('balance', 'Outstanding debt', 125), ('currency', 'Currency', 75),
              ('annual_interest', 'Annual interest', 105), ('property_id', 'Property', 145), ('repayment', 'Repayment', 150)),
    'properties': (('name', 'Name', 170), ('value', 'Your market value', 125), ('currency', 'Currency', 75),
                   ('appreciation_percent', 'Appreciation', 110), ('depreciation_percent', 'Depreciation', 110),
                   ('vacancy_percent', 'Vacancy', 100)),
}
_HELP = {
    'name': 'A name identifying this pension, loan or property. Related payments remain linked when renamed. Enter current balances after this month’s payments; linked schedules begin affecting the forecast next month.',
    'currency': 'The currency of this balance. HUF amounts must be whole numbers. Pension and loan payments use this same currency.',
    'value': 'The current market value of your own share of the property. It appears separately from tradable investments. Do not subtract a mortgage here.',
    'balance': 'Current pension savings or outstanding loan principal. Enter only your own share. This is not available trading cash.',
    'kind': 'Investment balance tracks savings, growth and withdrawals. Retirement income records contributions and promised payments without inventing a present asset value or deriving benefits from contributions.',
    'annual_return': 'Expected annual growth of the pension balance, net of fees and taxes. Retirement income has no balance or growth assumption.',
    'access': 'Earliest permitted withdrawal month for an investment pension. Reaching this month does not automatically release the balance; add pension payments below.',
    'annual_interest': 'Effective annual loan interest assumption. Repayments reduce interest first, then principal; amounts are capped at the remaining debt.',
    'property_id': 'Optional link to a property. The full outstanding debt is shown separately; it is never subtracted twice from the property value.',
    'appreciation_percent': 'Annual increase in property market value. This changes valuation only, never Calculator income. Enter 0 if already reflected in another assumption.',
    'depreciation_percent': 'Annual loss in property market value. Annual value factor = (1 + appreciation / 100) × (1 − depreciation / 100). This changes valuation only, never Calculator expenses. Maintenance is a separate payment; avoid counting the same deterioration twice.',
    'vacancy_percent': 'Expected share of time without a tenant, from 0% to 100%. Rented cash flows use 100% minus this rate; Vacant flows use this rate; Always flows apply in full. This affects cash flows, not property market value.',
}
_NOTES = {
    'pensions': 'Contributions and payments belong to this pension. Payroll and employer contributions grow it without reducing bank cash again.',
    'loans': 'Record outstanding principal, then add its repayment schedule below. Debt stays separate from investment targets.',
    'properties': 'Enter your own share of values, rent and costs. Appreciation and depreciation change valuation only; they are never cash flows.',
}
_FLOW_HELP = {
    'pensions': 'Bank contributions reduce available cash. Payroll contributions are already deducted from net salary; employer contributions do not reduce your cash. Pension payments release money from the access month.',
    'loans': 'One repayment schedule per loan, in its currency. Payments include interest and principal, stop when the debt is repaid, and appear automatically in Calculator.',
    'properties': 'Enter your own income and outgoings at their full amount in the Begins month. Each later payment grows by (1 + appreciation / 100) to the power of elapsed months / 12, then is weighted by vacancy. The Calculator and projection use these averaged amounts. Bills paid directly by a tenant are not your cash flows.',
}
_PROPERTY_FLOW_HELP = {
    'occupancy': 'Rented: multiply by (100 − vacancy)% of the property. Vacant: multiply by vacancy%. Always: use 100%. These are expected averages, not a prediction of which months have a tenant.',
    'appreciation_percent': 'Expected annual change of this income or cost. Amount applies in the Begins month; later payments compound monthly at this annual rate. A negative rate models a decrease. A single payment uses its entered amount. This is separate from property value appreciation.',
}


class WealthEditor:
    """Source of pension, property and loan inputs; committed edits save automatically."""

    def __init__(self, master, *, parents, show_page, currency, currency_choices, on_change):
        self._master, self._show_page = master, show_page
        self._currency, self._currency_choices, self._on_change = currency, currency_choices, on_change
        self._directory, self._original, self._load_error = None, None, ''
        self._enabled, self.dirty = True, False
        self._items = {kind: {} for kind in _KINDS}
        self._selected = {kind: None for kind in _KINDS}
        self.tables, self.flows, self.pages, self.flow_titles = {}, {}, {}, {}
        self.current_charts, self.current_titles = {}, {}
        self.message = tk.StringVar(master)
        for kind, title in _LABELS.items():
            self._build_page(parents[kind], kind, title)

    def load(self, directory):
        self._directory, self._load_error = Path(directory), ''
        try:
            settings, self._original = load_wealth(self._directory)
        except (ValueError, OSError) as error:
            settings, self._original = WealthSettings(), None
            self._load_error = f'Cannot load other assets: {error}'
        self._items = {kind: {item.id: item for item in getattr(settings, kind)} for kind in _KINDS}
        self._selected = {kind: None for kind in _KINDS}
        self.dirty = False
        self.message.set(self._load_error)
        for kind in _KINDS:
            self._refresh_records(kind)
        self.set_enabled(self._enabled)

    def save(self):
        if not self.dirty:
            return True
        try:
            if self._directory is None:
                raise ValueError('Open a portfolio directory before saving other assets.')
            self._original = save_wealth(self.settings(), self._directory, self._original)
        except (ValueError, OSError) as error:
            self.message.set(f'Cannot save other assets: {error}')
            return False
        self.dirty = False
        self.message.set('')
        return True

    def settings(self):
        if self._load_error:
            raise ValueError(self._load_error)
        return WealthSettings(**{kind: tuple(items.values()) for kind, items in self._items.items()})

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        available = self._enabled and not self._load_error
        for kind in _KINDS:
            self.tables[kind].set_enabled(available)
            self.flows[kind].set_enabled(available and self._selected[kind] is not None)

    def refresh_allocations(self, values, currency, colors, *, errors=None):
        """Show converted current values in the caller's stable order, without targets."""
        for kind, chart in self.current_charts.items():
            error = (errors or {}).get(kind, '')
            allocation = {} if error else {name: value for name, value in values.get(kind, {}).items() if value > 0}
            try:
                total = fsum(allocation.values())
            except OverflowError:
                error, allocation, total = 'Total exceeds the supported numeric range', {}, 0
            chart.set_data(allocation, colors.get(kind, {}),
                           empty_message=error or ('No property holdings' if kind == 'properties' else 'No pension balances'))
            value = 'unavailable' if error else f'{format_amount(total)} {currency}'
            self.current_titles[kind].set(f'Current allocation · {value}')

    @property
    def required_currencies(self):
        currencies = set()
        for item in self._items['properties'].values():
            currencies.add(item.currency)
            currencies.update(entry.flow.currency for entry in item.cashflows)
        for kind in ('pensions', 'loans'):
            currencies.update(item.currency for item in self._items[kind].values())
        return currencies

    def select_source(self, source_id):
        """Focus the originating record of a read-only Calculator payment."""
        for kind, items in self._items.items():
            if source_id not in items:
                continue
            self._show_page(kind)
            table = self.tables[kind]
            item = next(item for item in table.tree.get_children() if table.row(item)['id'] == source_id)
            table.tree.selection_set(item)
            table.tree.see(item)
            table.tree.focus(item)
            self._select(kind)
            return True
        return False

    def _build_page(self, parent, kind, title):
        page = self.pages[kind] = ttk.Frame(parent, padding=10)
        page.columnconfigure(0, weight=1)
        page.rowconfigure((1, 3), weight=1)
        charted = kind != 'loans'
        if charted:
            page.columnconfigure((0, 1), weight=1, uniform='wealth')
            chart_title = self.current_titles[kind] = tk.StringVar(page, value='Current allocation')
            help_text = ('Each slice is one property’s current market value, converted to the base currency. '
                         'Loans remain separate. There is no trading target for properties.' if kind == 'properties' else
                         'Each slice is one pension investment balance, converted to the base currency. '
                         'Future retirement income has no present asset value. There is no trading target for pensions.')
            info_label(page, '', help_text, textvariable=chart_title).grid(
                row=0, column=1, sticky='w', pady=(0, 7), padx=(6, 0))
            chart = self.current_charts[kind] = PieChart(page, width=380, height=230)
            chart.grid(row=1, column=1, sticky='nsew', padx=(6, 0))
        info_label(page, title, _NOTES[kind]).grid(row=0, column=0, sticky='w', pady=(0, 7))
        table = self.tables[kind] = _RecordTable(page, self, kind)
        table.grid(row=1, column=0, sticky='nsew', padx=(0, 6) if charted else 0)
        table.tree.bind('<<TreeviewSelect>>', lambda _event, kind=kind: self._select(kind), add='+')
        self.flow_titles[kind] = tk.StringVar(self._master, value='Select a record to edit its payments')
        label = info_label(page, 'Linked payments', _FLOW_HELP[kind])
        label.grid(row=2, column=0, columnspan=1 + int(charted), sticky='w', pady=(14, 7))
        label.label.configure(textvariable=self.flow_titles[kind])
        flows = self.flows[kind] = _LinkedFlowTable(page, self, kind)
        flows.grid(row=3, column=0, columnspan=1 + int(charted), sticky='nsew')
        flows.set_enabled(False)
        ttk.Label(page, textvariable=self.message, wraplength=900).grid(
            row=4, column=0, columnspan=1 + int(charted), sticky='w', pady=(5, 0))

    def _record(self, kind, row):
        values = {key: row[key] for key, _, _ in _FIELDS[kind] if key != 'repayment'}
        values['currency'] = currency_code(values['currency'])
        for key in ('value', 'balance'):
            if key in values:
                values[key] = parse_currency_amount(values[key], values['currency'])
        for key in ('annual_return', 'annual_interest', 'appreciation_percent', 'depreciation_percent', 'vacancy_percent'):
            if key in values:
                values[key] = _percentage(values[key])
        if kind == 'pensions':
            values['kind'] = _key(_PENSION_KINDS, values['kind'])
            values['access'] = MonthEntry.validate_month(values['access']) if values['access'] else None
        if kind == 'loans':
            values['property_id'] = _key(self._property_choices(), values['property_id']) or None
        values['name'] = values['name'].strip()
        original = self._items[kind].get(row.get('id'))
        return replace(original, **values) if original else _KINDS[kind](id=row.get('id') or str(uuid4()), **values)

    def _validate_record(self, kind, row, _old):
        record = self._record(kind, row)
        self._validate_replacement(kind, record)
        return self._record_row(kind, record)

    def _validate_replacement(self, kind, record):
        items = {**self._items[kind], record.id: record}
        WealthSettings(**{key: tuple((items if key == kind else values).values())
                          for key, values in self._items.items()})

    def _record_changed(self, kind, action, old, row):
        if action == 'remove':
            del self._items[kind][old['id']]
            self._selected[kind] = None
        else:
            record = self._record(kind, row)
            self._items[kind][record.id] = record
            self._selected[kind] = record.id
        self._refresh_records(kind)
        if kind == 'properties':
            self._refresh_records('loans')
        self._changed()

    def _can_delete(self, kind, row):
        if kind == 'properties' and any(loan.property_id == row['id'] for loan in self._items['loans'].values()):
            raise ValueError('Unlink this property from its loan before removing it.')

    def _record_row(self, kind, record):
        row = {key: plain_number(getattr(record, key)) for key, _, _ in _FIELDS[kind] if key != 'repayment'}
        row['id'] = record.id
        if kind == 'pensions':
            row['access'] = record.access.isoformat()[:7] if record.access else ''
        if kind == 'loans':
            row['property_id'] = record.property_id or ''
            row['repayment'] = (f'{format_amount(record.payment.amount)} {record.payment.currency}'
                                if record.payment else 'No repayment schedule')
        return row

    def _refresh_records(self, kind):
        table = self.tables[kind]
        table.set_rows([self._record_row(kind, item) for item in self._items[kind].values()])
        for item in table.tree.get_children():
            if table.row(item)['id'] == self._selected[kind]:
                table.tree.selection_set(item)
                break
        self._select(kind)

    def _select(self, kind):
        selection = self.tables[kind].tree.selection()
        selected = self.tables[kind].row(selection[0])['id'] if selection else None
        self._selected[kind] = selected
        record = self._items[kind].get(selected)
        title = 'Cash flows' if kind == 'properties' else 'Linked payments'
        self.flow_titles[kind].set(f'{record.name} · {title}' if record else 'Select a record to edit its payments')
        self.flows[kind].set_rows(self._flow_rows(kind, record) if record else [])
        self.flows[kind].set_enabled(self._enabled and not self._load_error and record is not None)

    def _flow_rows(self, kind, record):
        flows = []
        if kind == 'properties':
            return [dict(CashFlowTable._row(entry.flow, entry.direction.capitalize()),
                         occupancy=entry.occupancy, appreciation_percent=plain_number(entry.appreciation_percent))
                    for entry in record.cashflows]
        elif kind == 'pensions':
            roles = {'bank': 'Bank contribution', 'payroll': 'Payroll contribution', 'employer': 'Employer contribution'}
            flows.extend((roles[entry.source], entry.flow) for entry in record.contributions)
            flows.extend(('Pension payment', flow) for flow in record.withdrawals)
        elif record.payment:
            flows.append(('Repayment', record.payment))
        return [CashFlowTable._row(flow, role) for role, flow in flows]

    def _with_flows(self, kind, rows):
        record = self._items[kind][self._selected[kind]]
        if kind == 'properties':
            return replace(record, cashflows=tuple(PropertyCashFlow(
                CashFlowTable._cashflow(row), row['kind'].lower(), _key(_OCCUPANCY, row['occupancy']),
                _percentage(row['appreciation_percent'])) for row in rows))
        flows = [(row['kind'], CashFlowTable._cashflow(row)) for row in rows]
        if kind == 'pensions':
            roles = {'Bank contribution': 'bank', 'Payroll contribution': 'payroll', 'Employer contribution': 'employer'}
            return replace(record, withdrawals=tuple(flow for role, flow in flows if role == 'Pension payment'),
                           contributions=tuple(PensionContribution(flow, roles[role]) for role, flow in flows
                                               if role != 'Pension payment'))
        if len(flows) > 1:
            raise ValueError('A loan has one repayment schedule. Edit its existing repayment row.')
        return replace(record, payment=flows[0][1] if flows else None)

    def _flows_changed(self, kind, *_):
        record = self._with_flows(kind, self.flows[kind].get_rows())
        self._items[kind][record.id] = record
        self._refresh_records(kind)
        self._changed()

    def _property_choices(self):
        items = tuple(self._items['properties'].values())
        return {'': 'None', **{item.id: item.name if item.name != 'None'
                              else f'{item.name} [{item.id}]' for item in items}}

    def _changed(self):
        self.dirty = True
        self.save()
        self._on_change()


class _RecordTable(DataTable):
    def __init__(self, parent, view, kind):
        self.view, self.kind = view, kind
        rows = (('name',), ('balance', 'currency'), ('kind', 'annual_return'), ('access',)) if kind == 'pensions' else (
            (('name',), ('balance', 'currency'), ('annual_interest', 'property_id')) if kind == 'loans' else
            (('name',), ('value', 'currency'), ('appreciation_percent', 'depreciation_percent'), ('vacancy_percent',)))
        super().__init__(parent, _FIELDS[kind], height=5, field_rows=rows, dialog_title={'pensions': 'pension', 'loans': 'loan', 'properties': 'property'}[kind],
                         rounded_columns=('value', 'balance'),
                         percent_columns=('annual_return', 'annual_interest', 'appreciation_percent', 'depreciation_percent', 'vacancy_percent'),
                         validate=lambda row, old: view._validate_record(kind, row, old),
                         on_change=lambda *args: view._record_changed(kind, *args),
                         can_delete=lambda row: view._can_delete(kind, row))

    def display_value(self, row, key):
        if key == 'kind':
            return _PENSION_KINDS.get(row[key], row[key])
        if key == 'property_id':
            return self.view._property_choices().get(row[key], row[key])
        return super().display_value(row, key)

    def sort_value(self, row, key):
        return self.display_value(row, key) if key in ('kind', 'property_id') else super().sort_value(row, key)

    def _edit_fields(self, form, old):
        initial = {key: '0' for key in self.rounded_columns | self.percent_columns}
        initial.update(currency=self.view._currency.get(), kind='balance', property_id='', name='', access='')
        initial.update(old or {})
        choices = {'currency': tuple(self.view._currency_choices()), 'kind': tuple(_PENSION_KINDS.values()),
                   'property_id': tuple(self.view._property_choices().values())}
        for key, mapping in (('kind', _PENSION_KINDS), ('property_id', self.view._property_choices())):
            initial[key] = mapping.get(initial[key], initial[key])
        variables, editors = {}, {}
        labels = {key: title for key, title, _ in self.columns}
        for index, fields in enumerate(self.field_rows):
            for column, key in enumerate(fields):
                span = 2 if len(fields) == 1 else 1
                info_label(form, labels[key], _HELP[key]).grid(row=index * 2, column=column, columnspan=span,
                                                             sticky='w', padx=(0, 10), pady=(6, 2))
                variable = variables[key] = tk.StringVar(form, value=initial[key])
                if key in choices:
                    editor = ttk.Combobox(form, textvariable=variable, values=choices[key], state='readonly', width=24)
                else:
                    entry = MonthEntry if key == 'access' else PercentEntry if key in self.percent_columns else RoundedEntry if key in self.rounded_columns else ttk.Entry
                    editor = entry(form, textvariable=variable, width=24)
                editors[key] = editor
                editor.grid(row=index * 2 + 1, column=column, columnspan=span, sticky='ew', padx=(0, 10), pady=(0, 6))
                add_help(editor, _HELP[key])
        def update_currency(*_):
            for key in ('value', 'balance'):
                if key in editors:
                    editors[key].set_integer_only(variables['currency'].get() == 'HUF')
        variables['currency'].trace_add('write', update_currency)
        update_currency()
        return variables, editors['name']


class _LinkedFlowTable(CashFlowTable):
    def __init__(self, parent, view, kind):
        self.view, self.kind = view, kind
        extra = (('occupancy', 'Occupancy', 95), ('appreciation_percent', 'Appreciation', 110)) if kind == 'properties' else ()
        super().__init__(parent, currency_choices=self._currencies,
                         extra_columns=extra, percent_columns=('appreciation_percent',) if extra else (),
                         on_change=lambda *args: view._flows_changed(kind, *args))
        if extra:
            self.field_rows += (('occupancy', 'appreciation_percent'),)
        if kind != 'properties':
            self.tree.heading('kind', text='Payment type')
            self.tree.column('kind', width=155)
        add_help(self.tree, _FLOW_HELP[kind])

    def display_value(self, row, key):
        return _OCCUPANCY[row[key]] if key == 'occupancy' else super().display_value(row, key)

    def _currencies(self):
        if self.kind == 'properties':
            return self.view._currency_choices()
        return (self.view._items[self.kind][self.view._selected[self.kind]].currency,)

    def _edit_fields(self, form, old):
        selected = self.view._items[self.kind][self.view._selected[self.kind]]
        initial = dict(start=month_after(date.today().replace(day=1), 1).isoformat()[:7], frequency='monthly')
        initial.update(old or {}, currency=(old or {}).get('currency', selected.currency))
        variables, first = super()._edit_fields(form, initial)
        editor = next(widget for widget in form.winfo_children() if isinstance(widget, ttk.Combobox)
                      and str(widget.cget('textvariable')) == str(variables['kind']))
        roles = _ROLES[self.kind]
        editor.configure(values=roles, width=24)
        variables['kind'].set((old or {}).get('kind', 'Outgoing' if self.kind == 'properties' else roles[0]))
        if self.kind == 'properties':
            row = 2 * (len(self.field_rows) - 1)
            for column, (key, title) in enumerate((('occupancy', 'Occupancy'), ('appreciation_percent', 'Appreciation'))):
                value = (old or {}).get(key, 'always' if key == 'occupancy' else '0')
                variable = variables[key] = tk.StringVar(form, value=_OCCUPANCY.get(value, value) if key == 'occupancy' else value)
                info_label(form, title, _PROPERTY_FLOW_HELP[key]).grid(row=row, column=column, sticky='w', pady=(6, 2))
                if key == 'occupancy':
                    field = ttk.Combobox(form, textvariable=variable, values=tuple(_OCCUPANCY.values()), state='readonly', width=24)
                else:
                    field = PercentEntry(form, textvariable=variable, width=24)
                field.grid(row=row + 1, column=column, sticky='ew', padx=(0, 12) if column == 0 else 0, pady=(0, 6))
                add_help(field, _PROPERTY_FLOW_HELP[key])
        return variables, first

    def _validate(self, row, old):
        role = row['kind']
        if role not in _ROLES[self.kind]:
            raise ValueError('Select a valid payment type.')
        normalized = super()._validate({**row, 'kind': 'Outgoing'}, old)
        normalized['kind'] = role
        if self.kind == 'properties':
            normalized.update(occupancy=_key(_OCCUPANCY, row['occupancy']),
                              appreciation_percent=plain_number(_percentage(row['appreciation_percent'])))
        rows = self.get_rows()
        if old is None:
            rows.append(normalized)
        else:
            rows[rows.index(old)] = normalized
        self.view._validate_replacement(self.kind, self.view._with_flows(self.kind, rows))
        return normalized


def _key(mapping, value):
    if value in mapping:
        return value
    try:
        return next(key for key, label in mapping.items() if label == value)
    except StopIteration as error:
        raise ValueError('Choose a listed option.') from error


def _percentage(value):
    try:
        return float(str(value).rstrip('%').strip())
    except ValueError as error:
        raise ValueError('Enter a valid percentage.') from error
