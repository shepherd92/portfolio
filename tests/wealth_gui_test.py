"""Other assets keep editable source records and generated payments together."""

from datetime import date
import os
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch

from portfolio.wealth import Loan, Pension, Property, WealthSettings
from portfolio.wealth_storage import load_wealth, save_wealth
from ui.dates import MonthEntry
from ui.theme import configure_theme
from ui.wealth import WealthEditor


@unittest.skipUnless(os.environ.get('REBALANCE_GUI_TESTS') == '1', 'GUI tests require a display')
class WealthGuiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        configure_theme(self.root)
        self.changed = Mock()
        self.currency = tk.StringVar(self.root, value='HUF')
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill='both', expand=True)
        self.view = WealthEditor(self.root,
                                parents={kind: self.notebook for kind in ('pensions', 'loans', 'properties')},
                                show_page=lambda kind: self.notebook.select(self.view.pages[kind]),
                                currency=self.currency, currency_choices=lambda: ('EUR', 'HUF', 'GBP'),
                                on_change=self.changed)
        for kind, page in self.view.pages.items():
            self.notebook.add(page, text=kind)
        self.view.load(self.directory)

    def test_one_controller_places_pages_in_distinct_parents_and_routes_sources(self):
        self.seed(properties=(Property('flat', 'Apartment', 100000, 'EUR'),),
                  loans=(Loan('loan', 'Mortgage', 50000, currency='EUR', property_id='flat'),))
        parents = {kind: ttk.Frame(self.root) for kind in ('pensions', 'loans', 'properties')}
        show_page = Mock()
        editor = WealthEditor(self.root, parents=parents, show_page=show_page, currency=self.currency,
                             currency_choices=lambda: ('EUR', 'HUF'), on_change=self.changed)
        editor.load(self.directory)
        self.assertEqual(editor.settings(), self.view.settings())
        for kind, page in editor.pages.items():
            self.assertIs(page.master, parents[kind])
            status = [widget for widget in page.winfo_children() if isinstance(widget, ttk.Label)
                      and str(widget.cget('textvariable')) == str(editor.message)]
            self.assertEqual(len(status), 1)
        self.assertTrue(editor.select_source('loan'))
        show_page.assert_called_once_with('loans')
        self.assertEqual(editor._selected['loans'], 'loan')
        editor.set_enabled(False)
        self.assertTrue(all(not table._enabled for table in (*editor.tables.values(), *editor.flows.values())))
        self.assertEqual(editor.settings().loans[0].property_id, 'flat')
        self.assertFalse(editor.dirty)

    def test_current_charts_preserve_order_colors_and_inputs_when_display_changes(self):
        self.seed(properties=(Property('flat', 'Apartment', 100000, 'EUR'),
                              Property('house', 'House', 500000, 'EUR')),
                  pensions=(Pension('pension', 'Pension', 'EUR', balance=10000, access=date(2050, 1, 1)),))
        saved = (self.directory / 'wealth.json').read_bytes()
        colors = {'properties': {'House': '#ff0000', 'Apartment': '#0000ff'},
                  'pensions': {'Pension': '#00ff00'}}
        self.view.refresh_allocations({'properties': {'House': 500000, 'Apartment': 100000},
                                       'pensions': {'Pension': 10000}}, 'EUR', colors)
        chart = self.view.current_charts['properties']
        self.assertEqual(list(chart._data), ['House', 'Apartment'])
        self.assertEqual(chart._colors, colors['properties'])
        self.assertEqual(self.view.current_titles['properties'].get(), 'Current allocation · 600 000 EUR')
        self.view.refresh_allocations({'properties': {'House': 1000, 'Apartment': 10000000}}, 'HUF', colors)
        self.assertEqual(list(chart._data), ['House', 'Apartment'])
        self.assertEqual(chart._colors, colors['properties'])
        self.assertTrue(self.view.current_titles['properties'].get().endswith(' HUF'))
        self.assertEqual((self.directory / 'wealth.json').read_bytes(), saved)
        self.changed.assert_not_called()

    def test_missing_exchange_rate_clears_chart_and_recovery_restores_it(self):
        values = {'properties': {'Apartment': 200000}, 'pensions': {'Pension': 10000}}
        self.view.refresh_allocations(values, 'EUR', {})
        self.view.refresh_allocations(values, 'HUF', {}, errors={'properties': 'Missing EUR exchange rate'})
        chart = self.view.current_charts['properties']
        self.assertFalse(chart._data)
        self.assertEqual(chart._empty_message, 'Missing EUR exchange rate')
        self.assertEqual(self.view.current_titles['properties'].get(), 'Current allocation · unavailable')
        self.assertEqual(self.view.current_charts['pensions']._data, values['pensions'])
        self.view.refresh_allocations(values, 'EUR', {})
        self.assertEqual(chart._data, values['properties'])
        self.assertEqual(self.view.current_titles['properties'].get(), 'Current allocation · 200 000 EUR')

    def test_empty_charts_have_no_targets_or_loan_chart(self):
        self.view.refresh_allocations({'properties': {'Apartment': 0}}, 'EUR', {})
        self.assertEqual(set(self.view.current_charts), {'properties', 'pensions'})
        self.assertFalse(hasattr(self.view, 'target_charts'))
        for kind, chart in self.view.current_charts.items():
            self.assertFalse(chart._data)
            self.assertEqual(self.view.current_titles[kind].get(), 'Current allocation · 0 EUR')
        self.assertEqual(self.view.current_charts['pensions']._empty_message, 'No pension balances')

    def test_current_charts_and_editor_controls_fit_above_linked_payments(self):
        self.root.deiconify()
        self.root.geometry('1050x650')
        for kind in ('properties', 'pensions'):
            page = self.view.pages[kind]
            self.notebook.select(page)
            self.root.update()
            chart = self.view.current_charts[kind]
            table = self.view.tables[kind]
            self.assertTrue(chart.winfo_viewable())
            self.assertGreater(chart.winfo_height(), 100)
            self.assertLess(table.winfo_rootx(), chart.winfo_rootx())
            self.assertLessEqual(abs(table.winfo_width() - chart.winfo_width()), 1)
            self.assertEqual(table.winfo_rooty(), chart.winfo_rooty())
            self.assertEqual(table.winfo_height(), chart.winfo_height())
            for table in (self.view.tables[kind], self.view.flows[kind]):
                self.assertTrue(table.add_button.winfo_viewable())
                self.assertGreater(table.tree.winfo_height(), 80)
                self.assertLess(table.add_button.winfo_rooty() + table.add_button.winfo_height(),
                                page.winfo_rooty() + page.winfo_height())

    def test_add_property_autosaves_own_value_without_creating_income(self):
        self.edit('properties', dict(name='Apartment', value='40000000', appreciation_percent='3.125',
                                     depreciation_percent='0.5', vacancy_percent='12.5%'))
        settings = load_wealth(self.directory)[0]
        self.assertEqual(settings, self.view.settings())
        apartment = settings.properties[0]
        self.assertEqual((apartment.value, apartment.appreciation_percent, apartment.depreciation_percent),
                         (40000000, 3.125, .5))
        self.assertFalse(apartment.cashflows)
        self.assertEqual(apartment.vacancy_percent, 12.5)
        self.assertFalse(settings.events(date(2026, 1, 1), date(2027, 1, 1)))
        row = self.view.tables['properties'].tree.get_children()[0]
        self.assertEqual(self.view.tables['properties'].tree.set(row, 'appreciation_percent'), '3.12%')
        self.assertFalse(self.view.dirty)
        self.changed.assert_called_once()

    def test_overflowing_property_total_clears_only_its_chart(self):
        self.view.refresh_allocations({'properties': {'First': 1e308, 'Second': 1e308},
                                       'pensions': {'Fund': 100}}, 'EUR', {})
        self.assertFalse(self.view.current_charts['properties']._data)
        self.assertIn('numeric range', self.view.current_charts['properties']._empty_message)
        self.assertEqual(self.view.current_titles['properties'].get(), 'Current allocation · unavailable')
        self.assertEqual(self.view.current_charts['pensions']._data, {'Fund': 100})

    def test_property_cash_flows_keep_currency_condition_growth_and_selected_record(self):
        self.seed(properties=(Property('flat', 'Apartment', 100000, 'EUR', vacancy_percent=25),
                              Property('house', 'House', 500000, 'EUR')))
        self.assertTrue(self.view.select_source('flat'))
        self.edit('properties', dict(kind='Income', occupancy='Rented', appreciation_percent='3.125%', name='Rent', amount='900', currency='EUR',
                                     frequency='Monthly', start='2027-01', ending='Ongoing'), flow=True)
        self.edit('properties', dict(kind='Outgoing', occupancy='Vacant', appreciation_percent='2%', name='Power', amount='90', currency='GBP',
                                     frequency='Monthly', start='2027-01', ending='Ongoing'), flow=True)
        flat, house = self.view.settings().properties
        self.assertEqual([(entry.flow.amount, entry.direction, entry.occupancy, entry.appreciation_percent) for entry in flat.cashflows],
                         [(900, 'income', 'rented', 3.125), (90, 'outgoing', 'vacant', 2)])
        self.assertEqual(flat.cashflows[1].flow.currency, 'GBP')
        self.assertFalse(house.cashflows)
        table = self.view.flows['properties']
        rows = table.tree.get_children()
        self.assertEqual(table.tree.set(rows[0], 'occupancy'), 'Rented')
        self.assertEqual(table.tree.set(rows[0], 'appreciation_percent'), '3.12%')
        self.assertEqual(self.view.required_currencies, {'EUR', 'GBP'})
        self.assertEqual(self.view.settings(), load_wealth(self.directory)[0])

    def test_linked_payment_edit_preserves_other_payments(self):
        self.seed(properties=(Property('flat', 'Apartment', 100000, 'EUR'),))
        self.view.select_source('flat')
        self.edit('properties', dict(kind='Outgoing', occupancy='Always', name='Tax', amount='100', currency='EUR',
                                     frequency='Annually', start='2027-06', ending='Ongoing'), flow=True)
        self.edit('properties', dict(kind='Outgoing', occupancy='Vacant', name='Utilities', amount='60', currency='EUR',
                                     frequency='Monthly', start='2027-01', ending='Ongoing'), flow=True)
        table = self.view.flows['properties']
        tax = next(item for item in table.tree.get_children() if table.row(item)['name'] == 'Tax')
        self.edit('properties', dict(amount='120'), flow=True, item=tax)
        flows = self.view.settings().properties[0].cashflows
        self.assertEqual([(entry.flow.name, entry.flow.amount, entry.occupancy) for entry in flows],
                         [('Tax', 120, 'always'), ('Utilities', 60, 'vacant')])

    def test_vacancy_is_a_percentage_and_invalid_edits_preserve_saved_inputs(self):
        self.seed(properties=(Property('flat', 'Apartment', 100000, 'EUR'),))
        table = self.view.tables['properties']
        item = table.tree.get_children()[0]
        before = (self.directory / 'wealth.json').read_bytes()
        for rate in ('-1', '100.01', 'nan'):
            self.edit('properties', dict(vacancy_percent=rate), item=item, error='Vacancy')
            self.assertEqual((self.directory / 'wealth.json').read_bytes(), before)

        def inspect(_form, variables):
            self.assertIn('vacancy_percent', variables)
            self.assertNotIn('occupancy', variables)
        self.edit('properties', dict(vacancy_percent='12.345'), item=item, inspect=inspect)
        self.assertEqual(self.view.settings().properties[0].vacancy_percent, 12.345)
        self.assertEqual(table.tree.set(table.tree.get_children()[0], 'vacancy_percent'), '12.34%')

    def test_property_flow_uses_occupancy_dropdown_and_rejects_invalid_growth(self):
        self.seed(properties=(Property('flat', 'Apartment', 100000, 'EUR'),))
        self.view.select_source('flat')

        def inspect(form, variables):
            field = next(widget for widget in form.winfo_children() if isinstance(widget, ttk.Combobox)
                         and str(widget.cget('textvariable')) == str(variables['occupancy']))
            self.assertEqual(tuple(field['values']), ('Always', 'Rented', 'Vacant'))
            self.assertEqual(str(field['state']), 'readonly')
            buttons = next(widget for widget in form.winfo_children()
                           if any(isinstance(child, ttk.Button) and child['text'] == 'Save'
                                  for child in widget.winfo_children()))
            self.assertGreater(int(buttons.grid_info()['row']), int(field.grid_info()['row']))
        self.edit('properties', dict(kind='Income', occupancy='Rented', name='Rent', amount='1000',
                                     frequency='Monthly', start='2027-01', ending='Ongoing',
                                     appreciation_percent='3'), flow=True, inspect=inspect)
        table = self.view.flows['properties']
        item = table.tree.get_children()[0]
        before = (self.directory / 'wealth.json').read_bytes()
        self.edit('properties', dict(appreciation_percent='-100'), flow=True, item=item, error='Appreciation')
        self.assertEqual((self.directory / 'wealth.json').read_bytes(), before)
        self.edit('properties', dict(kind='Income', occupancy='Always', name='Reimbursement', amount='20',
                                     frequency='Monthly', start='2027-01', ending='Ongoing'), flow=True)
        self.assertEqual(len(self.view.settings().properties[0].cashflows), 2)

    def test_pension_access_uses_month_picker_and_payroll_is_not_bank_cash(self):
        def inspect(form, variables):
            self.assertTrue(any(isinstance(widget, MonthEntry) for widget in form.winfo_children()))
            self.assertEqual(variables['currency'].get(), 'EUR')
        self.edit('pensions', dict(name='Work pension', kind='Investment balance', balance='10000', currency='EUR',
                                   annual_return='4.25%', access='2050-06'), inspect=inspect)
        pension = self.view.settings().pensions[0]
        self.assertEqual(pension.access, date(2050, 6, 1))
        self.view.select_source(pension.id)
        self.edit('pensions', dict(kind='Payroll contribution', name='Payroll', amount='200', currency='EUR',
                                   frequency='Monthly', start='2027-01', ending='Ongoing'), flow=True)
        self.assertEqual(self.view.settings().pensions[0].contributions[0].source, 'payroll')
        self.assertFalse(self.view.settings().events(date(2026, 12, 1), date(2027, 1, 1)))

    def test_pension_withdrawal_before_access_is_rejected_without_saving(self):
        self.seed(pensions=(Pension('work', 'Pension', 'EUR', balance=1000, access=date(2050, 1, 1)),))
        self.view.select_source('work')
        before = (self.directory / 'wealth.json').read_bytes()
        self.edit('pensions', dict(kind='Pension payment', name='Early withdrawal', amount='10', currency='EUR',
                                   frequency='Once', start='2040-01'), flow=True, error='access month')
        self.assertEqual((self.directory / 'wealth.json').read_bytes(), before)

    def test_retirement_income_does_not_require_an_investment_balance(self):
        self.edit('pensions', dict(name='Public pension', kind='Retirement income', currency='EUR'))
        pension = self.view.settings().pensions[0]
        self.assertEqual(pension.kind, 'income')
        self.assertEqual(pension.balance, 0)
        self.view.select_source(pension.id)
        self.edit('pensions', dict(kind='Pension payment', name='Retirement payment', amount='700', currency='EUR',
                                   frequency='Monthly', start='2050-01', ending='Ongoing'), flow=True)
        self.assertEqual(self.view.settings().pensions[0].withdrawals[0].amount, 700)

    def test_income_entitlement_allows_bank_payroll_and_employer_contributions(self):
        self.seed(pensions=(Pension('entitlement', 'Future pension', 'EUR', kind='income'),))
        self.view.select_source('entitlement')
        for role in ('Bank contribution', 'Payroll contribution', 'Employer contribution'):
            self.edit('pensions', dict(kind=role, name=role, amount='100', currency='EUR',
                                       frequency='Monthly', start='2027-01', ending='Ongoing'), flow=True)
        pension = self.view.settings().pensions[0]
        self.assertEqual(pension.balance, 0)
        self.assertEqual([entry.source for entry in pension.contributions], ['bank', 'payroll', 'employer'])
        events = self.view.settings().events(date(2026, 12, 1), date(2027, 1, 1))
        self.assertEqual([(event.direction, event.amount) for event in events], [('outgoing', 100)])
        self.assertEqual(self.view.settings(), load_wealth(self.directory)[0])

    def test_loan_can_link_property_and_has_one_repayment_schedule(self):
        self.seed(properties=(Property('flat', 'Apartment', 100000, 'EUR'),))
        self.edit('loans', dict(name='Mortgage', balance='50000', currency='EUR', annual_interest='4', property_id='Apartment'))
        loan = self.view.settings().loans[0]
        self.assertEqual(loan.property_id, 'flat')
        self.assertIsNone(loan.payment)
        self.view.select_source(loan.id)
        self.edit('loans', dict(kind='Repayment', name='Mortgage payment', amount='500', currency='EUR',
                                frequency='Monthly', start='2027-01', ending='Number of payments', count='120'), flow=True)
        self.assertEqual(self.view.settings().loans[0].payment.count, 120)
        self.edit('loans', dict(kind='Repayment', name='Duplicate', amount='500', currency='EUR',
                                frequency='Monthly', start='2027-01', ending='Ongoing'), flow=True,
                  error='one repayment schedule')
        with self.assertRaisesRegex(ValueError, 'Unlink'):
            self.view._can_delete('properties', {'id': 'flat'})

    def test_huf_fraction_rejected_and_other_currencies_keep_precision(self):
        self.edit('properties', dict(name='HUF flat', value='10.2'), error='whole')
        self.assertFalse(self.view.settings().properties)
        self.edit('properties', dict(name='EUR flat', value='10.25', currency='EUR'))
        self.assertEqual(self.view.settings().properties[0].value, 10.25)
        table = self.view.tables['properties']
        self.assertEqual(table.tree.set(table.tree.get_children()[0], 'value'), '10')

    def test_external_change_preserved_and_unsaved_edit_reported(self):
        self.seed(properties=(Property('flat', 'Apartment', 100000, 'EUR'),))
        path = self.directory / 'wealth.json'
        changed = path.read_bytes() + b'\n'
        path.write_bytes(changed)
        table = self.view.tables['properties']
        self.edit('properties', dict(value='120000'), item=table.tree.get_children()[0])
        self.assertTrue(self.view.dirty)
        self.assertIn('changed outside', self.view.message.get())
        self.assertFalse(self.view.save())
        self.assertEqual(path.read_bytes(), changed)
        self.assertEqual(self.view.settings().properties[0].value, 120000)

    def test_invalid_file_disables_edits_and_does_not_overwrite_it(self):
        path = self.directory / 'wealth.json'
        path.write_text('{invalid json}')
        self.view.load(self.directory)
        self.assertIn('Cannot load other assets', self.view.message.get())
        self.assertTrue(self.view.save(), 'An unmodified corrupt file must not block closing or changing directories.')
        self.assertFalse(self.view.tables['properties']._enabled)
        self.view.set_enabled(True)
        self.assertFalse(self.view.tables['properties']._enabled)
        self.assertEqual(path.read_text(), '{invalid json}')
        with self.assertRaisesRegex(ValueError, 'Cannot load'):
            self.view.settings()

    def test_source_navigation_disable_and_column_sorting(self):
        self.seed(properties=(Property('large', 'Large', 200, 'EUR'), Property('small', 'Small', 100, 'EUR')),
                  loans=(Loan('loan', 'Loan', 50, currency='EUR'),))
        self.assertFalse(self.view.select_source('missing'))
        self.assertTrue(self.view.select_source('loan'))
        self.assertEqual(self.notebook.select(), str(self.view.pages['loans']))
        self.assertTrue(self.view.flows['loans']._enabled)
        self.view.set_enabled(False)
        self.assertFalse(self.view.flows['loans']._enabled)
        self.assertFalse(self.view.tables['properties']._enabled)
        self.view.set_enabled(True)
        self.assertTrue(self.view.flows['loans']._enabled)
        table = self.view.tables['properties']
        self.root.tk.call(table.tree.heading('value', 'command'))
        values = [float(table.row(item)['value']) for item in table.tree.get_children()]
        self.assertEqual(values, sorted(values))

    def test_duplicate_record_name_is_rejected_without_changing_saved_record(self):
        self.seed(properties=(Property('one', 'Apartment', 100, 'EUR'),))
        self.edit('properties', dict(name='Apartment', value='200', currency='EUR'), error='distinct names')
        self.assertEqual(len(self.view.settings().properties), 1)
        self.assertEqual(self.view.settings().properties[0].value, 100)

    def seed(self, **values):
        save_wealth(WealthSettings(**values), self.directory, None)
        self.view.load(self.directory)

    def edit(self, kind, values, *, flow=False, item=None, error=None, inspect=None):
        table = self.view.flows[kind] if flow else self.view.tables[kind]
        original = table._edit_fields
        captured = {}
        def fields(form, old):
            variables, first = original(form, old)
            captured.update(variables=variables, form=form)
            return variables, first
        def interact(dialog, _first):
            for key, value in values.items():
                captured['variables'][key].set(value)
            if inspect:
                inspect(captured['form'], captured['variables'])
            save = next(widget for frame in captured['form'].winfo_children() for widget in frame.winfo_children()
                        if isinstance(widget, ttk.Button) and widget['text'] == 'Save')
            save.invoke()
            if dialog.winfo_exists():
                dialog.destroy()
                if error is None:
                    self.fail('Valid editor inputs were rejected: ' + str(message.call_args))
        with patch.object(table, '_edit_fields', side_effect=fields), patch('ui.table.show_modal', side_effect=interact), \
                patch('ui.table.messagebox.showerror') as message:
            table._edit(item)
            if error is None:
                message.assert_not_called()
            else:
                self.assertIn(error.lower(), message.call_args.args[1].lower())
