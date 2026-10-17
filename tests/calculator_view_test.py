"""Calculator edits, live rate references and saved drafts remain independent."""

from datetime import date, timedelta
import os
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import font, ttk
import unittest
from unittest.mock import Mock, patch

from portfolio.calculator import CashFlow
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
        self.return_parents = {branch: ttk.Frame(self.root) for branch in (STOCK_MARKET, TREASURY)}
        self.allocation = Mock(return_value=AllocationTargets({STOCK_MARKET: {'World': 60},
                                                              TREASURY: {'HUF': 40}, CASH: {}}, {'World': 'Equities'}))
        self.changed = Mock()
        self.rates = Mock(return_value={'HUF': 1, 'EUR': 400, 'USD': 360, 'GBP': 480})
        self.view = CalculatorView(self.root, get_total=self.current_total,
                                   return_parents=self.return_parents, currency=self.currency,
                                   get_allocation=self.allocation, get_rates=self.rates,
                                   currency_choices=lambda: ('HUF', 'EUR', 'USD', 'GBP'), on_change=self.changed)
        self.view.load(self.directory)

    def flow_rows(self, kind):
        return [row for row in self.view.cashflows.get_rows() if row['kind'] == kind]

    def set_flow_rows(self, kind, rows):
        other = [row for row in self.view.cashflows.get_rows() if row['kind'] != kind]
        self.view.cashflows.set_rows(other + [dict(row, kind=kind) for row in rows])

    def set_flows(self, kind, flows):
        self.set_flow_rows(kind, [self.view.cashflows._row(flow, kind) for flow in flows])

    def set_income(self, amount, currency=None):
        rows = [] if str(amount) == '0' else [dict(name='Salary', amount=str(amount),
                                                  currency=currency or self.view._currency.get(),
                                                  frequency='monthly', start=date.today().isoformat()[:7])]
        self.set_flow_rows('Income', rows)
        self.view._changed()

    def set_cost_rows(self, rows):
        existing = [row for row in self.flow_rows('Outgoing') if row['frequency'] == 'once']
        costs = [dict(row, frequency='monthly', start=date.today().isoformat()[:7], count='', end='') for row in rows]
        self.set_flow_rows('Outgoing', costs + existing)

    def set_single_rows(self, rows):
        existing = [row for row in self.flow_rows('Outgoing') if row['frequency'] != 'once']
        payments = [dict(name=row['name'], amount=row['amount'], currency=row['currency'],
                         frequency='once', start=row['due'][:7], count='', end='') for row in rows]
        self.set_flow_rows('Outgoing', existing + payments)

    def set_monthly_cost(self, amount, currency='HUF'):
        rows = [{'name': 'Living costs', 'amount': str(amount), 'currency': currency}] if float(amount) else []
        self.set_cost_rows(rows)
        self.view._changed()

    def wait_for_autosave(self):
        self.root.after(550, self.root.quit)
        self.root.mainloop()

    def test_portfolio_allocation_is_read_only_and_percentages_round_without_losing_precision(self):
        self.view.set_base_rate(5.5, 'Effective today')
        self.current_total.return_value = 1234567.89123
        self.view.refresh()
        self.assertEqual(self.view.portfolio_value.get(), '1\u202f234\u202f568')
        self.assertAlmostEqual(sum(row['amount'] for row in self.view._summary_values.values()), 1234567.89123)
        self.assertFalse(self.view.dirty)
        self.changed.assert_not_called()
        self.allocation.return_value = AllocationTargets({STOCK_MARKET: {'World': 59.875},
                                                         TREASURY: {'HUF': 30.125}, CASH: {'EUR': 10}}, {'World': 'Equities'})
        self.view.refresh()
        self.assertEqual(self.view.return_weights['Equities'].get(), '59.88%')
        self.assertEqual(set(self.view.return_weights), {'Equities', 'Bonds', 'Gold'})
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
        self.set_income('1')
        self.current_total.return_value = 1000.125
        self.view.refresh()
        self.assertTrue(self.view.save())
        settings = self.view.settings()
        original = (self.directory / 'calculator.json').read_bytes()
        self.changed.reset_mock()
        self.current_total.return_value = 1250.875
        self.view.refresh()
        self.assertEqual(self.view.portfolio_value.get(), '1\u202f251')
        self.assertAlmostEqual(sum(row['amount'] for row in self.view._summary_values.values()), 1250.875)
        self.currency.set('EUR')
        self.view.refresh()
        self.assertEqual(self.view.portfolio_value.get(), '3')
        self.assertAlmostEqual(sum(row['amount'] for row in self.view._summary_values.values()), 1250.875)
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


    def test_overall_table_weights_returns_by_recommended_stock_mak_and_cash(self):
        self.view.set_base_rate(5.5, 'Effective today')
        self.current_total.return_value = 1000
        self.view.refresh()
        self.view._inputs['equities'].set('5.33')
        self.allocation.return_value = AllocationTargets({STOCK_MARKET: {'World': 30},
                                                         TREASURY: {'HUF': 70}, CASH: {}}, {'World': 'Equities'})
        self.view.refresh()
        self.assertEqual(self.view.overall_return.get(), '5.45%')
        self.assertEqual(self.view.allocation_summary.item(TREASURY, 'values'), ('MÁK', '70%', '700'))
        self.allocation.return_value = AllocationTargets({STOCK_MARKET: {'World': 20},
                                                         TREASURY: {'HUF': 70}, CASH: {'HUF': 10}}, {'World': 'Equities'})
        self.view.refresh()
        self.assertEqual(self.view.overall_return.get(), '4.92%')
        self.view._summary_sorting.sort('share')
        self.assertEqual(self.view.allocation_summary.get_children(), (CASH, STOCK_MARKET, TREASURY))
        self.view.refresh()
        self.assertEqual(self.view.allocation_summary.get_children(), (CASH, STOCK_MARKET, TREASURY))
        self.current_total.side_effect = ValueError('Missing portfolio prices.')
        self.view.refresh()
        self.assertFalse(self.view.allocation_summary.get_children())
        self.assertEqual(self.view.overall_return.get(), '—')

    def test_three_rows_keep_cost_inputs_and_results_visible_at_minimum_window_size(self):
        self.root.geometry('1050x760')
        self.view.pack(fill='both', expand=True, padx=16, pady=(52, 54))
        self.root.deiconify()
        self.view.set_base_rate(5.5, 'Effective today')
        self.current_total.return_value = 1_000_000
        self.set_monthly_cost('1000')
        self.set_single_rows([{'name': 'Trip', 'amount': '100', 'currency': 'HUF', 'due': '2030-01-01'}])
        self.view.refresh()
        self.view.message.set('Calculator settings saved.')
        self.root.update()
        self.assertIsInstance(self.view.cashflows.master, ttk.Notebook)
        costs = self.view.cashflows.master.master
        monthly = self.view.monthly_summary.master
        reserves = next(entry.master for entry in self.view._entries
                        if str(entry.cget('textvariable')) == str(self.view._inputs['emergency_months']))
        overall, funding = self.view.allocation_summary.master, self.view.breakdown.master
        summary = self.view._summary_panel
        self.assertEqual(reserves.cget('text'), 'Settings')
        self.assertEqual(summary.cget('text'), 'Portfolio summary')
        self.assertFalse(any(isinstance(widget, ttk.Combobox) for widget in reserves.winfo_children()))
        self.assertFalse(hasattr(self.view, 'projection_button'))
        self.assertTrue(all(panel.master in self.return_parents.values()
                            for panel in self.view.return_panels.values()))
        self.assertEqual(overall.cget('text'), 'Recommended overall allocations')
        self.assertLess(reserves.winfo_rootx(), summary.winfo_rootx())
        self.assertEqual(reserves.winfo_rooty(), summary.winfo_rooty())
        self.assertEqual(costs.winfo_rooty(), monthly.winfo_rooty())
        self.assertGreaterEqual(funding.winfo_rooty(), monthly.winfo_rooty() + monthly.winfo_height())
        self.assertGreaterEqual(overall.winfo_rooty(), funding.winfo_rooty() + funding.winfo_height())
        self.assertEqual(reserves.winfo_rootx(), costs.winfo_rootx())
        self.assertEqual(costs.winfo_rootx(), overall.winfo_rootx())
        self.assertEqual(summary.winfo_rootx(), monthly.winfo_rootx())
        self.assertEqual(monthly.winfo_rootx(), funding.winfo_rootx())
        self.assertEqual(funding.winfo_rootx(), summary.winfo_rootx())
        self.assertLess(summary.winfo_rooty(), costs.winfo_rooty())
        self.assertLess(costs.winfo_rooty(), overall.winfo_rooty())
        self.assertLess(overall.winfo_rootx(), funding.winfo_rootx())
        right = self.root.winfo_rootx() + self.root.winfo_width()
        self.assertEqual(int(overall.grid_info()['columnspan']), 2)
        for panel in (reserves, costs, monthly, overall, funding, summary):
            self.assertLessEqual(panel.winfo_rootx() + panel.winfo_width(), right)
        for table in (self.view.cashflows,):
            row = table.tree.bbox(table.tree.get_children()[0])
            self.assertTrue(row, 'Each input table must show a complete row.')
            self.assertLessEqual(row[1] + row[3], table.tree.winfo_height())
        self.assertEqual(self.view.allocation_summary.xview(), (0.0, 1.0))
        last = self.view.allocation_summary.bbox(self.view.allocation_summary.get_children()[-1])
        self.assertTrue(last)
        self.assertLessEqual(last[1] + last[3], self.view.allocation_summary.winfo_height())
        for widget in summary.winfo_children():
            self.assertLessEqual(widget.winfo_y() + widget.winfo_height(), summary.winfo_height())
            if isinstance(widget, ttk.Label):
                actual = font.Font(root=self.root, font=ttk.Style(self.root).lookup(
                    widget.cget('style') or 'TLabel', 'font')).actual()
                normal = font.Font(root=self.root, font=ttk.Style(self.root).lookup('TLabel', 'font')).actual()
                self.assertEqual(actual['size'], normal['size'])
                self.assertEqual(actual['weight'], normal['weight'])
        expense = self.view.breakdown.bbox(self.view.breakdown.get_children()[0])
        self.assertTrue(expense, 'The middle-right funding table must show a complete row.')
        self.assertLessEqual(expense[1] + expense[3], self.view.breakdown.winfo_height())
        monthly_last = self.view.monthly_summary.bbox('Net')
        self.assertLessEqual(monthly_last[1] + monthly_last[3], self.view.monthly_summary.winfo_height())
        self.assertLessEqual(self.view.monthly_summary.winfo_y() + self.view.monthly_summary.winfo_height(), monthly.winfo_height())

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
        self.assertTrue(self.view.allocation_summary.get_children())
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
                         {'Equities': '40%', 'Bonds': '10%', 'Gold': '10%'})
        self.assertEqual(self.view.overall_return.get(), '5.1%')
        self.view._inputs['gold'].set('4')
        self.assertEqual(self.view.overall_return.get(), '5.3%')
        self.allocation.return_value = AllocationTargets(weights, {**categories, 'Gold fund': 'Bonds'})
        self.view.refresh()
        self.assertEqual(self.view.return_weights['Bonds'].get(), '20%')
        self.assertEqual(self.view.return_weights['Gold'].get(), '0%')
        self.assertEqual(self.view.overall_return.get(), '5.2%')
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
        self.assertEqual(order(), ['Gold', 'Bonds', 'Equities'])
        editor = self.view._return_rows['Gold'][1]
        self.view._inputs['gold'].set('7')
        self.assertEqual(order(), ['Bonds', 'Equities', 'Gold'])
        self.assertIs(self.view._return_rows['Gold'][1], editor)
        self.view._sort_return_rows(2)
        self.assertEqual(order(), ['Bonds', 'Gold', 'Equities'])
        self.assertEqual(self.view._return_rows['Equities'][2].winfo_class(), 'TLabel')

    def test_mak_return_is_one_field_with_auto_rate_option(self):
        panel = self.view.return_panels[TREASURY]
        self.assertIsInstance(panel, ttk.Frame)
        self.assertNotIn(TREASURY, self.view._return_rows)
        self.assertFalse(any(isinstance(widget, (ttk.Button, ttk.Treeview))
                             for widget in panel.winfo_children()))
        self.assertIs(self.view._mak_entry.master, panel)
        self.assertEqual(self.view._mak_entry.grid_info()['row'], self.view._auto_rate_box.grid_info()['row'])
        self.view.set_base_rate(5.5, 'Effective today')
        self.assertEqual(self.view._mak_entry.get(), '5.5%')
        self.assertTrue(self.view._mak_entry.instate(['disabled']))
        self.view._auto_rate_box.invoke()
        self.assertTrue(self.view._mak_entry.instate(['!disabled']))
        self.view._inputs['mak'].set('5.1234567')
        self.assertEqual(self.view.settings().returns.mak, 5.1234567)
        self.view.set_enabled(False)
        self.assertTrue(self.view._mak_entry.instate(['disabled']))
        self.assertTrue(self.view._auto_rate_box.instate(['disabled']))
        self.view.set_enabled(True)
        self.assertTrue(self.view._mak_entry.instate(['!disabled']))

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

    def test_expense_editor_uses_month_field_and_cancel_keeps_original(self):
        from ui.dates import MonthEntry

        self.root.deiconify()
        self.view.pack(fill='both', expand=True)
        self.root.update()
        row = {'name': 'Car', 'amount': '500', 'currency': 'HUF', 'due': '2030-01-01'}
        self.set_single_rows([row])
        failures = []
        remaining = 50

        def inspect():
            nonlocal remaining
            dialog = next(child for child in self.view.cashflows.winfo_children() if isinstance(child, tk.Toplevel))
            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)

            months = [widget for widget in descendants(dialog) if isinstance(widget, MonthEntry)]
            visible = [widget for widget in months if widget.winfo_viewable()]
            if not visible and remaining:
                remaining -= 1
                self.root.after(20, inspect)
                return
            try:
                self.assertEqual(len(months), 2)
                self.assertEqual(len(visible), 1)
                self.assertEqual(visible[0].entry.get(), '2030-01')
            except Exception as error:
                failures.append(error)
            finally:
                dialog.destroy()

        self.root.after(30, inspect)
        item = self.view.cashflows.tree.get_children()[0]
        self.view.cashflows._edit(item)
        if failures:
            raise failures[0]
        self.assertEqual(self.flow_rows('Outgoing')[0]['start'], row['due'][:7])
        self.assertEqual(self.flow_rows('Outgoing')[0]['amount'], row['amount'])

    def test_discounted_expenses_show_current_reserves_and_overcommitment_keeps_funded_rows(self):
        self.view.set_base_rate(5, 'Effective today')
        self.current_total.return_value = 1000
        self.view.refresh()
        due = date.today().replace(year=date.today().year + 3).isoformat()
        self.set_single_rows([{'name': 'Car', 'amount': '500', 'currency': 'HUF', 'due': due}])
        self.view._changed()
        rows = [dict(purpose=self.view.breakdown.item(item, 'text'), **dict(zip(self.view.breakdown['columns'], self.view.breakdown.item(item, 'values'))))
                for item in self.view.breakdown.get_children()]
        car = next(row for row in rows if row['purpose'] == 'Car')
        self.assertNotIn('future', self.view.breakdown['columns'])
        self.assertEqual(car['income'], '0')
        self.assertLess(int(car['stock']) + int(car['mak']), 500)
        self.assertEqual(self.view.settings().outgoings[-1].amount, 500)
        self.assertTrue(self.view.save())
        self.view.refresh()
        self.assertFalse(self.view.dirty)
        self.set_monthly_cost('200')
        self.assertTrue(self.view.breakdown.get_children())
        self.assertIn('60%', self.view.allocation_summary.set(TREASURY, 'share'))
        self.assertIn('40%', self.view.allocation_summary.set(CASH, 'share'))
        self.assertIn('0%', self.view.allocation_summary.set(STOCK_MARKET, 'share'))
        self.assertIn('HUF', self.view.message.get())
        self.assertAlmostEqual(sum(value['amount'] for value in self.view._summary_values.values()), 1000)
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
        self.set_single_rows([{'name': 'Car', 'amount': '500', 'currency': 'HUF', 'due': due}])
        self.view.refresh()
        baseline = list(self.view._values.values())

        self.set_income('150')
        self.assertEqual(self.view._monthly_values['Net']['recurring'], -50)
        self.assertEqual(self.view._saving_ratio.get(), '-33.33%')
        self.assertIn('0', self.view.monthly_summary.set('Net', 'recurring'))
        self.assertEqual(list(self.view._values.values()), baseline)

        self.set_income('300')
        self.assertEqual(self.view._monthly_values['Net']['recurring'], 100)
        self.assertIn('100', self.view.monthly_summary.set('Net', 'recurring'))
        rows = {value['purpose']: value for value in self.view._values.values()}
        self.assertAlmostEqual(rows['Car']['stock'], baseline[0]['stock'])
        self.assertAlmostEqual(rows['Car']['income'] + rows['Car']['stock'], 500)
        self.assertAlmostEqual(rows['Car']['mak'], 0)
        self.assertEqual(set(rows), {'Car'})
        self.assertAlmostEqual(sum(value['amount'] for value in self.view._summary_values.values()), 1000)
        displayed = {self.view.breakdown.item(item, 'text'): self.view.breakdown.set(item, 'income')
                     for item in self.view.breakdown.get_children()}
        self.assertEqual(displayed, {'Car': format_amount(rows['Car']['income'])})
        self.assertTrue(self.view.save())
        self.view.load(self.directory)
        self.assertEqual(self.view.settings().incomes[0].amount, 300)
        self.assertEqual(self.view._monthly_values['Net']['recurring'], 100)

    def test_overcommitment_preserves_required_mak_and_allocates_remaining_money_to_stock(self):
        self.view.set_base_rate(0, 'Effective today')
        self.current_total.return_value = 1000
        self.view.refresh()
        self.view._inputs['equities'].set('0')
        self.set_monthly_cost('100')
        self.view._inputs['emergency_months'].set('2')
        due = date.today().replace(year=date.today().year + 10).isoformat()
        self.set_single_rows([{'name': 'Future expense', 'amount': '2000', 'currency': 'HUF', 'due': due}])
        self.view._changed()
        self.assertIn('60%', self.view.allocation_summary.set(STOCK_MARKET, 'share'))
        self.assertIn('20%', self.view.allocation_summary.set(CASH, 'share'))
        self.assertIn('20%', self.view.allocation_summary.set(TREASURY, 'share'))
        self.assertAlmostEqual(sum(value['stock'] for value in self.view._values.values()), 600)
        self.assertAlmostEqual(self.view._summary_values[CASH]['amount'], 200)
        self.assertIn('1\u202f400', self.view.message.get())

    def test_monthly_cost_total_drives_bank_cash_and_full_emergency_reserve(self):
        self.view.set_base_rate(0, 'Effective today')
        self.current_total.return_value = 1000
        self.set_monthly_cost('100')
        self.view._inputs['emergency_months'].set('1')
        self.assertFalse(self.view.breakdown.get_children())
        self.assertEqual(self.view._summary_values[CASH]['amount'], 200)
        self.assertEqual(self.view._summary_values[TREASURY]['amount'], 380)
        self.assertEqual(self.view.monthly_summary.set('Outgoings', 'recurring'), '100')
        self.view._inputs['cash_months'].set('4')
        self.assertEqual(self.view._summary_values[CASH]['amount'], 400)
        self.assertEqual(self.view._summary_values[TREASURY]['amount'], 300)
        self.assertTrue(self.view.save())
        self.view.load(self.directory)
        self.assertEqual(self.view.settings().cash_months, 4)
        self.assertEqual(self.view.settings().outgoings[0].amount, 100)
        self.view._inputs['cash_months'].set('0')
        self.assertEqual(self.view._summary_values[CASH]['amount'], 0)
        for value in ('1.5', '-1', '121'):
            self.view._inputs['cash_months'].set(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.view.settings()

    def test_fractional_huf_income_and_costs_are_rejected_without_writing_settings(self):
        self.view.set_base_rate(5, 'Effective today')
        self.current_total.return_value = 1000
        self.set_income('100.5')
        with self.assertRaisesRegex(ValueError, 'whole|integer'):
            self.view.settings()
        self.assertFalse(self.view.save())
        self.set_income('0')
        for table in (self.view.cashflows,):
            table.set_rows([{'kind': 'Outgoing', 'name': 'Cost', 'amount': '100.5', 'currency': 'HUF', 'start': '2030-01', 'frequency': 'once'}])
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
        self.set_cost_rows(costs)
        self.currency.set('USD')
        self.view.refresh()
        self.set_income('300.25')
        self.currency.set('HUF')
        self.view.refresh()
        expense = {'name': 'Car', 'amount': '500.25', 'currency': 'USD', 'due': date.today().isoformat()}
        self.set_single_rows([expense])
        self.view._changed()
        settings = self.view.settings()
        self.assertEqual(settings.monthly_spending(self.rates()), 45_200)
        self.assertEqual(settings.monthly_totals(date.today().year, date.today().month, self.rates()).recurring_income - settings.monthly_spending(self.rates()), 62_890)
        self.assertEqual(self.view.monthly_summary.set('Outgoings', 'recurring'), '45\u202f200')
        self.assertEqual(self.view.monthly_summary.set('Income', 'recurring'), '108\u202f090')
        self.assertEqual(self.view.cashflows.display_value(costs[0], 'amount'), '100')
        self.assertEqual(self.view.required_currencies, {'HUF', 'EUR', 'USD'})
        self.assertTrue(self.view.save())
        original = (self.directory / 'calculator.json').read_bytes()
        self.currency.set('EUR')
        self.view.refresh()
        self.assertEqual(self.view.monthly_summary.set('Outgoings', 'recurring'), '113')
        self.assertEqual(self.view.monthly_summary.set('Net', 'recurring'), '157')
        self.assertEqual(self.view.allocation_summary.heading('amount', 'text'), 'Amount (EUR)')
        item = next(item for item, row in self.view._values.items() if row['purpose'] == 'Car')
        self.assertEqual(self.view.breakdown.set(item, 'cash'), '450')
        self.assertEqual(self.view._values[item]['cash'], 180_090)
        for item, values in self.view._summary_values.items():
            self.assertEqual(self.view.allocation_summary.set(item, 'amount'), format_amount(values['amount'] / 400))
        self.assertEqual((self.directory / 'calculator.json').read_bytes(), original)
        self.assertFalse(self.view.dirty)
        self.view.load(self.directory)
        self.assertEqual(self.view.settings(), settings)
        self.assertEqual([(row['name'], float(row['amount']), row['currency']) for row in self.flow_rows('Outgoing') if row['frequency'] != 'once'],
                         [('Rent', 100.5, 'EUR'), ('Food', 5000, 'HUF')])
        self.assertEqual(self.flow_rows('Outgoing')[-1]['currency'], 'USD')
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
        self.set_income('100.5')
        self.currency.set('HUF')
        self.view.refresh()
        self.assertTrue(self.view.allocation_summary.get_children())
        self.rates.return_value = {}
        self.view.refresh()
        self.assertFalse(self.view.breakdown.get_children())
        self.assertIn('USD/HUF', self.view.message.get())
        self.assertTrue(self.view.save())
        self.assertEqual(load_settings(self.directory)[0].incomes[0].amount, 100.5)
        self.set_income('unfinished')
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
        self.set_income('12')
        first_timer = self.view._autosave_id
        self.set_income('1200')
        self.assertNotEqual(self.view._autosave_id, first_timer)
        self.assertFalse(path.exists())
        self.wait_for_autosave()
        self.assertEqual(load_settings(self.directory)[0].incomes[0].amount, 1200)
        self.assertFalse(self.view.dirty)
        self.assertIsNone(self.view._autosave_id)
        self.set_income('')
        self.wait_for_autosave()
        self.assertEqual(load_settings(self.directory)[0].incomes[0].amount, 1200)
        self.assertTrue(self.view.dirty)
        self.assertIn('Cannot save calculator settings', self.view.message.get())
        self.set_income('2500')
        self.view.save()
        self.assertEqual(load_settings(self.directory)[0].incomes[0].amount, 2500)
        self.assertFalse(self.view.dirty)
        self.assertNotIn('Cannot save', self.view.message.get())

    def test_load_and_refresh_do_not_save_and_loading_cancels_pending_edits(self):
        self.set_income('500')
        self.assertIsNotNone(self.view._autosave_id)
        self.view.load(self.directory)
        self.assertIsNone(self.view._autosave_id)
        self.assertEqual(self.view.settings().incomes, ())
        with patch('ui.calculator.save_settings') as save:
            self.view.set_base_rate(6, 'Today')
            self.current_total.return_value = 2000
            self.currency.set('EUR')
            self.view.refresh()
            self.wait_for_autosave()
        save.assert_not_called()
        self.assertFalse((self.directory / 'calculator.json').exists())

    def test_failed_autosave_is_visible_until_a_successful_retry(self):
        self.set_income('500')
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
        self.set_income('500')
        timer = self.view._autosave_id
        self.view.destroy()
        self.assertNotIn(timer, self.root.tk.call('after', 'info'))

    def test_disabled_controls_cannot_start_callbacks_and_expense_validation_is_recoverable(self):
        self.view.set_enabled(False)
        self.assertTrue(all(entry.instate(['disabled']) for entry in self.view._entries))
        self.assertTrue(self.view._auto_rate_box.instate(['disabled']))
        self.assertFalse(self.view.cashflows._enabled)
        self.view.set_enabled(True)
        self.assertTrue(self.view._mak_entry.instate(['disabled']))
        for row in ({'name': '', 'amount': '5', 'currency': 'HUF', 'due': '2030-01-01'},
                    {'name': 'Car', 'amount': '-1', 'currency': 'HUF', 'due': '2030-01-01'},
                    {'name': 'Car', 'amount': '5.125', 'currency': 'HUF', 'due': '2030-01-01'},
                    {'name': 'Car', 'amount': '5', 'currency': 'HUF', 'due': 'bad-date'}):
            with self.subTest(row=row), self.assertRaises(ValueError):
                self.view.cashflows._validate(dict(row, kind='Outgoing', start=row['due'][:7], frequency='once'), None)
        validated = self.view.cashflows._validate({'kind': 'Outgoing', 'name': ' Car ', 'amount': '5', 'currency': 'HUF', 'start': '2030-01', 'frequency': 'once'}, None)
        self.assertEqual(validated['name'], 'Car')
        self.assertEqual(float(validated['amount']), 5)
        self.assertEqual(validated['start'], '2030-01')

    def test_schedule_editor_preserves_month_when_frequency_changes(self):
        from ui.dates import MonthEntry

        form = ttk.Frame(self.root)
        self.addCleanup(form.destroy)
        variables, _ = self.view.cashflows._edit_fields(form, None)
        variables['name'].set('Loan')
        variables['amount'].set('250')
        variables['start'].set('2030-01')
        values = lambda: {key: variable.get() for key, variable in variables.items()}
        selector = next(widget for widget in form.winfo_children()
                        if isinstance(widget, ttk.Combobox)
                        and str(widget.cget('textvariable')) == str(variables['frequency']))
        self.assertEqual(selector['values'], ('Once', 'Monthly', 'Annually'))
        end = next(widget for widget in form.winfo_children()
                   if isinstance(widget, MonthEntry) and str(widget._value) == str(variables['end']))
        self.assertFalse(end.grid_info())
        for label, frequency in (('Monthly', 'monthly'), ('Annually', 'annual')):
            variables['frequency'].set(label)
            variables['ending'].set('End month')
            variables['end'].set('2031-01')
            self.assertTrue(end.grid_info())
            starts = [widget for widget in form.winfo_children()
                      if type(widget) is MonthEntry and str(widget._value) == str(variables['start'])]
            self.assertEqual(len(starts), 1)
            self.assertEqual(variables['start'].get(), '2030-01')
            row = self.view.cashflows._validate(values(), None)
            self.assertEqual((row['frequency'], row['end'], row['count']), (frequency, '2031-01', ''))
            variables['ending'].set('Number of payments')
            variables['count'].set('12')
            row = self.view.cashflows._validate(values(), None)
            self.assertEqual((row['count'], row['end']), ('12', ''))
            variables['frequency'].set('Once')
            row = self.view.cashflows._validate(values(), None)
            self.assertEqual((row['frequency'], row['count'], row['end']), ('once', '', ''))
            self.assertEqual(row['start'], '2030-01')
        variables['frequency'].set('Monthly')
        variables['start'].set('2030-02')
        variables['frequency'].set('Once')
        self.assertEqual(variables['start'].get(), '2030-02')
        variables['frequency'].set('Monthly')
        variables['count'].set('1.5')
        with self.assertRaisesRegex(ValueError, 'whole'):
            self.view.cashflows._validate(values(), None)

    def test_annual_schedule_round_trips_with_month_dates_and_updates_selected_month(self):
        from ui.dates import MonthEntry

        self.view.set_base_rate(0, 'Today')
        table = self.view.cashflows
        flow = CashFlow('Annual bonus', 1200, date(2030, 7, 1), frequency='annual', count=3)
        self.set_flows('Income', (flow,))
        self.view._changed()
        self.assertTrue(self.view.save())
        self.view.load(self.directory)
        self.assertEqual(self.view.settings().incomes, (flow,))
        row = table.get_rows()[0]
        self.assertEqual(row['start'], '2030-07')
        self.assertEqual(table.display_value(row, 'frequency'), 'Annually')
        self.assertIsInstance(self.view._month_box, MonthEntry)
        self.view._month.set('2031-07')
        self.assertEqual(self.view.monthly_summary.set('Income', 'recurring'), '1\u202f200')
        self.view._month.set('2031-08')
        self.assertEqual(self.view.monthly_summary.set('Income', 'recurring'), '0')
        self.view._month.set('2031-')
        self.assertFalse(self.view.monthly_summary.get_children())
        self.assertFalse(self.view.dirty)

    def test_funding_only_displays_once_outgoings_while_all_schedules_remain_saved(self):
        self.view.set_base_rate(0, 'Effective today')
        self.current_total.return_value = 100_000
        start = date.today() + timedelta(days=400)
        self.set_flows('Outgoing', (CashFlow('Loan', 100, start, frequency='monthly', count=3),
                                    CashFlow('Loan', 250, start, frequency='monthly', count=2),
                                    CashFlow('Trip', 600, start), CashFlow('Car', 1000, start)))
        self.view._changed()
        self.assertEqual([self.view.breakdown.item(item, 'text') for item in self.view.breakdown.get_children()],
                         ['Trip', 'Car'])
        self.assertNotIn('future', self.view.breakdown['columns'])
        self.assertTrue(all(not self.view.breakdown.get_children(item) for item in self.view.breakdown.get_children()))
        self.assertAlmostEqual(sum(row['amount'] for row in self.view._summary_values.values()), 100_000)
        self.view._sorting.sort('stock')
        self.assertEqual(self.view.breakdown.item(self.view.breakdown.get_children()[0], 'text'), 'Trip')
        self.assertTrue(self.view.save())
        expected = self.view.settings()
        self.view.load(self.directory)
        self.assertEqual(self.view.settings(), expected)

    def test_ongoing_costs_define_baseline_and_finite_instalments_are_funded_separately(self):
        from portfolio.calculator import CashFlow

        self.view.set_base_rate(0, 'Effective today')
        self.current_total.return_value = 100_000
        self.set_income('1000')
        self.set_flows('Outgoing', (CashFlow('Food', 200, date.today(), frequency='monthly'),
                                          CashFlow('Loan', 300, date.today(), frequency='monthly', count=12)))
        self.view._changed()
        self.assertEqual(self.view.monthly_summary.set('Outgoings', 'recurring'), '500')
        self.assertEqual(self.view.monthly_summary.set('Net', 'recurring'), '500')
        names = [self.view.breakdown.item(item, 'text') for item in self.view.breakdown.get_children()]
        self.assertNotIn('Food', names)
        self.assertNotIn('Loan', names)
        self.assertEqual(names, [])
        self.assertEqual(self.view.cashflows.display_value(self.flow_rows('Outgoing')[0], 'ending'), 'Ongoing')

    def test_selected_month_shows_recurring_single_and_net_without_changing_the_plan(self):
        self.view.set_base_rate(0, 'Today')
        self.current_total.return_value = 100_000
        self.set_flows('Income', (CashFlow('Salary', 1000, date(2030, 1, 31), frequency='monthly', count=3),
                                         CashFlow('Bonus', 250, date(2030, 2, 10))))
        self.set_flows('Outgoing', (CashFlow('Rent', 400, date(2030, 1, 31), frequency='monthly'),
                                           CashFlow('Loan', 100, date(2030, 1, 31), frequency='monthly', count=2),
                                           CashFlow('Trip', 300, date(2030, 2, 15))))
        self.view._changed()
        self.assertTrue(self.view.save())
        settings, original = self.view.settings(), (self.directory / 'calculator.json').read_bytes()
        recommended = dict(self.view._summary_values)
        self.changed.reset_mock()
        self.view._month.set('2030-02')
        self.assertEqual(self.view.monthly_summary.item('Income', 'values'), ('Income', '1\u202f000', '250', '1\u202f250'))
        self.assertEqual(self.view.monthly_summary.item('Outgoings', 'values'), ('Outgoings', '500', '300', '800'))
        self.assertEqual(self.view.monthly_summary.item('Net', 'values'), ('Net', '500', '-50', '450'))
        self.assertEqual(self.view._saving_ratio.get(), '50%')
        for currency in ('EUR', 'HUF'):
            self.currency.set(currency)
            self.view.refresh()
            self.assertEqual(self.view._saving_ratio.get(), '50%')
        self.view._month.set('2030-')
        self.assertEqual(self.view._saving_ratio.get(), '—')
        self.view._month.set('2030-04')
        self.assertEqual(self.view.monthly_summary.set('Net', 'total'), '-400')
        self.assertEqual(self.view._saving_ratio.get(), '—')
        self.view._monthly_sorting.sort('total')
        self.assertEqual(self.view.monthly_summary.get_children(), ('Net', 'Income', 'Outgoings'))
        self.assertEqual(self.view.settings(), settings)
        self.assertEqual(self.view._summary_values, recommended)
        self.assertEqual((self.directory / 'calculator.json').read_bytes(), original)
        self.assertFalse(self.view.dirty)
        self.assertIsNone(self.view._autosave_id)
        self.changed.assert_not_called()

    def test_income_rows_keep_native_precision_through_currency_refresh_and_missing_rates(self):
        self.view.set_base_rate(0, 'Today')
        self.set_income('100.25123456789', 'USD')
        self.assertTrue(self.view.save())
        native = self.view.settings()
        original = (self.directory / 'calculator.json').read_bytes()
        self.changed.reset_mock()
        for currency in ('EUR', 'HUF', 'GBP', 'USD', 'HUF'):
            self.currency.set(currency)
            self.view.refresh()
            self.assertEqual(self.view.settings(), native)
        self.assertEqual(self.view.monthly_summary.set('Income', 'recurring'), '36\u202f090')
        self.rates.return_value = {}
        self.view.refresh()
        self.assertFalse(self.view.monthly_summary.get_children())
        self.assertEqual(self.view._saving_ratio.get(), '—')
        self.assertTrue(self.view.cashflows._enabled)
        self.assertEqual(self.view.required_currencies, {'HUF', 'USD'})
        self.assertEqual((self.directory / 'calculator.json').read_bytes(), original)
        self.assertFalse(self.view.dirty)
        self.changed.assert_not_called()
        self.view.load(self.directory)
        self.assertEqual(self.view.settings(), native)

    def test_unified_cashflow_type_edit_moves_the_schedule_and_updates_summary_and_storage(self):
        self.view.set_base_rate(0, 'Today')
        self.current_total.return_value = 100_000
        self.set_flows('Outgoing', (CashFlow('Refund', 300, date.today()),))
        table = self.view.cashflows
        self.assertEqual(table.tree['columns'][0], 'kind')
        self.assertEqual(table.tree.heading('kind', 'text'), 'Type')
        self.assertEqual(table.tree.heading('start', 'text'), 'Begins')
        self.view._changed()
        self.assertEqual(self.view.monthly_summary.set('Outgoings', 'single'), '300')
        self.assertEqual(len(self.view.breakdown.get_children()), 1)
        old = table.get_rows()[0]
        new = table._validate(dict(old, kind='Income'), old)
        table.set_rows([new])
        table._notify('edit', old, new)
        self.wait_for_autosave()
        settings = load_settings(self.directory)[0]
        self.assertEqual(settings.outgoings, ())
        self.assertEqual(settings.incomes[0].name, 'Refund')
        self.assertEqual(self.view.monthly_summary.set('Income', 'single'), '300')
        self.assertEqual(self.view.monthly_summary.set('Outgoings', 'single'), '0')
        self.assertFalse(self.view.breakdown.get_children())
        self.view.load(self.directory)
        self.assertEqual(table.get_rows()[0]['kind'], 'Income')
        with self.assertRaisesRegex(ValueError, 'Choose Income or Outgoing'):
            table._validate(dict(new, kind='Other'), new)

    def test_schedule_end_dates_have_no_prefix_and_type_is_a_dropdown(self):
        from tkinter import ttk

        table = self.view.cashflows
        flow = CashFlow('Rent', 300, date(2030, 1, 1), frequency='monthly', end=date(2030, 6, 1))
        self.assertEqual(table.display_value(table._row(flow, 'Outgoing'), 'ending'), '2030-06')
        form = ttk.Frame(self.root)
        self.addCleanup(form.destroy)
        variables, _ = table._edit_fields(form, None)
        selector = next(widget for widget in form.winfo_children()
                        if isinstance(widget, ttk.Combobox)
                        and str(widget.cget('textvariable')) == str(variables['kind']))
        self.assertEqual(selector['values'], ('Income', 'Outgoing'))
        self.assertTrue(selector.instate(['readonly']))
        self.assertEqual(variables['kind'].get(), 'Outgoing')
