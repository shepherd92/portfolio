"""Calculator policy informs plans while keeping Portfolio inputs and saved CSVs unchanged."""

from dataclasses import replace
from datetime import date, timedelta
import os
from pathlib import Path
import tempfile
import time
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from portfolio.models import (AllocationTargets, AssetDetails, CASH, STOCK_MARKET, TREASURY,
                              Holding, ManualHolding, MarketData, Portfolio, Quote)
from portfolio.market import BaseRate
from portfolio.funding import prepare_allocation
from portfolio.calculator import CashFlow
from portfolio.storage import load_settings
from portfolio.storage import save_portfolio
from ui.app import PortfolioApp


@unittest.skipUnless(os.environ.get('REBALANCE_GUI_TESTS') == '1',
                     'GUI tests require REBALANCE_GUI_TESTS=1 and a display')
class CalculatorIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        allocation = AllocationTargets(
            {STOCK_MARKET: {'World': 60, 'Bullion': 20, 'Debt': 10},
             TREASURY: {'HUF': 10}, CASH: {'EUR': 0}},
            {'World': 'Equities', 'Bullion': 'Gold', 'Debt': 'Bonds'})
        self.portfolio = Portfolio((Holding('EQ', 'World', 1), Holding('AU', 'Bullion', 0),
                                    Holding('FI', 'Debt', 0)), allocation, {'EUR': 50},
                                   (ManualHolding(4000, 'HUF'),))
        save_portfolio(self.portfolio, self.directory)
        self.market = MarketData({ticker: Quote(price, 'EUR')
                                 for ticker, price in {'EQ': 100, 'AU': 50, 'FI': 10}.items()}, {'HUF': .0025})
        self.prices = patch('ui.app.fetch_market_data', return_value=self.market).start()
        self.base_rate = patch('ui.app.fetch_base_rate', return_value=BaseRate(5.5, date(2026, 9, 1))).start()
        self.errors = patch('ui.app.messagebox.showerror').start()
        patch('ui.app.fetch_asset_details', return_value=AssetDetails()).start()
        self.addCleanup(patch.stopall)
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = PortfolioApp(self.root, self.directory)
        self.view = self.app.calculator
        self.addCleanup(self.cleanup_root)
        self.finish_work()
        self.prices.reset_mock()

    def cleanup_root(self):
        if not self.app.closed:
            self.app.dirty = self.view.dirty = False
            self.app.close()

    def reopen(self):
        self.app.dirty = self.view.dirty = False
        self.app.close()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = PortfolioApp(self.root, self.directory)
        self.view = self.app.calculator
        self.finish_work()

    def finish_work(self):
        deadline = time.monotonic() + 5
        self.root.update()
        while self.app.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertFalse(self.app.busy, 'Background work did not finish')
        self.root.update()

    def visit(self):
        self.app.notebook.select(self.view)
        self.root.update()
        self.app._update_calculator()
        self.finish_work()

    def configure_calculator(self):
        self.view._auto_rate.set(False)
        self.view._inputs['mak'].set('5')

    def csv_bytes(self):
        return {path.name: path.read_bytes() for path in self.directory.glob('*.csv')}

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

    def test_returns_follow_category_assumptions_and_portfolio_targets_automatically(self):
        self.configure_calculator()
        for name, value in {'equities': '8', 'gold': '2', 'bonds': '4'}.items():
            self.view._inputs[name].set(value)
        self.assertNotIn('market_return', self.view._inputs)
        self.assertEqual({name: value.get() for name, value in self.view.return_weights.items()},
                         {'Equities': '60%', 'Gold': '20%', 'Bonds': '10%'})
        self.assertEqual(self.view.overall_return.get(), '6.1%')
        self.view._inputs['equities'].set('10')
        self.assertEqual(self.view.overall_return.get(), '7.3%')
        rows = self.app.target_table.get_rows()
        for row in rows:
            if row['parent'] == STOCK_MARKET and row['group'] in ('World', 'Bullion'):
                row['target'] = '20' if row['group'] == 'World' else '60'
        self.app.target_table.set_rows(rows)
        self.app._edited()
        self.assertEqual(self.view.return_weights['Equities'].get(), '20%')
        self.assertEqual(self.view.return_weights['Gold'].get(), '60%')
        self.assertEqual(self.view.overall_return.get(), '4.1%')

    def test_portfolio_return_editors_share_autosave_and_clear_invalid_projection(self):
        self.configure_calculator()
        before = self.csv_bytes()
        self.app.projection_button.invoke()
        window = self.app.projection_window
        self.assertIsNotNone(window._projection)
        starting_value = sum(series[0] for series in window._projection.portfolio.values())
        self.assertAlmostEqual(starting_value, 160)
        self.app.plan = object()
        editor = self.view._return_rows['Equities'][1]
        editor.delete(0, 'end')
        editor.insert(0, '7.1234567')
        self.assertEqual(self.view.settings().returns.equities, 7.1234567)
        self.assertIsNone(self.app.plan)
        self.assertIsNotNone(window._projection)
        self.root.after(550, self.root.quit)
        self.root.mainloop()
        settings, _ = load_settings(self.directory)
        self.assertEqual(settings.returns.equities, 7.1234567)
        self.assertEqual(settings.returns.mak, 5)
        saved = (self.directory / 'calculator.json').read_bytes()
        editor.delete(0, 'end')
        editor.insert(0, 'invalid')
        self.assertFalse(self.view.allocation_summary.get_children())
        self.assertEqual(self.view.overall_return.get(), '—')
        self.assertIsNone(window._projection)
        self.assertFalse(window.canvas.find_withtag('area'))
        self.assertIn('Equities', window._message)
        self.assertFalse(self.view.save())
        self.assertEqual((self.directory / 'calculator.json').read_bytes(), saved)
        editor.delete(0, 'end')
        editor.insert(0, '8.25')
        self.assertTrue(self.view.save())
        self.assertIsNotNone(window._projection)
        self.assertEqual(self.csv_bytes(), before)
        self.assertFalse(self.app.dirty)
        self.reopen()
        self.assertEqual(self.view.settings().returns.equities, 8.25)
        self.assertEqual(self.view.settings().returns.mak, 5)

    def test_return_controls_and_projection_exist_only_on_corresponding_portfolio_pages(self):
        self.root.deiconify()
        self.root.geometry('1050x760')
        self.app.notebook.select(self.app.portfolio_page)
        self.app.portfolio_notebook.select(self.app.summary_page)
        self.root.update()
        self.assertTrue(self.app.projection_button.winfo_ismapped())
        self.assertIs(self.app.projection_button.master.master, self.app.summary_page)
        self.assertIs(self.app.target_table.edit_button.master, self.app.projection_button.master)
        self.assertEqual(self.app.target_table.edit_button.winfo_rooty(), self.app.projection_button.winfo_rooty())
        right = self.root.winfo_rootx() + self.root.winfo_width()
        bottom = self.root.winfo_rooty() + self.root.winfo_height()
        for branch in (STOCK_MARKET, TREASURY):
            page = self.app.allocation_pages[branch]
            if branch == STOCK_MARKET:
                self.app.portfolio_notebook.select(self.app.stock_page)
                self.app.stock_notebook.select(page)
            else:
                self.app.portfolio_notebook.select(page)
            self.root.update()
            self.assertFalse(self.app.projection_button.winfo_ismapped())
            self.assertIs(self.view.return_panels[branch].master, page.return_controls)
            if branch == STOCK_MARKET:
                controls = [widget for widgets in self.view._return_rows.values() for widget in widgets]
            else:
                controls = [self.view._mak_entry, self.view._auto_rate_box]
            for widget in (*controls, page.targets, page.holdings):
                self.assertTrue(widget.winfo_ismapped())
                self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), right)
                self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), bottom)
            self.assertEqual(self.view._auto_rate_box.winfo_ismapped(), branch == TREASURY)
            for widget in (page.targets.edit_button, page.holdings.add_button, page.holdings.edit_button,
                           page.holdings.remove_button):
                self.assertTrue(widget.winfo_ismapped())
                self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), page.winfo_rooty() + page.winfo_height())
            self.view.message.set('Equities: enter a number.')
            self.root.update()
            self.assertTrue(self.view._return_messages[0 if branch == STOCK_MARKET else 1].winfo_ismapped())
            self.assertLessEqual(page.holdings.add_button.winfo_rooty() + page.holdings.add_button.winfo_height(),
                                 page.winfo_rooty() + page.winfo_height())
            self.view.refresh()
        self.app._set_busy(True)
        self.app.projection_button.invoke()
        self.assertIsNone(self.app.projection_window)
        self.assertTrue(self.view._auto_rate_box.instate(['disabled']))
        self.assertTrue(self.view._mak_entry.instate(['disabled']))
        self.assertTrue(all(widgets[1].instate(['disabled']) for widgets in self.view._return_rows.values()))
        self.app._set_busy(False)
        self.assertTrue(self.view._mak_entry.instate(['disabled']))
        self.app.portfolio_notebook.select(self.app.summary_page)
        self.app.projection_button.invoke()
        window = self.app.projection_window
        self.assertIsNotNone(window._projection)
        self.app.projection_button.invoke()
        self.assertIs(self.app.projection_window, window)
        self.app.notebook.select(self.view)
        self.root.update()
        self.assertFalse(self.app.projection_button.winfo_ismapped())
        self.assertFalse(any(widgets[1].winfo_ismapped() for widgets in self.view._return_rows.values()))
        self.assertFalse(self.view._mak_entry.winfo_ismapped())

    def test_unclassified_positive_targets_block_calculation_and_planning(self):
        self.configure_calculator()
        classes = self.app.class_table.get_rows()
        next(row for row in classes if row['group'] == 'Bullion')['category'] = ''
        self.app.class_table.set_rows(classes)
        self.app._edited()
        self.assertFalse(self.view.allocation_summary.get_children())
        self.assertIn('Bullion', self.view.message.get())
        self.app.generate_plan()
        self.finish_work()
        self.assertIsNone(self.app.plan)
        self.errors.assert_called_once()
        self.assertIn('Bullion', str(self.errors.call_args))

        rows = self.app.target_table.get_rows()
        for row in rows:
            if row['group'] == 'Bullion':
                row['target'] = '0'
            elif row['group'] == 'World':
                row['target'] = '80'
        self.app.target_table.set_rows(rows)
        self.app._edited()
        self.assertEqual(self.view.overall_return.get(), '5.6%')
        self.errors.reset_mock()
        self.app.generate_plan()
        self.finish_work()
        self.assertIsNotNone(self.app.plan)
        self.errors.assert_not_called()

    def test_portfolio_value_is_available_on_startup_without_a_calculator_visit(self):
        self.assertEqual(self.view.portfolio_value.get(), '160')
        self.assertEqual(self.app._calculator_total(), 64_000)
        self.assertEqual(sum(row['amount'] for row in self.view._summary_values.values()), 64_000)
        self.prices.assert_not_called()
        self.assertIs(self.app.market, self.market)
        self.assertFalse(self.app.dirty)
        self.assertFalse(self.view.dirty)

    def test_cash_and_quote_changes_refresh_exact_value_without_editing_calculator_settings(self):
        self.set_income('1')
        self.assertTrue(self.view.save())
        original = (self.directory / 'calculator.json').read_bytes()
        settings = self.view.settings()
        self.app.cash_table.set_rows([{'currency': 'EUR', 'amount': '75.25'}])
        self.app._edited()
        self.assertEqual(self.view.portfolio_value.get(), '185')
        self.assertEqual(self.app._calculator_total(), 74_100)
        updated = MarketData({**self.market.quotes, 'EQ': Quote(125.125, 'EUR')}, {'HUF': .0025})
        self.app._accept_market(updated)
        self.assertEqual(self.view.portfolio_value.get(), '210')
        self.assertEqual(self.app._calculator_total(), 84_150)
        self.assertAlmostEqual(sum(row['amount'] for row in self.view._summary_values.values()), 84_150)
        self.assertEqual(self.view.settings(), settings)
        self.assertEqual((self.directory / 'calculator.json').read_bytes(), original)
        self.assertFalse(self.view.dirty)
        self.view.load(self.directory)
        self.assertEqual(self.view.portfolio_value.get(), '210')
        self.prices.assert_not_called()

    def test_calculator_currency_is_shared_and_refreshes_quotes_without_changing_huf_recommendations(self):
        portfolio = self.app.portfolio()
        box = self.app.base_entry
        self.assertEqual(str(box.cget('textvariable')), str(self.app.base_currency))
        self.assertTrue(box.instate(['readonly']))
        self.prices.return_value = MarketData(self.market.quotes, {'EUR': 1.1, 'HUF': .00275}, 'USD')
        self.app.base_currency.set('USD')
        self.finish_work()
        self.prices.assert_called_once_with(portfolio, 'USD', extra_currencies=('HUF',), cache=self.app.market_cache)
        self.assertEqual(self.view.portfolio_value.get(), '176')
        self.assertAlmostEqual(self.app._calculator_total(), 64_000)
        self.assertAlmostEqual(sum(row['amount'] for row in self.view._summary_values.values()), 64_000)
        self.assertEqual(self.app.portfolio(), portfolio)
        self.assertFalse(self.view.dirty)

    def test_currency_switch_preserves_unsaved_income_during_pending_quote_refresh(self):
        self.set_income('12.125', 'EUR')
        settings = self.view.settings()
        self.assertEqual(settings.incomes[0].currency, 'EUR')
        self.prices.return_value = MarketData(self.market.quotes, {'EUR': 1.1, 'HUF': .00275}, 'USD')
        self.app.base_currency.set('USD')
        self.assertFalse(self.view.cashflows._enabled)
        self.assertEqual(self.view.settings(), settings)
        self.assertTrue(self.view.save())
        original = (self.directory / 'calculator.json').read_bytes()
        self.finish_work()
        self.prices.assert_called_once_with(self.app.portfolio(), 'USD', extra_currencies=('EUR', 'HUF'), cache=self.app.market_cache)
        self.assertEqual(self.view.monthly_summary.set('Income', 'recurring'), '13')
        self.assertEqual(self.view.settings(), settings)
        self.assertEqual((self.directory / 'calculator.json').read_bytes(), original)
        self.assertFalse(self.view.dirty)


    def test_missing_valuation_clears_results_without_inventing_a_zero_portfolio(self):
        for market in (None, MarketData(self.market.quotes, {}), MarketData({}, {'HUF': .0025})):
            with self.subTest(market=market):
                self.app.market = market
                self.view.refresh()
                self.assertEqual(self.view.portfolio_value.get(), '—')
                self.assertFalse(self.view.allocation_summary.get_children())
                self.assertFalse(self.view.breakdown.get_children())
                self.assertTrue(self.view.message.get())
                self.assertFalse(self.view.dirty)
        self.prices.assert_not_called()

    def test_invalid_holdings_surface_an_error_without_background_valuation(self):
        self.app.manual_table.set_rows([{'currency': 'HUF', 'amount': '1.001'}])
        self.view.refresh()
        self.assertFalse(self.app.busy)
        self.assertEqual(self.view.portfolio_value.get(), '—')
        self.assertTrue(self.view.message.get())
        self.prices.assert_not_called()

    def test_corrupt_calculator_file_preserves_actual_value_and_blocks_recommendations(self):
        path = self.directory / 'calculator.json'
        corrupt = b'{broken calculator json}\n'
        path.write_bytes(corrupt)
        self.view.load(self.directory)
        before = self.csv_bytes()
        self.visit()
        self.assertIn('Cannot load calculator settings', self.view.message.get())
        self.assertEqual(self.view.portfolio_value.get(), '160')
        self.assertFalse(self.view.allocation_summary.get_children())
        self.assertFalse(self.view.dirty)
        self.assertEqual(path.read_bytes(), corrupt)
        self.prices.assert_not_called()
        self.configure_calculator()
        self.set_monthly_cost('1000')
        self.assertNotIn('Cannot load calculator settings', self.view.message.get())
        self.assertTrue(self.view.dirty)
        self.assertEqual(path.read_bytes(), corrupt)
        self.assertTrue(self.view.save())
        self.assertEqual(load_settings(self.directory)[0].monthly_spending(), 1000)
        self.assertEqual(self.csv_bytes(), before)
        self.assertFalse(self.app.dirty)

    def test_target_pie_switches_current_reserves_and_long_term_without_editing_targets(self):
        self.configure_calculator()
        self.set_monthly_cost('1000')
        self.set_single_rows([{'name': 'Upcoming bill', 'amount': '6000', 'currency': 'HUF',
                                     'due': (date.today() + timedelta(days=40)).isoformat()}])
        self.view._changed()
        portfolio = self.app.portfolio()
        targets = self.app.target_table.get_rows()
        revision = self.app.revision
        funding = prepare_allocation(portfolio, self.market, self.view.planning_settings(), date.today())
        expected = funding.portfolio.allocation.weights
        self.app.target_basis.set('Whole portfolio')
        for branch, weights in expected.items():
            self.assertAlmostEqual(self.app.target_chart._fractions.get(branch, 0), sum(weights.values()) / 100)
        self.assertNotIn('HUF', portfolio.allocation.weights[CASH])
        self.assertGreater(expected[CASH]['HUF'], 0)
        mak = self.app.allocation_pages[TREASURY]
        mak.target_basis.set('Whole portfolio')
        self.assertAlmostEqual(mak.target_chart._data['HUF'],
                               expected[TREASURY]['HUF'] / sum(expected[TREASURY].values()) * 100)
        mak.target_basis.set('Long-term')
        self.app.target_basis.set('Long-term')
        self.assertAlmostEqual(self.app.target_chart._fractions[STOCK_MARKET], .9)
        self.assertAlmostEqual(self.app.target_chart._fractions[TREASURY], .1)
        self.assertEqual(self.app.target_table.get_rows(), targets)
        self.assertEqual(self.app.portfolio(), portfolio)
        self.assertEqual(self.app.revision, revision)

    def test_calculator_edits_invalidate_plan_without_changing_portfolio_values_or_csv_files(self):
        before, portfolio, revision = self.csv_bytes(), self.app.portfolio(), self.app.revision
        self.app.plan = object()
        self.app.plan_report = 'Existing plan'
        self.configure_calculator()
        self.set_monthly_cost('1000')
        self.set_income('12.5')
        self.view._inputs['cash_months'].set('4')
        self.set_single_rows([{'name': 'Expense', 'amount': '5000', 'currency': 'HUF', 'due': '2030-01-01'}])
        self.view.refresh()
        self.assertTrue(self.view.dirty)
        self.assertFalse(self.app.dirty)
        self.root.after(550, self.root.quit)
        self.root.mainloop()
        settings, _ = load_settings(self.directory)
        self.assertEqual(settings.monthly_spending(), 1000)
        self.assertEqual(settings.incomes[0].amount, 12.5)
        self.assertEqual(settings.incomes[0].currency, 'EUR')
        self.assertEqual(settings.cash_months, 4)
        self.assertEqual(settings.monthly_totals(date.today().year, date.today().month, self.app._calculator_rates()).recurring_income - settings.monthly_spending(self.app._calculator_rates()), 4000)
        self.assertEqual(settings.outgoings[-1].amount, 5000)
        self.assertFalse(self.view.dirty)
        self.assertEqual(self.csv_bytes(), before)
        self.assertEqual(self.app.portfolio(), portfolio)
        self.assertGreater(self.app.revision, revision)
        self.assertIsNone(self.app.plan)
        self.assertEqual(self.app.plan_report, '')

    def test_generate_plan_combines_unsaved_reserves_with_actual_portfolio_wealth(self):
        before, portfolio = self.csv_bytes(), self.app.portfolio()
        self.configure_calculator()
        self.set_monthly_cost('1000')
        self.set_income('12.5')
        self.app.generate_plan()
        self.finish_work()
        self.errors.assert_not_called()
        plan = self.app.plan
        self.assertIsNotNone(plan)
        self.assertAlmostEqual(plan.wealth / self.market.rates['HUF'], 64_000)
        self.assertGreaterEqual(plan.cash['HUF'], 2000 - 1e-5)
        mak_huf = sum(holding.amount for holding in plan.final_manual_holdings if holding.currency == 'HUF')
        self.assertGreaterEqual(mak_huf, 6000 - 1e-5)
        self.assertIn('Protected reserves: Cash 2\u202f000 HUF · MÁK 6\u202f000 HUF', self.app.plan_report)
        self.assertEqual(self.view.portfolio_value.get(), '160')
        self.assertEqual(self.app.portfolio(), portfolio)
        self.assertEqual(self.csv_bytes(), before)
        self.assertFalse((self.directory / 'calculator.json').exists())

    def test_near_term_expense_is_protected_cash_before_future_income_arrives(self):
        before, portfolio = self.csv_bytes(), self.app.portfolio()
        self.configure_calculator()
        self.set_monthly_cost('1000')
        self.set_income('25')
        self.set_single_rows([{'name': 'Upcoming bill', 'amount': '6000', 'currency': 'HUF',
                                     'due': (date.today() + timedelta(days=40)).isoformat()}])
        self.view._changed()
        expense = next(value for value in self.view._values.values() if value['purpose'] == 'Upcoming bill')
        self.assertEqual((expense['cash'], expense['mak'], expense['stock']), (6000, 0, 0))
        self.assertEqual(expense['income'], 0)
        self.app.generate_plan()
        self.finish_work()
        self.errors.assert_not_called()
        self.assertGreaterEqual(self.app.plan.cash['HUF'], 8000 - 1e-5)
        mak = sum(holding.amount for holding in self.app.plan.final_manual_holdings if holding.currency == 'HUF')
        self.assertGreaterEqual(mak, 6000 - 1e-5)
        self.assertIn('Protected reserves: Cash 8\u202f000 HUF · MÁK 6\u202f000 HUF', self.app.plan_report)
        self.assertEqual(self.app.portfolio(), portfolio)
        self.assertEqual(self.csv_bytes(), before)

    def test_longer_cash_buffer_also_moves_expense_funding_deadline_earlier(self):
        self.configure_calculator()
        self.set_monthly_cost('1000')
        self.set_income('25')
        self.view._inputs['cash_months'].set('4')
        self.set_single_rows([{'name': 'Later bill', 'amount': '6000', 'currency': 'HUF',
                                     'due': (date.today() + timedelta(days=100)).isoformat()}])
        self.view._changed()
        expense = next(value for value in self.view._values.values() if value['purpose'] == 'Later bill')
        self.assertEqual((expense['cash'], expense['income']), (6000, 0))
        self.app.generate_plan()
        self.finish_work()
        self.errors.assert_not_called()
        self.assertGreaterEqual(self.app.plan.cash['HUF'], 10_000 - 1e-5)
        mak = sum(holding.amount for holding in self.app.plan.final_manual_holdings if holding.currency == 'HUF')
        self.assertGreaterEqual(mak, 6000 - 1e-5)
        self.assertIn('Protected reserves: Cash 10\u202f000 HUF · MÁK 6\u202f000 HUF', self.app.plan_report)

    def test_startup_loads_mnb_on_portfolio_and_generate_reuses_it_without_opening_calculator(self):
        self.assertFalse(self.view.needs_base_rate)
        self.base_rate.assert_called_once_with(cache=self.app.market_cache)
        self.assertFalse(self.view.dirty)
        self.assertEqual(self.view.portfolio_value.get(), '160')
        self.assertGreater(self.app.next_base_rate_refresh, time.monotonic() + 890)
        self.assertLessEqual(self.app.next_base_rate_refresh, time.monotonic() + 900)
        self.assertEqual(self.app.notebook.select(), str(self.app.portfolio_page))
        self.set_monthly_cost('1000')
        self.app.generate_plan()
        self.finish_work()
        self.base_rate.assert_called_once_with(cache=self.app.market_cache)
        self.errors.assert_not_called()
        self.assertFalse(self.view.needs_base_rate)
        self.assertIsNotNone(self.app.plan)
        self.assertGreaterEqual(self.app.plan.cash['HUF'], 2000 - 1e-5)
        self.assertEqual(self.app.notebook.select(), str(self.app.plan_page))

    def test_generate_plan_stops_when_automatic_mnb_rate_cannot_be_fetched(self):
        before = self.csv_bytes()
        self.base_rate.reset_mock()
        self.base_rate.side_effect = ValueError('MNB offline')
        self.reopen()
        self.assertTrue(self.view.needs_base_rate)
        self.base_rate.assert_called_once_with(cache=self.app.market_cache)
        self.set_monthly_cost('1000')
        self.app.generate_plan()
        self.finish_work()
        self.assertEqual(self.base_rate.call_count, 2)
        self.assertIsNone(self.app.plan)
        self.assertEqual(self.app.plan_report, '')
        self.assertIn('MNB offline', self.view.message.get())
        self.assertEqual(self.csv_bytes(), before)

    def test_generate_plan_labels_cached_mnb_rate_when_a_refresh_fails(self):
        self.base_rate.reset_mock()
        self.view.set_base_rate(5.5, 'effective 2026-09-01')
        self.app.next_base_rate_refresh = 0
        self.base_rate.side_effect = ValueError('MNB offline')
        self.set_monthly_cost('1000')
        self.app.generate_plan()
        self.finish_work()
        self.base_rate.assert_called_once_with(cache=self.app.market_cache)
        self.errors.assert_not_called()
        self.assertIsNotNone(self.app.plan)
        self.assertIn('5.5%', self.app.plan_report)
        self.assertIn('cached', self.app.plan_report)
        self.assertGreaterEqual(self.app.plan.cash['HUF'], 2000 - 1e-5)

    def test_foreign_currency_inputs_refresh_missing_fx_and_plan_huf_reserves(self):
        before, portfolio = self.csv_bytes(), self.app.portfolio()
        self.configure_calculator()
        self.view._inputs['mak'].set('0')
        self.set_monthly_cost('10.5', 'EUR')
        self.view._set_inputs(replace(self.view.settings(), incomes=(CashFlow('Salary', 100.25, date.today(), 'USD', 'monthly'),)))
        self.set_single_rows([{'name': 'Expense', 'amount': '20.75', 'currency': 'GBP',
                                     'due': date.today().isoformat()}])
        self.view._changed()
        self.assertIn('Missing exchange rate', self.view.message.get())
        self.assertTrue(self.app._prices_needed(self.app.portfolio()))
        self.prices.return_value = MarketData(self.market.quotes, {'HUF': .0025, 'USD': .9, 'GBP': 1.2})
        self.app.mode.set('Allowed')
        self.app.generate_plan()
        self.finish_work()
        self.errors.assert_not_called()
        self.prices.assert_called_once()
        self.assertEqual(set(self.prices.call_args.kwargs['extra_currencies']), {'HUF', 'EUR', 'USD', 'GBP'})
        self.assertIsNotNone(self.app.plan)
        self.assertGreaterEqual(self.app.plan.cash['HUF'], 18_360 - 1e-5)
        mak = sum(holding.amount for holding in self.app.plan.final_manual_holdings if holding.currency == 'HUF')
        self.assertGreaterEqual(mak, 25_200 - 1e-5)
        self.assertEqual(self.view.monthly_summary.set('Net', 'recurring'), '80')
        self.assertEqual(self.app.portfolio(), portfolio)
        self.assertEqual(self.csv_bytes(), before)

    def test_close_flushes_pending_calculator_changes_without_asking_to_save(self):
        self.configure_calculator()
        before = self.csv_bytes()
        self.set_monthly_cost('1200')
        with patch('ui.app.messagebox.askyesnocancel') as confirm, \
                patch('ui.app.messagebox.askyesno') as discard, patch.object(self.app, 'save') as portfolio_save:
            self.app.close()
        confirm.assert_not_called()
        discard.assert_not_called()
        portfolio_save.assert_not_called()
        self.assertTrue(self.app.closed)
        self.assertTrue((self.directory / 'calculator.json').exists())
        self.assertEqual(load_settings(self.directory)[0].monthly_spending(), 1200)
        self.assertEqual(self.csv_bytes(), before)

    def test_close_preserves_failed_calculator_edits_unless_explicitly_discarded(self):
        self.set_income('invalid')
        with patch('ui.app.messagebox.askyesno', return_value=False) as discard:
            self.app.close()
        self.assertFalse(self.app.closed)
        self.assertTrue(self.view.dirty)
        self.assertIn('Cannot save calculator settings', discard.call_args.args[1])
        self.assertEqual(discard.call_args.kwargs['default'], 'no')
        with patch('ui.app.messagebox.askyesno', return_value=True):
            self.app.close()
        self.assertTrue(self.app.closed)
        self.assertFalse((self.directory / 'calculator.json').exists())

    def test_switching_directory_flushes_calculator_edits_before_discarding_portfolio_edits(self):
        self.visit()
        before = self.csv_bytes()
        self.set_income('500')
        self.app.cash_table.set_rows([{'currency': 'EUR', 'amount': '75'}])
        self.app._edited()
        other = self.directory / 'other'
        save_portfolio(replace(self.portfolio, cash={'EUR': 100}), other)
        with patch('ui.app.messagebox.askyesnocancel', return_value=False), \
                patch('ui.app.filedialog.askdirectory', return_value=str(other)):
            self.app.open_directory()
        self.finish_work()
        self.app._update_calculator()
        self.finish_work()
        self.assertEqual(self.app.directory, other)
        self.assertEqual(self.view.portfolio_value.get(), '210')
        self.assertEqual(self.app._calculator_total(), 84_000)
        self.assertFalse(self.view.dirty)
        self.assertEqual(load_settings(self.directory)[0].incomes[0].amount, 500)
        self.assertEqual(self.csv_bytes(), before)
        self.assertEqual(self.view.settings().incomes, ())
        self.assertFalse((other / 'calculator.json').exists())

    def test_automatic_mnb_failure_retry_and_refresh_preserve_manual_override(self):
        self.base_rate.reset_mock()
        self.base_rate.side_effect = [ValueError('MNB offline'), BaseRate(5.75, date(2026, 10, 1)),
                                      BaseRate(5.5, date(2026, 10, 15))]
        with patch('ui.app.monotonic', return_value=100):
            self.app.next_base_rate_refresh = 100
            self.app._update_calculator()
            self.finish_work()
            self.assertEqual(self.app.next_base_rate_refresh, 1000)
        self.assertIn('MNB offline', self.view.message.get())
        self.assertIn('cached', self.view.rate_description)
        self.errors.assert_not_called()
        self.assertIsNone(self.view.settings().returns.mak)
        with patch('ui.app.monotonic', return_value=999):
            self.app._update_calculator()
            self.base_rate.assert_called_once_with(cache=self.app.market_cache)
        with patch('ui.app.monotonic', return_value=1000):
            self.app._update_calculator()
            self.finish_work()
        self.assertEqual(float(self.view._inputs['mak'].get()), 5.75)
        self.assertIn('effective 2026-10-01', self.view.rate_description)
        self.assertTrue(self.view.allocation_summary.get_children())
        self.view._auto_rate.set(False)
        self.view._inputs['mak'].set('4.25')
        self.assertTrue(self.view.save())
        with patch('ui.app.monotonic', return_value=1900):
            self.app._update_calculator()
            self.finish_work()
        self.assertEqual(self.view.settings().returns.mak, 4.25)
        self.assertFalse(self.view.dirty)
        self.assertFalse(self.app.dirty)
        self.assertIn('5.75%', self.view.rate_description)
        self.assertEqual(self.base_rate.call_count, 2)
        self.assertEqual(self.app.notebook.select(), str(self.app.portfolio_page))

    def test_calculator_controls_fit_the_complete_application_at_minimum_size(self):
        self.root.deiconify()
        self.root.geometry('1050x760')
        self.visit()
        self.app.cash_table.set_rows([{'currency': 'HUF', 'amount': '80000000'}])
        self.app._edited()
        self.set_income('1000000')
        self.set_monthly_cost('400000')
        self.root.update()
        self.assertEqual(self.app.notebook.index(self.view), 1)
        self.assertIn('2026-09-01', self.view.rate_description)
        self.assertEqual(self.view._auto_rate_box.cget('text'), 'Use MNB base rate')
        right = self.root.winfo_rootx() + self.root.winfo_width()
        bottom = self.root.winfo_rooty() + self.root.winfo_height()
        for widget in (*self.view._entries, self.app.base_entry, self.view._auto_rate_box,
                       self.view._message_label, self.app.cancel_button, *self.view.monthly_summary.master.winfo_children(),
                       *self.view.allocation_summary.master.winfo_children(),
                       *self.view._summary_panel.winfo_children()):
            if not widget.winfo_ismapped():
                continue
            with self.subTest(widget=str(widget)):
                self.assertGreaterEqual(widget.winfo_rootx(), self.root.winfo_rootx())
                self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), right)
                self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), bottom)
        summary = self.view._summary_panel
        self.assertFalse(hasattr(self.view, 'projection_button'))
        self.assertIs(self.app.projection_button.master.master, self.app.summary_page)
        for widget in summary.winfo_children():
            self.assertLessEqual(widget.winfo_y() + widget.winfo_height(), summary.winfo_height())
        monthly = self.view.monthly_summary.master
        self.assertLessEqual(self.view.monthly_summary.winfo_y() + self.view.monthly_summary.winfo_height(), monthly.winfo_height())
        net = self.view.monthly_summary.bbox('Net')
        self.assertTrue(net, 'All three monthly cash-flow rows must remain visible.')
        self.assertLessEqual(net[1] + net[3], self.view.monthly_summary.winfo_height())
        self.assertLessEqual(abs(monthly.winfo_height() - self.view._funding_panel.winfo_height()), 1)


if __name__ == '__main__':
    unittest.main()
