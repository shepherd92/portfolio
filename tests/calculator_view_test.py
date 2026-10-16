"""Calculator edits, live rate references and saved drafts remain independent."""

from dataclasses import replace
from datetime import date, timedelta
import os
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from portfolio.storage import load_settings
from portfolio.models import AllocationTargets, CASH, STOCK_MARKET, TREASURY
from ui.calculator import CalculatorView
from ui.reporting import format_amount
from ui.theme import configure_theme


@unittest.skipUnless(os.environ.get('REBALANCE_GUI_TESTS') == '1',
                     'GUI tests require REBALANCE_GUI_TESTS=1 and a display')
class CalculatorViewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        configure_theme(self.root)
        self.current_total = Mock(return_value=0.)
        self.currency = tk.StringVar(self.root, value='HUF')
        self.show_projection = Mock()
        self.allocation = Mock(return_value=AllocationTargets({STOCK_MARKET: {'World': 60},
                                                              TREASURY: {'HUF': 40}, CASH: {}}, {'World': 'Equities'}))
        self.changed = Mock()
        self.rates = Mock(return_value={'HUF': 1, 'EUR': 400, 'USD': 360, 'GBP': 480})
        self.view = CalculatorView(self.root, get_total=self.current_total,
                                   show_projection=self.show_projection, currency=self.currency,
                                   get_allocation=self.allocation, get_rates=self.rates,
                                   currency_choices=lambda: ('HUF', 'EUR', 'USD', 'GBP'), on_change=self.changed)
        self.view.load(self.directory)

    def set_monthly_cost(self, amount, currency='HUF'):
        rows = [{'name': 'Living costs', 'amount': str(amount), 'currency': currency}] if float(amount) else []
        self.view.monthly_costs.set_rows(rows)
        self.view._changed()

    def wait_for_autosave(self):
        self.root.after(550, self.root.quit)
        self.root.mainloop()

    def test_portfolio_allocation_is_read_only_and_percentages_round_without_losing_precision(self):
        self.view.set_base_rate(5.5, 'Effective today')
        self.current_total.return_value = 1234567.89123
        self.view.refresh()
        self.assertEqual(self.view.portfolio_value.get(), '1\u202f234\u202f568')
        self.assertAlmostEqual(sum(row['amount'] for row in self.view._values.values()), 1234567.89123)
        self.assertFalse(self.view.dirty)
        self.changed.assert_not_called()
        self.allocation.return_value = AllocationTargets({STOCK_MARKET: {'World': 59.875},
                                                         TREASURY: {'HUF': 30.125}, CASH: {'EUR': 10}}, {'World': 'Equities'})
        self.view.refresh()
        self.assertEqual(self.view.return_weights['Equities'].get(), '59.88%')
        self.assertEqual(self.view.return_weights[TREASURY].get(), '30.12%')
        self.assertNotIn(CASH, self.view.return_weights)
        self.assertIn('59.88%', self.view.allocation_summary.set(STOCK_MARKET, 'share'))
        self.assertIn('10%', self.view.allocation_summary.set(CASH, 'share'))
        self.assertNotIn('stock_percent', self.view._inputs)
        self.assertNotIn('mak_percent', self.view._inputs)
        self.allocation.side_effect = ValueError('Targets must total 100%.')
        self.view.refresh()
        self.assertFalse(self.view.breakdown.get_children())
        self.assertIn('Targets must total', self.view.message.get())
        self.assertFalse(self.view.allocation_summary.get_children())

    def test_market_value_refresh_preserves_exact_calculations_and_saved_inputs(self):
        self.view.set_base_rate(5.5, 'Effective today')
        self.view._inputs['monthly_income'].set('1')
        self.current_total.return_value = 1000.125
        self.view.refresh()
        self.assertTrue(self.view.save())
        settings = self.view.settings()
        original = (self.directory / 'calculator.json').read_bytes()
        self.changed.reset_mock()
        self.current_total.return_value = 1250.875
        self.view.refresh()
        self.assertEqual(self.view.portfolio_value.get(), '1\u202f251')
        self.assertAlmostEqual(sum(row['amount'] for row in self.view._values.values()), 1250.875)
        self.currency.set('EUR')
        self.view.refresh()
        self.assertEqual(self.view.portfolio_value.get(), '3')
        self.assertAlmostEqual(sum(row['amount'] for row in self.view._values.values()), 1250.875)
        self.assertTrue(self.view._currency_box.instate(['readonly']))
        self.assertEqual(self.view.settings(), settings)
        self.assertEqual((self.directory / 'calculator.json').read_bytes(), original)
        self.assertFalse(self.view.dirty)
        self.changed.assert_not_called()
        self.current_total.side_effect = ValueError('Missing portfolio prices.')
        self.view.refresh()
        self.assertEqual(self.view.portfolio_value.get(), '—')
        self.assertFalse(self.view.allocation_summary.get_children())
        self.assertFalse(self.view.breakdown.get_children())
        self.assertEqual(self.view.message.get(), 'Missing portfolio prices.')
        self.assertTrue(self.view.save())
        self.assertEqual(self.view.settings(), settings)

    def test_monthly_amounts_and_currency_suffixes_share_columns(self):
        self.root.deiconify()
        self.view.pack(fill='both', expand=True)
        self.root.update_idletasks()
        income = self.view._income_entry
        budget = income.master
        amounts = [budget.grid_slaves(row=row, column=1)[0] for row in range(3)]
        suffixes = [budget.grid_slaves(row=row, column=2)[0] for row in range(3)]
        self.assertEqual(len({widget.winfo_x() for widget in amounts}), 1)
        self.assertEqual(len({widget.winfo_width() for widget in amounts}), 1)
        self.assertEqual(len({widget.winfo_x() for widget in suffixes}), 1)
        self.assertTrue(all(str(widget.cget('textvariable')) == str(self.currency) for widget in suffixes))
        self.assertEqual(str(income.cget('justify')), 'right')
        self.assertTrue(all(str(widget.cget('anchor')) == 'e' for widget in amounts[1:]))

    def test_converted_income_preserves_native_precision_through_refresh_save_and_reload(self):
        native = replace(self.view.settings(), monthly_income=300.25123456789, income_currency='USD')
        self.view._set_inputs(native)
        self.view._changed()
        self.assertTrue(self.view.save())
        original = (self.directory / 'calculator.json').read_bytes()
        self.changed.reset_mock()
        for currency in ('EUR', 'HUF', 'GBP', 'USD', 'HUF') * 3:
            self.currency.set(currency)
            self.view.refresh()
            self.assertEqual(self.view.settings(), native)
        self.rates.return_value = {**self.rates(), 'USD': 365.125}
        self.view.refresh()
        self.assertAlmostEqual(float(self.view._inputs['monthly_income'].get()), native.monthly_income * 365.125)
        self.view._income_entry.event_generate('<FocusOut>')
        self.assertEqual((self.directory / 'calculator.json').read_bytes(), original)
        self.assertFalse(self.view.dirty)
        self.changed.assert_not_called()
        self.view._inputs['cash_months'].set('3')
        self.assertTrue(self.view.save())
        self.view.load(self.directory)
        self.assertEqual(self.view.settings(), replace(native, cash_months=3))

    def test_editing_converted_income_uses_shown_currency_and_rejects_typed_huf_fractions(self):
        self.view._set_inputs(replace(self.view.settings(), monthly_income=1.01, income_currency='USD'))
        self.view.refresh()
        income = self.view._income_entry
        self.assertEqual(income.get(), '364')
        self.assertAlmostEqual(float(self.view._inputs['monthly_income'].get()), 363.6)
        self.assertEqual(self.view.settings().monthly_income, 1.01)
        income._display.set('364.5')
        with self.assertRaisesRegex(ValueError, 'whole'):
            self.view.settings()
        income._display.set('364')
        self.assertEqual((self.view.settings().monthly_income, self.view.settings().income_currency), (364, 'HUF'))
        self.currency.set('EUR')
        self.view.refresh()
        income._display.set('12.125')
        self.view.refresh()
        self.assertEqual(self.view._inputs['monthly_income'].get(), '12.125')
        self.assertEqual((self.view.settings().monthly_income, self.view.settings().income_currency), (12.125, 'EUR'))
        self.currency.set('HUF')
        self.view.refresh()
        self.assertEqual(income.get(), '4\u202f850')
        self.assertTrue(self.view.save())
        self.assertEqual(load_settings(self.directory)[0].income_currency, 'EUR')

    def test_missing_fx_disables_only_unavailable_income_and_recovers_without_editing_settings(self):
        self.rates.return_value = {}
        native = replace(self.view.settings(), monthly_income=100.25, income_currency='USD')
        self.view._set_inputs(native)
        self.view.refresh()
        income = self.view._income_entry
        self.assertEqual(income.get(), '—')
        self.assertTrue(income.instate(['disabled']))
        self.view.set_enabled(False)
        self.view.set_enabled(True)
        self.assertTrue(income.instate(['disabled']))
        self.assertEqual(self.view.settings(), native)
        self.assertEqual(self.view.required_currencies, {'HUF', 'USD'})
        self.rates.return_value = {'HUF': 1, 'USD': 360}
        self.view.refresh()
        self.assertEqual(income.get(), '36\u202f090')
        self.assertTrue(income.instate(['!disabled']))
        self.assertFalse(self.view.dirty)

    def test_invalid_income_draft_keeps_its_currency_when_switch_is_attempted(self):
        self.view._inputs['monthly_income'].set('1200')
        self.assertTrue(self.view.save())
        for draft in ('', 'unfinished', '-1', '1.5'):
            self.view._income_entry._display.set(draft)
            self.currency.set('EUR')
            self.assertFalse(self.view.change_currency())
            self.assertEqual(self.currency.get(), 'HUF')
            self.assertEqual(self.view._inputs['monthly_income'].get(), draft)
            self.assertIn('Complete the income edit', self.view.message.get())
            self.assertFalse(self.view.save())
        self.assertEqual(load_settings(self.directory)[0].monthly_income, 1200)

    def test_overall_table_weights_returns_by_recommended_stock_mak_and_cash(self):
        self.view.set_base_rate(5.5, 'Effective today')
        self.current_total.return_value = 1000
        self.view.refresh()
        self.view._inputs['equities'].set('5.33')
        self.allocation.return_value = AllocationTargets({STOCK_MARKET: {'World': 30},
                                                         TREASURY: {'HUF': 70}, CASH: {}}, {'World': 'Equities'})
        self.view.refresh()
        self.assertEqual(self.view.overall_return.get(), 'Overall annual return: 5.45%')
        self.assertEqual(self.view.allocation_summary.item(TREASURY, 'values'), ('MÁK', '70%', '700'))
        self.allocation.return_value = AllocationTargets({STOCK_MARKET: {'World': 20},
                                                         TREASURY: {'HUF': 70}, CASH: {'HUF': 10}}, {'World': 'Equities'})
        self.view.refresh()
        self.assertEqual(self.view.overall_return.get(), 'Overall annual return: 4.92%')
        self.view._summary_sorting.sort('share')
        self.assertEqual(self.view.allocation_summary.get_children(), (CASH, STOCK_MARKET, TREASURY))
        self.view.refresh()
        self.assertEqual(self.view.allocation_summary.get_children(), (CASH, STOCK_MARKET, TREASURY))
        self.current_total.side_effect = ValueError('Missing portfolio prices.')
        self.view.refresh()
        self.assertFalse(self.view.allocation_summary.get_children())
        self.assertEqual(self.view.overall_return.get(), 'Overall annual return: —')

    def test_three_rows_keep_cost_inputs_and_results_visible_at_minimum_window_size(self):
        self.root.geometry('1050x760')
        self.view.pack(fill='both', expand=True, padx=16, pady=(52, 54))
        self.root.deiconify()
        self.view.set_base_rate(5.5, 'Effective today')
        self.current_total.return_value = 1_000_000
        self.set_monthly_cost('1000')
        self.view.expenses.set_rows([{'name': 'Trip', 'amount': '100', 'currency': 'HUF', 'due': '2030-01-01'}])
        self.view.refresh()
        self.view.message.set('Calculator settings saved.')
        self.root.update_idletasks()
        returns = self.view._mak_entry.master
        costs, expenses = self.view.monthly_costs.master, self.view.expenses.master
        overall, funding = self.view.allocation_summary.master, self.view.breakdown.master
        self.assertEqual(returns.cget('text'), 'Expected annual returns')
        self.assertEqual(overall.cget('text'), 'Recommended overall allocations')
        self.assertLess(returns.winfo_rooty(), costs.winfo_rooty())
        self.assertEqual(costs.winfo_rooty(), expenses.winfo_rooty())
        self.assertLess(costs.winfo_rootx(), expenses.winfo_rootx())
        self.assertLess(costs.winfo_rooty(), overall.winfo_rooty())
        self.assertLess(overall.winfo_rootx(), funding.winfo_rootx())
        right = self.root.winfo_rootx() + self.root.winfo_width()
        for panel in (returns, costs, expenses, overall, funding):
            self.assertLessEqual(panel.winfo_rootx() + panel.winfo_width(), right)
        for table in (self.view.monthly_costs, self.view.expenses):
            row = table.tree.bbox(table.tree.get_children()[0])
            self.assertTrue(row, 'Each input table must show a complete row.')
            self.assertLessEqual(row[1] + row[3], table.tree.winfo_height())
        self.assertEqual(self.view.allocation_summary.xview(), (0.0, 1.0))
        last = self.view.allocation_summary.bbox(self.view.allocation_summary.get_children()[-1])
        self.assertTrue(last)
        self.assertLessEqual(last[1] + last[3], self.view.allocation_summary.winfo_height())

    def test_planning_uses_unsaved_inputs_independently_of_market_valuation(self):
        self.current_total.side_effect = ValueError('Missing portfolio prices.')
        self.set_monthly_cost('200')
        self.assertTrue(self.view.needs_base_rate)
        with self.assertRaisesRegex(ValueError, 'MNB base rate'):
            self.view.planning_settings()
        self.changed.reset_mock()
        self.view.set_base_rate(5.5, 'Effective today')
        self.assertFalse(self.view.needs_base_rate)
        settings = self.view.planning_settings()
        self.assertEqual(settings.monthly_spending(), 200)
        self.assertEqual(settings.returns.mak, 5.5)
        self.changed.assert_not_called()
        self.set_monthly_cost('300')
        self.changed.assert_called_once_with()
        self.assertEqual(self.view.planning_settings().monthly_spending(), 300)
        self.view._load_error = 'Cannot load calculator settings: invalid file'
        with self.assertRaisesRegex(ValueError, 'Cannot load calculator settings'):
            self.view.planning_settings()

    def test_missing_automatic_rate_has_no_zero_fallback_and_manual_override_survives_refresh(self):
        self.current_total.return_value = 1000
        self.view.refresh()
        self.assertFalse(self.view.breakdown.get_children())
        self.view.set_rate_error('Rate unavailable; retry.')
        self.assertIn('Rate unavailable', self.view.message.get())
        self.view.set_base_rate(5.5, 'Effective today')
        self.assertEqual(self.view._auto_rate_box.cget('text'), 'Use MNB base rate')
        self.assertEqual(self.view.rate_description, 'MNB base rate: 5.5% · Effective today')
        self.assertTrue(self.view.breakdown.get_children())
        self.assertTrue(self.view._mak_entry.instate(['disabled']))
        self.assertIsNone(self.view.settings().returns.mak)
        self.assertTrue(self.view.save())
        self.view.set_base_rate(6.5, 'Effective tomorrow')
        self.assertFalse(self.view.dirty)
        self.assertEqual(float(self.view._inputs['mak'].get()), 6.5)
        self.view._auto_rate.set(False)
        self.view._inputs['mak'].set('5.125')
        self.assertFalse(self.view._mak_entry.instate(['disabled']))
        self.assertTrue(self.view.save())
        self.view.set_base_rate(7, 'New reference')
        self.view.refresh()
        self.assertEqual(self.view.settings().returns.mak, 5.125)
        self.assertFalse(self.view.dirty)
        self.view.load(self.directory)
        self.assertEqual(self.view.settings().returns.mak, 5.125)
        self.assertFalse(self.view._auto_rate.get())

    def test_category_returns_and_portfolio_weights_update_recommendations_automatically(self):
        self.view.set_base_rate(5.5, 'Effective today')
        self.current_total.return_value = 1000
        self.view.refresh()
        weights = {STOCK_MARKET: {'World': 40, 'Bond fund': 10, 'Gold fund': 10},
                   TREASURY: {'HUF': 40}, CASH: {}}
        categories = {'World': 'Equities', 'Bond fund': 'Bonds', 'Gold fund': 'Gold'}
        self.allocation.return_value = AllocationTargets(weights, categories)
        self.view.refresh()
        self.assertEqual({name: variable.get() for name, variable in self.view.return_weights.items()},
                         {'Equities': '40%', 'Bonds': '10%', 'Gold': '10%', TREASURY: '40%'})
        self.assertEqual(self.view.overall_return.get(), 'Overall annual return: 5.1%')
        self.view._inputs['gold'].set('4')
        self.assertEqual(self.view.overall_return.get(), 'Overall annual return: 5.3%')
        self.allocation.return_value = AllocationTargets(weights, {**categories, 'Gold fund': 'Bonds'})
        self.view.refresh()
        self.assertEqual(self.view.return_weights['Bonds'].get(), '20%')
        self.assertEqual(self.view.return_weights['Gold'].get(), '0%')
        self.assertEqual(self.view.overall_return.get(), 'Overall annual return: 5.2%')
        self.allocation.return_value = AllocationTargets(weights, {**categories, 'World': ''})
        self.view.refresh()
        self.assertIn('World', self.view.message.get())
        self.assertTrue(all(variable.get() == '—' for variable in self.view.return_weights.values()))

    def test_return_columns_sort_without_recreating_the_editors(self):
        self.view.set_base_rate(5.5, 'Effective today')
        self.view._sort_return_rows(1)
        def order():
            return sorted(self.view._return_rows,
                          key=lambda name: self.view._return_rows[name][0].grid_info()['row'])
        self.assertEqual(order(), ['Gold', 'Bonds', TREASURY, 'Equities'])
        editor = self.view._return_rows['Gold'][1]
        self.view._inputs['gold'].set('7')
        self.assertEqual(order(), ['Bonds', TREASURY, 'Equities', 'Gold'])
        self.assertIs(self.view._return_rows['Gold'][1], editor)
        self.view._sort_return_rows(2)
        self.assertEqual(order(), ['Bonds', 'Gold', TREASURY, 'Equities'])
        self.assertEqual(self.view._return_rows['Equities'][2].winfo_class(), 'TLabel')

    def test_all_percentage_entries_limit_display_precision_without_rounding_saved_values(self):
        from ui.widgets import PercentEntry

        names = ('equities', 'gold', 'bonds', 'mak')
        for name in names:
            variable = self.view._inputs[name]
            entry = next(widget for widget in self.view._entries if getattr(widget, '_value', None) is variable)
            self.assertIsInstance(entry, PercentEntry)
            variable.set('7.1234567')
            self.assertRegex(entry.get(), r'^7\.12%?$')
            self.assertEqual(float(variable.get()), 7.1234567)

    def test_expense_editor_uses_calendar_date_field_and_cancel_keeps_original(self):
        from ui.dates import DateEntry

        row = {'name': 'Car', 'amount': '500', 'currency': 'HUF', 'due': '2030-01-01'}
        self.view.expenses.set_rows([row])
        failures = []

        def inspect():
            dialog = next(child for child in self.view.expenses.winfo_children() if isinstance(child, tk.Toplevel))

            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)

            try:
                dates = [widget for widget in descendants(dialog) if isinstance(widget, DateEntry)]
                self.assertEqual(len(dates), 1)
                self.assertEqual(dates[0].entry.get(), '2030-01-01')
            except Exception as error:
                failures.append(error)
            finally:
                dialog.destroy()

        self.root.after(30, inspect)
        item = self.view.expenses.tree.get_children()[0]
        self.view.expenses._edit(item)
        if failures:
            raise failures[0]
        self.assertEqual(self.view.expenses.get_rows(), [row])

    def test_discounted_expenses_show_due_amounts_and_overcommitment_keeps_funded_rows(self):
        self.view.set_base_rate(5, 'Effective today')
        self.current_total.return_value = 1000
        self.view.refresh()
        due = date.today().replace(year=date.today().year + 3).isoformat()
        self.view.expenses.set_rows([{'name': 'Car', 'amount': '500', 'currency': 'HUF', 'due': due}])
        self.view._changed()
        rows = [dict(zip(self.view.breakdown['columns'], self.view.breakdown.item(item, 'values')))
                for item in self.view.breakdown.get_children()]
        car = next(row for row in rows if row['purpose'] == 'Car')
        self.assertEqual(car['future'], '500')
        self.assertEqual(car['income'], '0')
        self.assertLess(int(car['stock']) + int(car['mak']), 500)
        self.assertEqual(self.view.settings().expenses[0].amount, 500)
        self.assertTrue(self.view.save())
        self.view.refresh()
        self.assertFalse(self.view.dirty)
        self.set_monthly_cost('200')
        self.assertTrue(self.view.breakdown.get_children())
        self.assertIn('60%', self.view.allocation_summary.set(TREASURY, 'share'))
        self.assertIn('40%', self.view.allocation_summary.set(CASH, 'share'))
        self.assertIn('0%', self.view.allocation_summary.set(STOCK_MARKET, 'share'))
        self.assertIn('HUF', self.view.message.get())
        self.assertAlmostEqual(sum(value['amount'] for value in self.view._values.values()), 1000)
        self.assertTrue(all(value['stock'] >= 0 and value['mak'] >= 0 and value['cash'] >= 0
                            for value in self.view._values.values()))

    def test_income_funding_uses_savings_and_keeps_current_portfolio_separate(self):
        self.view.set_base_rate(0, 'Effective today')
        self.current_total.return_value = 1000
        self.view.refresh()
        self.view._inputs['equities'].set('0')
        self.set_monthly_cost('200')
        self.view._inputs['emergency_months'].set('0')
        self.view._inputs['cash_months'].set('0')
        due = (date.today() + timedelta(days=366)).isoformat()
        self.view.expenses.set_rows([{'name': 'Car', 'amount': '500', 'currency': 'HUF', 'due': due}])
        self.view.refresh()
        baseline = list(self.view._values.values())

        self.view._inputs['monthly_income'].set('150')
        self.assertEqual(self.view.settings().monthly_savings(), 0)
        self.assertIn('0', self.view.monthly_savings.get())
        self.assertEqual(list(self.view._values.values()), baseline)

        self.view._inputs['monthly_income'].set('300')
        self.assertEqual(self.view.settings().monthly_savings(), 100)
        self.assertIn('100', self.view.monthly_savings.get())
        rows = {value['purpose']: value for value in self.view._values.values()}
        self.assertEqual(rows['Car']['income'], 500)
        self.assertEqual(rows['Car']['amount'], 0)
        self.assertIsNone(rows['Emergency reserve']['income'])
        self.assertEqual(rows['Long-term allocation']['amount'], 1000)
        self.assertEqual(sum(value['amount'] for value in rows.values()), 1000)
        self.assertIn('60%', self.view.allocation_summary.set(STOCK_MARKET, 'share'))
        self.assertIn('40%', self.view.allocation_summary.set(TREASURY, 'share'))
        displayed = {self.view.breakdown.set(item, 'purpose'): self.view.breakdown.set(item, 'income')
                     for item in self.view.breakdown.get_children()}
        self.assertEqual(displayed, {'Emergency reserve': '—', 'Bank cash buffer': '—', 'Car': '500', 'Long-term allocation': '—'})
        self.assertTrue(self.view.save())
        self.view.load(self.directory)
        self.assertEqual(self.view.settings().monthly_income, 300)
        self.assertEqual(self.view.settings().monthly_savings(), 100)

    def test_overcommitment_preserves_required_mak_and_allocates_remaining_money_to_stock(self):
        self.view.set_base_rate(0, 'Effective today')
        self.current_total.return_value = 1000
        self.view.refresh()
        self.view._inputs['equities'].set('0')
        self.set_monthly_cost('100')
        self.view._inputs['emergency_months'].set('2')
        due = date.today().replace(year=date.today().year + 10).isoformat()
        self.view.expenses.set_rows([{'name': 'Future expense', 'amount': '2000', 'currency': 'HUF', 'due': due}])
        self.view._changed()
        self.assertIn('60%', self.view.allocation_summary.set(STOCK_MARKET, 'share'))
        self.assertIn('20%', self.view.allocation_summary.set(CASH, 'share'))
        self.assertIn('20%', self.view.allocation_summary.set(TREASURY, 'share'))
        self.assertAlmostEqual(sum(value['stock'] for value in self.view._values.values()), 600)
        self.assertAlmostEqual(sum(value['cash'] for value in self.view._values.values()), 200)
        self.assertIn('1\u202f400', self.view.message.get())

    def test_monthly_cost_total_drives_bank_cash_and_full_emergency_reserve(self):
        self.view.set_base_rate(0, 'Effective today')
        self.current_total.return_value = 1000
        self.set_monthly_cost('100')
        self.view._inputs['emergency_months'].set('1')
        rows = {value['purpose']: value for value in self.view._values.values()}
        self.assertEqual(rows['Emergency reserve']['cash'], 0)
        self.assertEqual(rows['Emergency reserve']['mak'], 100)
        self.assertEqual(rows['Bank cash buffer']['cash'], 200)
        self.assertEqual(rows['Long-term allocation']['amount'], 700)
        self.assertEqual(self.view.monthly_spending.get(), '100')
        self.view._inputs['cash_months'].set('4')
        rows = {value['purpose']: value for value in self.view._values.values()}
        self.assertEqual(rows['Bank cash buffer']['cash'], 400)
        self.assertEqual(rows['Emergency reserve']['mak'], 100)
        self.assertTrue(self.view.save())
        self.view.load(self.directory)
        self.assertEqual(self.view.settings().cash_months, 4)
        self.assertEqual(self.view.settings().monthly_costs[0].amount, 100)
        self.view._inputs['cash_months'].set('0')
        rows = {value['purpose']: value for value in self.view._values.values()}
        self.assertEqual(rows['Bank cash buffer']['cash'], 0)
        for value in ('1.5', '-1', '121'):
            self.view._inputs['cash_months'].set(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.view.settings()

    def test_fractional_huf_income_and_costs_are_rejected_without_writing_settings(self):
        self.view.set_base_rate(5, 'Effective today')
        self.current_total.return_value = 1000
        self.view._inputs['monthly_income'].set('100.5')
        with self.assertRaisesRegex(ValueError, 'whole|integer'):
            self.view.settings()
        self.assertFalse(self.view.save())
        self.view._inputs['monthly_income'].set('0')
        for table in (self.view.monthly_costs, self.view.expenses):
            table.set_rows([{'name': 'Cost', 'amount': '100.5', 'currency': 'HUF', 'due': '2030-01-01'}])
            with self.subTest(table=table), self.assertRaisesRegex(ValueError, 'whole|integer'):
                self.view.settings()
            self.assertFalse(self.view.save())
            table.set_rows([])
        self.assertFalse((self.directory / 'calculator.json').exists())

    def test_native_cost_amounts_round_trip_and_all_results_follow_display_currency(self):
        self.view.set_base_rate(0, 'Effective today')
        self.current_total.return_value = 1_000_000
        costs = [{'name': 'Rent', 'amount': '100.5', 'currency': 'EUR'},
                 {'name': 'Food', 'amount': '5000', 'currency': 'HUF'}]
        self.view.monthly_costs.set_rows(costs)
        self.currency.set('USD')
        self.view.refresh()
        self.view._inputs['monthly_income'].set('300.25')
        self.currency.set('HUF')
        self.view.refresh()
        expense = {'name': 'Car', 'amount': '500.25', 'currency': 'USD', 'due': date.today().isoformat()}
        self.view.expenses.set_rows([expense])
        self.view._changed()
        settings = self.view.settings()
        self.assertEqual(settings.monthly_spending(self.rates()), 45_200)
        self.assertEqual(settings.monthly_savings(self.rates()), 62_890)
        self.assertEqual(self.view.monthly_spending.get(), '45\u202f200')
        self.assertEqual(self.view._income_entry.get(), '108\u202f090')
        self.assertEqual(self.view.monthly_costs.display_value(costs[0], 'amount'), '100')
        self.assertEqual(self.view.required_currencies, {'HUF', 'EUR', 'USD'})
        self.assertTrue(self.view.save())
        original = (self.directory / 'calculator.json').read_bytes()
        self.currency.set('EUR')
        self.view.refresh()
        self.assertEqual(self.view.monthly_spending.get(), '113')
        self.assertEqual(self.view.monthly_savings.get(), '157')
        self.assertEqual(self.view.allocation_summary.heading('amount', 'text'), 'Amount (EUR)')
        item = next(item for item, row in self.view._values.items() if row['purpose'] == 'Car')
        self.assertEqual(self.view.breakdown.set(item, 'future'), '450')
        self.assertEqual(self.view.breakdown.set(item, 'cash'), '450')
        self.assertEqual(self.view._values[item]['future'], 180_090)
        for item, values in self.view._summary_values.items():
            self.assertEqual(self.view.allocation_summary.set(item, 'amount'), format_amount(values['amount'] / 400))
        self.assertEqual((self.directory / 'calculator.json').read_bytes(), original)
        self.assertFalse(self.view.dirty)
        self.view.load(self.directory)
        self.assertEqual(self.view.settings(), settings)
        self.assertEqual([(row['name'], float(row['amount']), row['currency']) for row in self.view.monthly_costs.get_rows()],
                         [('Rent', 100.5, 'EUR'), ('Food', 5000, 'HUF')])
        self.assertEqual(self.view.expenses.get_rows()[0]['currency'], 'USD')
        self.assertTrue(self.view._currency_box.instate(['readonly']))
        self.rates.return_value = {'HUF': 1, 'USD': 360}
        self.view.refresh()
        self.assertFalse(self.view.allocation_summary.get_children())
        self.assertIn('EUR/HUF', self.view.message.get())
        self.assertTrue(self.view.save())

    def test_missing_currency_rate_hides_stale_result_but_does_not_block_saving_inputs(self):
        self.view.set_base_rate(0, 'Effective today')
        self.current_total.return_value = 1_000_000
        self.view.refresh()
        self.currency.set('USD')
        self.view.refresh()
        self.view._inputs['monthly_income'].set('100.5')
        self.currency.set('HUF')
        self.view.refresh()
        self.assertTrue(self.view.breakdown.get_children())
        self.rates.return_value = {}
        self.view.refresh()
        self.assertFalse(self.view.breakdown.get_children())
        self.assertIn('USD/HUF', self.view.message.get())
        self.assertTrue(self.view.save())
        self.assertEqual(load_settings(self.directory)[0].monthly_income, 100.5)
        self.view._inputs['monthly_income'].set('unfinished')
        self.assertEqual(self.view.required_currencies, {'HUF'})

    def test_invalid_file_is_not_rewritten_until_inputs_change_and_external_edits_are_protected(self):
        path = self.directory / 'calculator.json'
        original = b'{invalid settings'
        path.write_bytes(original)
        self.view.load(self.directory)
        self.assertIn('Cannot load', self.view.message.get())
        self.assertFalse(self.view.dirty)
        self.view.refresh()
        self.assertEqual(path.read_bytes(), original)
        self.view.set_base_rate(5.5, 'Effective today')
        self.current_total.return_value = 1000
        self.view.refresh()
        self.assertEqual(path.read_bytes(), original)
        self.assertTrue(self.view.save())
        self.assertEqual(path.read_bytes(), original)
        self.view._inputs['emergency_months'].set('8')
        self.wait_for_autosave()
        self.assertEqual(load_settings(self.directory)[0].monthly_spending(), 0)
        self.set_monthly_cost('2000')
        external = path.read_bytes() + b'\n'
        path.write_bytes(external)
        self.assertFalse(self.view.save())
        self.assertEqual(path.read_bytes(), external)
        self.assertTrue(self.view.dirty)
        self.assertIn('outside', self.view.message.get())
        self.view.set_base_rate(6, 'Tomorrow')
        self.view.refresh()
        self.assertIn('outside', self.view.message.get())

    def test_edits_autosave_after_typing_and_invalid_drafts_leave_saved_values_intact(self):
        path = self.directory / 'calculator.json'
        self.assertFalse(path.exists())
        self.view._inputs['monthly_income'].set('12')
        first_timer = self.view._autosave_id
        self.view._inputs['monthly_income'].set('1200')
        self.assertNotEqual(self.view._autosave_id, first_timer)
        self.assertFalse(path.exists())
        self.wait_for_autosave()
        self.assertEqual(load_settings(self.directory)[0].monthly_income, 1200)
        self.assertFalse(self.view.dirty)
        self.assertIsNone(self.view._autosave_id)
        self.view._inputs['monthly_income'].set('')
        self.wait_for_autosave()
        self.assertEqual(load_settings(self.directory)[0].monthly_income, 1200)
        self.assertTrue(self.view.dirty)
        self.assertIn('Cannot save calculator settings', self.view.message.get())
        self.view._inputs['monthly_income'].set('2500')
        self.view._income_entry.event_generate('<FocusOut>')
        self.assertEqual(load_settings(self.directory)[0].monthly_income, 2500)
        self.assertFalse(self.view.dirty)
        self.assertNotIn('Cannot save', self.view.message.get())

    def test_load_and_refresh_do_not_save_and_loading_cancels_pending_edits(self):
        self.view._inputs['monthly_income'].set('500')
        self.assertIsNotNone(self.view._autosave_id)
        self.view.load(self.directory)
        self.assertIsNone(self.view._autosave_id)
        self.assertEqual(self.view.settings().monthly_income, 0)
        with patch('ui.calculator.save_settings') as save:
            self.view.set_base_rate(6, 'Today')
            self.current_total.return_value = 2000
            self.currency.set('EUR')
            self.view.refresh()
            self.wait_for_autosave()
        save.assert_not_called()
        self.assertFalse((self.directory / 'calculator.json').exists())

    def test_failed_autosave_is_visible_until_a_successful_retry(self):
        self.view._inputs['monthly_income'].set('500')
        with patch('ui.calculator.save_settings', side_effect=OSError('Disk is full')):
            self.wait_for_autosave()
        self.assertTrue(self.view.dirty)
        self.assertIn('Disk is full', self.view.message.get())
        self.view.set_base_rate(5.5, 'Today')
        self.assertIn('Disk is full', self.view.message.get())
        self.assertTrue(self.view.save())
        self.assertFalse(self.view.dirty)
        self.assertNotIn('Disk is full', self.view.message.get())

    def test_destroy_cancels_pending_autosave(self):
        self.view._inputs['monthly_income'].set('500')
        timer = self.view._autosave_id
        self.view.destroy()
        self.assertNotIn(timer, self.root.tk.call('after', 'info'))

    def test_disabled_controls_cannot_start_callbacks_and_expense_validation_is_recoverable(self):
        self.view.set_enabled(False)
        self.view.projection_button.invoke()
        self.show_projection.assert_not_called()
        self.assertTrue(all(entry.instate(['disabled']) for entry in self.view._entries))
        self.assertFalse(self.view.expenses._enabled)
        self.assertFalse(self.view.monthly_costs._enabled)
        self.assertTrue(self.view._currency_box.instate(['disabled']))
        self.view.set_enabled(True)
        self.assertTrue(self.view._currency_box.instate(['readonly']))
        self.view.projection_button.invoke()
        self.show_projection.assert_called_once_with()
        self.assertTrue(self.view._mak_entry.instate(['disabled']))
        for row in ({'name': '', 'amount': '5', 'currency': 'HUF', 'due': '2030-01-01'},
                    {'name': 'Car', 'amount': '-1', 'currency': 'HUF', 'due': '2030-01-01'},
                    {'name': 'Car', 'amount': '5.125', 'currency': 'HUF', 'due': '2030-01-01'},
                    {'name': 'Car', 'amount': '5', 'currency': 'HUF', 'due': 'bad-date'}):
            with self.subTest(row=row), self.assertRaises(ValueError):
                self.view._validate_expense(row, None)
        validated = self.view._validate_expense({'name': ' Car ', 'amount': '5', 'currency': 'HUF', 'due': '2030-01-01'}, None)
        self.assertEqual(validated['name'], 'Car')
        self.assertEqual(float(validated['amount']), 5)
        self.assertEqual(validated['due'], '2030-01-01')
