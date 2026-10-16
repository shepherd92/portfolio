"""Automatic market-data refresh and planning use no external network in tests."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from portfolio.models import AllocationTargets, CASH, STOCK_MARKET, TREASURY, Holding, MarketData, Portfolio, Quote
from portfolio.storage import save_portfolio
from portfolio.market import BaseRate
from ui.app import PortfolioApp


@unittest.skipUnless(os.environ.get('REBALANCE_GUI_TESTS') == '1', 'GUI tests require a display and REBALANCE_GUI_TESTS=1')
class AutomaticPricesTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.market = MarketData({'MAIN': Quote(10, 'EUR')}, {'HUF': .0025})
        portfolio = Portfolio((Holding('MAIN', 'Stocks', 2),),
                              AllocationTargets({STOCK_MARKET: {'Stocks': 100}, TREASURY: {}, CASH: {'EUR': 0}},
                                                {'Stocks': 'Equities'}),
                              {'EUR': 10})
        save_portfolio(portfolio, directory.name)
        rate = patch('ui.app.fetch_base_rate', return_value=BaseRate(5, date(2026, 1, 1)))
        self.rate = rate.start()
        self.addCleanup(rate.stop)
        fetch = patch('ui.app.fetch_market_data', return_value=self.market)
        errors = patch('ui.app.messagebox.showerror')
        self.fetch, self.errors = fetch.start(), errors.start()
        self.addCleanup(fetch.stop)
        self.addCleanup(errors.stop)
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = PortfolioApp(self.root, Path(directory.name))
        self.addCleanup(self.close)
        self.finish_worker()
        self.fetch.reset_mock()
        self.rate.reset_mock()

    def close(self):
        self.app.dirty = self.app.calculator.dirty = False
        if not self.app.closed:
            self.app.close()

    def pump_until(self, condition):
        deadline = time.monotonic() + 5
        while not condition() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.005)
        self.assertTrue(condition(), 'Background work did not finish')

    def finish_worker(self):
        self.pump_until(lambda: not self.app.busy)
        self.root.update()

    def test_freshness_checks_age_base_and_required_quotes_and_rates(self):
        now = datetime.now(timezone.utc)
        portfolio = self.app.portfolio()
        fresh = replace(self.market, fetched_at=now)
        cases = [
            (fresh, portfolio, False),
            (None, portfolio, True),
            (replace(fresh, fetched_at=now - timedelta(seconds=899)), portfolio, False),
            (replace(fresh, fetched_at=now - timedelta(seconds=900)), portfolio, True),
            (MarketData({}, {}), portfolio, True),
            (MarketData(fresh.quotes, {'EUR': .9}, 'USD'), portfolio, True),
            (replace(fresh, rates={}), replace(portfolio, cash={'EUR': 10, 'HUF': 1000}), True),
        ]
        with patch('ui.app.datetime') as clock:
            clock.now.return_value = now
            for market, inputs, expected in cases:
                with self.subTest(market=market, cash=inputs.cash):
                    self.app.market = market
                    self.assertEqual(self.app._prices_needed(inputs), expected)
        self.fetch.assert_not_called()

    def test_generate_fetches_missing_or_stale_data_then_plans(self):
        portfolio = self.app.portfolio()
        for market in (None, replace(self.market, fetched_at=datetime.now(timezone.utc) - timedelta(minutes=16))):
            with self.subTest(missing=market is None):
                self.app.market = market
                self.fetch.reset_mock()
                self.app.generate_plan()
                self.finish_worker()
                self.fetch.assert_called_once_with(portfolio, 'EUR', extra_currencies=('HUF',))
                self.assertIs(self.app.market, self.market)
                self.assertIsNotNone(self.app.plan)
                self.assertIn('Plan ready', self.app.status.get())
                self.assertIsNone(self.app.timeout_id)
                self.assertEqual(self.app.portfolio(), portfolio)
        self.errors.assert_not_called()

    def test_generate_reuses_fresh_complete_prices(self):
        self.app.generate_plan()
        self.finish_worker()
        self.fetch.assert_not_called()
        self.assertIsNotNone(self.app.plan)
        self.errors.assert_not_called()

    def test_generate_fetches_missing_fx_and_preserves_entered_balances(self):
        self.app.cash_table.set_rows([{'currency': 'EUR', 'amount': '10'}, {'currency': 'HUF', 'amount': '4000'}])
        self.app._edited()
        portfolio = self.app.portfolio()
        self.app.market = replace(self.market, rates={})
        self.fetch.return_value = MarketData(self.market.quotes, {'HUF': .0025})
        self.app.generate_plan()
        self.finish_worker()
        self.fetch.assert_called_once_with(portfolio, 'EUR', extra_currencies=('HUF',))
        self.assertIsNotNone(self.app.plan)
        self.assertEqual(self.app.portfolio().cash, {'EUR': 10, 'HUF': 4000})
        self.errors.assert_not_called()

    def test_native_income_quotes_refresh_display_without_rewriting_saved_income(self):
        calculator = self.app.calculator
        native = replace(calculator.settings(), monthly_income=100.25, income_currency='USD')
        calculator._set_inputs(native)
        calculator._changed()
        self.assertTrue(calculator.save())
        original = (self.app.directory / 'calculator.json').read_bytes()
        self.assertTrue(self.app._prices_needed(self.app.portfolio()))
        self.fetch.return_value = replace(self.market, rates={'EUR': 1, 'HUF': .0025, 'USD': .9})
        with patch('ui.app.monotonic', return_value=100):
            self.app.next_price_refresh = 100
            self.app._refresh_due_prices()
            self.finish_worker()
        self.fetch.assert_called_once_with(self.app.portfolio(), 'EUR', extra_currencies=('HUF', 'USD'))
        self.assertAlmostEqual(float(calculator._inputs['monthly_income'].get()), 90.225)
        self.app._accept_market(replace(self.market, rates={'EUR': 1, 'HUF': .0025, 'USD': .95}))
        self.assertAlmostEqual(float(calculator._inputs['monthly_income'].get()), 95.2375)
        self.assertEqual(calculator.settings(), native)
        self.assertEqual((self.app.directory / 'calculator.json').read_bytes(), original)
        self.assertFalse(calculator.dirty)
        self.errors.assert_not_called()

    def test_failed_required_fetch_never_plans_with_stale_fallback(self):
        stale = replace(self.market, fetched_at=datetime.now(timezone.utc) - timedelta(minutes=16))
        self.app.market = stale
        self.fetch.side_effect = ValueError('Network unavailable')
        with patch.object(self.app, '_calculate_plan') as calculate:
            self.app.generate_plan()
            self.finish_worker()
        calculate.assert_not_called()
        self.assertIsNone(self.app.plan)
        self.assertIs(self.app.market, stale)
        self.assertIn('Network unavailable', self.app.status.get())
        self.errors.assert_called_once()

    def test_incomplete_or_stale_fetch_response_never_continues_planning(self):
        for market in (MarketData({}, {}),
                       replace(self.market, fetched_at=datetime.now(timezone.utc) - timedelta(minutes=16))):
            with self.subTest(market=market), patch.object(self.app, '_calculate_plan') as calculate:
                self.app.market = None
                self.fetch.return_value = market
                self.errors.reset_mock()
                self.app.generate_plan()
                self.finish_worker()
                calculate.assert_not_called()
                self.assertIsNone(self.app.market)
                self.assertIsNone(self.app.plan)
                self.errors.assert_called_once()

    def test_idle_due_refresh_defers_while_busy_or_modal(self):
        self.app.market = replace(self.market, fetched_at=datetime.now(timezone.utc) - timedelta(minutes=16))
        dialog = tk.Toplevel(self.root)
        dialog.update()
        dialog.grab_set()
        try:
            with patch('ui.app.monotonic', return_value=100):
                self.app.next_price_refresh = 100
                self.app.busy = True
                self.app._refresh_due_prices()
                self.app.busy = False
                self.app._refresh_due_prices()
                self.fetch.assert_not_called()
                self.assertEqual(self.app.next_price_refresh, 100)
                dialog.destroy()
                self.app._refresh_due_prices()
                self.finish_worker()
                self.fetch.assert_called_once()
                self.assertGreater(self.app.next_price_refresh, 900)
        finally:
            self.app.busy = False
            if dialog.winfo_exists():
                dialog.destroy()

    def test_idle_refresh_defers_for_tcl_dialog_without_python_widget(self):
        path = '.native_price_dialog'
        self.root.tk.call('toplevel', path)
        self.root.update()
        self.root.tk.call('grab', 'set', path)
        try:
            with patch('ui.app.monotonic', return_value=100):
                self.app.next_price_refresh = 100
                self.app.market = None
                self.app._refresh_due_prices()
                self.fetch.assert_not_called()
                self.assertEqual(self.app.next_price_refresh, 100)
        finally:
            self.root.tk.call('destroy', path)

    def test_unattended_failure_keeps_existing_plan_and_backs_off(self):
        self.app.generate_plan()
        self.finish_worker()
        plan = self.app.plan
        stale = replace(self.market, fetched_at=datetime.now(timezone.utc) - timedelta(minutes=16))
        self.app.market = stale
        self.fetch.side_effect = ValueError('Offline')
        with patch('ui.app.monotonic', return_value=100):
            self.app.next_price_refresh = 100
            self.app._refresh_due_prices()
            self.finish_worker()
            self.app._refresh_due_prices()
            self.assertGreaterEqual(self.app.next_price_refresh, 1000)
        self.fetch.assert_called_once()
        self.errors.assert_not_called()
        self.assertIs(self.app.market, stale)
        self.assertIs(self.app.plan, plan)
        self.assertIn('Offline', self.app.status.get())

    def test_successful_due_refresh_preserves_completed_plan_and_original_context(self):
        self.app.generate_plan()
        self.finish_worker()
        plan, report = self.app.plan, self.app.plan_report
        context, source = self.app.plan_view.status.get(), self.app.source.get()
        later = self.market.fetched_at + timedelta(minutes=16)
        updated = MarketData({'MAIN': Quote(20, 'EUR')}, {'HUF': .0025}, fetched_at=later)
        self.fetch.return_value = updated
        with patch('ui.app.datetime') as clock, patch('ui.app.monotonic', return_value=100):
            clock.now.return_value = later
            self.app.next_price_refresh = 100
            self.app._refresh_due_prices()
            self.finish_worker()
        self.fetch.assert_called_once_with(self.app.portfolio(), 'EUR', extra_currencies=('HUF',))
        self.assertIs(self.app.market, updated)
        self.assertNotEqual(self.app.source.get(), source)
        self.assertIn('50 EUR', self.app.current_title.get())
        self.assertEqual(self.app.calculator.portfolio_value.get(), '50')
        self.assertEqual(self.app._calculator_total(), 20_000)
        self.assertFalse(self.app.calculator.dirty)
        self.assertIs(self.app.plan, plan)
        self.assertEqual(self.app.plan_report, report)
        self.assertEqual(self.app.plan_view.status.get(), context)
        self.assertIn(source, context)
        self.assertTrue(self.app.export_button.instate(['!disabled']))
        self.errors.assert_not_called()

    def test_invalid_draft_defers_idle_refresh_without_error_popup(self):
        self.app.target_table.set_rows([{'parent': STOCK_MARKET, 'group': 'Stocks', 'target': '99'},
                                       {'parent': CASH, 'group': 'EUR', 'target': '0'}])
        with patch('ui.app.monotonic', return_value=100):
            self.app.next_price_refresh = 100
            self.app._refresh_due_prices()
            self.assertEqual(self.app.next_price_refresh, 130)
            self.app._refresh_due_prices()
        self.fetch.assert_not_called()
        self.errors.assert_not_called()
        self.assertFalse(self.app.busy)

    def test_new_held_ticker_schedules_deferred_refresh(self):
        self.app.holdings_table.set_rows([{'ticker': 'MAIN', 'group': 'Stocks', 'shares': '2'},
                                         {'ticker': 'NEW', 'group': 'Stocks', 'shares': '1'}])
        self.fetch.return_value = MarketData({**self.market.quotes, 'NEW': Quote(5, 'EUR')}, {'HUF': .0025})
        with patch('ui.app.monotonic', return_value=100):
            self.app._edited()
            self.assertEqual(self.app.next_price_refresh, 100.5)
            self.app._refresh_due_prices()
            self.fetch.assert_not_called()
        with patch('ui.app.monotonic', return_value=100.5):
            self.app._refresh_due_prices()
            self.finish_worker()
        self.fetch.assert_called_once_with(self.app.portfolio(), 'EUR', extra_currencies=('HUF',))
        self.assertEqual(self.app.market.price('NEW'), 5)
        self.errors.assert_not_called()

    def test_wrong_base_response_is_rejected_before_continuation(self):
        self.fetch.return_value = MarketData(self.market.quotes, {'EUR': .9}, 'USD')
        ready = Mock()
        self.app.refresh_prices(on_ready=ready)
        self.finish_worker()
        ready.assert_not_called()
        self.assertIs(self.app.market, self.market)
        self.errors.assert_called_once()

    def test_base_rate_refreshes_every_fifteen_minutes_without_opening_calculator(self):
        for index, page in enumerate((self.app.portfolio_page, self.app.plan_page)):
            with self.subTest(page=str(page)):
                self.app.notebook.select(page)
                self.app.next_base_rate_refresh = 1000 + index * 900
                with patch('ui.app.monotonic', return_value=999 + index * 900):
                    self.app._update_calculator()
                    self.rate.assert_not_called()
                with patch('ui.app.monotonic', return_value=1000 + index * 900):
                    self.app._update_calculator()
                    self.finish_worker()
                    self.rate.assert_called_once_with()
                    self.assertEqual(self.app.next_base_rate_refresh, 1900 + index * 900)
                    self.app._update_calculator()
                    self.rate.assert_called_once_with()
                self.rate.reset_mock()
        self.errors.assert_not_called()

    def test_due_base_rate_refresh_waits_until_busy_and_modal_work_finishes(self):
        dialog = tk.Toplevel(self.root)
        dialog.update()
        dialog.grab_set()
        try:
            with patch('ui.app.monotonic', return_value=100):
                self.app.next_base_rate_refresh = 100
                self.app.busy = True
                self.app._update_calculator()
                self.app.busy = False
                self.app._update_calculator()
                self.rate.assert_not_called()
                self.assertEqual(self.app.next_base_rate_refresh, 100)
                dialog.destroy()
                self.app._update_calculator()
                self.finish_worker()
                self.rate.assert_called_once_with()
                self.assertEqual(self.app.next_base_rate_refresh, 1000)
        finally:
            self.app.busy = False
            if dialog.winfo_exists():
                dialog.destroy()

    def test_failed_base_rate_refresh_keeps_cached_rate_plan_and_backs_off(self):
        self.app.generate_plan()
        self.finish_worker()
        plan, report, context = self.app.plan, self.app.plan_report, self.app.plan_view.status.get()
        self.rate.side_effect = ValueError('MNB offline')
        with patch('ui.app.monotonic', return_value=100):
            self.app.next_base_rate_refresh = 100
            self.app._update_calculator()
            self.finish_worker()
            self.app._update_calculator()
            self.assertEqual(self.app.next_base_rate_refresh, 1000)
        self.rate.assert_called_once_with()
        self.errors.assert_not_called()
        self.assertEqual(self.app.calculator.planning_settings().returns.mak, 5)
        self.assertIn('cached', self.app.calculator.rate_description)
        self.assertIs(self.app.plan, plan)
        self.assertEqual(self.app.plan_report, report)
        self.assertEqual(self.app.plan_view.status.get(), context)
        self.assertTrue(self.app.export_button.instate(['!disabled']))

    def test_successful_base_rate_refresh_keeps_completed_plan_and_original_rate_context(self):
        self.app.generate_plan()
        self.finish_worker()
        plan, report, context = self.app.plan, self.app.plan_report, self.app.plan_view.status.get()
        self.assertIn('5%', report)
        self.rate.return_value = BaseRate(6.25, date(2026, 2, 1))
        with patch('ui.app.monotonic', return_value=100):
            self.app.next_base_rate_refresh = 100
            self.app._update_calculator()
            self.finish_worker()
        self.rate.assert_called_once_with()
        self.assertEqual(self.app.calculator.planning_settings().returns.mak, 6.25)
        self.assertIn('6.25%', self.app.calculator.rate_description)
        self.assertIs(self.app.plan, plan)
        self.assertEqual(self.app.plan_report, report)
        self.assertEqual(self.app.plan_view.status.get(), context)
        self.assertTrue(self.app.export_button.instate(['!disabled']))
        self.assertFalse(self.app.calculator.dirty)
        self.errors.assert_not_called()

    def test_canceling_fetch_prevents_chained_plan_and_discards_late_prices(self):
        gate, completed = threading.Event(), threading.Event()
        self.addCleanup(gate.set)
        def fetch(*_, **kwargs):
            gate.wait(3)
            completed.set()
            return self.market
        self.fetch.side_effect = fetch
        self.app.calculator.set_base_rate(5, 'effective 2026-01-01')
        self.app.next_base_rate_refresh = time.monotonic() + 86400
        self.app.market = None
        with patch.object(self.app, '_calculate_plan') as calculate:
            self.app.generate_plan()
            self.assertTrue(self.app.busy)
            self.app.cancel_work()
            self.assertFalse(self.app.busy)
            gate.set()
            self.assertTrue(completed.wait(2))
            deadline = time.monotonic() + 2
            while self.app.events.empty() and time.monotonic() < deadline:
                time.sleep(.005)
            self.assertFalse(self.app.events.empty())
            self.pump_until(self.app.events.empty)
            calculate.assert_not_called()
        self.assertIsNone(self.app.market)
        self.assertIsNone(self.app.plan)
        self.assertIsNone(self.app.timeout_id)
        self.errors.assert_not_called()


if __name__ == '__main__':
    unittest.main()
