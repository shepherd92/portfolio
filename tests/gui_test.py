"""Real Tk widget tests. Run with REBALANCE_GUI_TESTS=1 under a display/Xvfb."""

import csv
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
from tkinter import font, ttk
import unittest
from unittest.mock import patch

from ui.app import MODES, PortfolioApp
from ui.widgets import PieChart
from portfolio.models import AllocationTargets, AssetDetails, CASH, STOCK_MARKET, TREASURY, Holding, ManualHolding, MarketData, Portfolio, Quote
from portfolio.storage import load_portfolio, save_portfolio


@unittest.skipUnless(os.environ.get('REBALANCE_GUI_TESTS') == '1', 'GUI tests require REBALANCE_GUI_TESTS=1 and a display')
class GuiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        allocation = AllocationTargets(
            {STOCK_MARKET: {'Stocks': 50, 'Bonds': 50}, TREASURY: {'EUR': 0, 'HUF': 0}, CASH: {'EUR': 0}})
        self.portfolio = Portfolio((Holding('MAIN', 'Stocks', 3), Holding('OLD', 'Stocks', 2, False),
                                    Holding('BOND', 'Bonds', 5)), allocation, {'EUR': 20},
                                   (ManualHolding(0, 'EUR'),))
        save_portfolio(self.portfolio, self.directory)
        self.portfolio = load_portfolio(self.directory)
        self.market = MarketData({ticker: Quote(price, 'EUR') for ticker, price in
                                     {'MAIN': 10, 'OLD': 5, 'BOND': 10, 'NEW': 20}.items()}, {})
        self.root = tk.Tk()
        self.root.withdraw()
        self.errors = patch('ui.app.messagebox.showerror').start()
        self.addCleanup(patch.stopall)
        patch('ui.app.fetch_market_data', return_value=self.market).start()
        patch('ui.app.fetch_asset_details', return_value=AssetDetails()).start()
        self.app = PortfolioApp(self.root, self.directory)
        self.addCleanup(self.cleanup_root)
        self.finish_worker()
        self.loaded_portfolio = self.app.portfolio()

    def cleanup_root(self):
        self.app.dirty = False
        if not self.app.closed:
            self.app.close()

    def finish_worker(self):
        deadline = time.monotonic() + 10
        while self.app.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertFalse(self.app.busy, 'Background work did not finish')
        self.root.update()

    def change_class(self, old, new):
        normalized = self.app._validate_class(new, old)
        rows = self.app.class_table.get_rows()
        rows[rows.index(old)] = normalized
        self.app.class_table.set_rows(rows)
        self.app._classes_changed('edit', old, normalized)

    def set_targets(self, *, stock=None, treasury=None, cash=None):
        changes = {STOCK_MARKET: stock, TREASURY: treasury, CASH: cash}
        rows = self.app.target_table.get_rows()
        for parent, values in changes.items():
            if values is not None:
                rows = [row for row in rows if row['parent'] != parent]
                rows += [{'parent': parent, 'group': name, 'target': str(value)}
                         for name, value in values.items()]
        self.app.target_table.set_rows(rows)
        self.app._edited()

    def reopen_app(self):
        self.app.dirty = False
        self.app.close()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = PortfolioApp(self.root, self.directory)

    def test_startup_refreshes_once_in_background_without_changing_inputs(self):
        before = {path: path.read_bytes() for path in self.directory.glob('*.csv')}
        worker_threads = []
        def fetch(portfolio, base):
            worker_threads.append(threading.get_ident())
            self.assertEqual(portfolio, self.portfolio)
            self.assertEqual(base, 'EUR')
            return self.market
        with patch('ui.app.fetch_market_data', side_effect=fetch) as request:
            self.reopen_app()
            self.assertTrue(self.app.busy)
            self.finish_worker()
            self.root.update()
            request.assert_called_once()
        self.assertNotEqual(worker_threads, [threading.get_ident()])
        self.assertEqual(self.app.market, self.market)
        self.assertIn('110 EUR', self.app.current_title.get())
        self.assertFalse(self.app.dirty)
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_failed_startup_refresh_leaves_app_available_for_retry(self):
        with patch('ui.app.fetch_market_data', side_effect=[ValueError('Network unavailable'), self.market]) as request:
            self.reopen_app()
            self.finish_worker()
            self.assertIsNone(self.app.market)
            self.assertTrue(self.app.holdings_table._enabled)
            self.assertEqual(str(self.app.generate_button.cget('state')), 'normal')
            self.assertIn('Network unavailable', self.app.status.get())
            self.app.generate_plan()
            self.finish_worker()
            self.assertEqual(request.call_count, 2)
            self.assertIsNotNone(self.app.plan)
        self.assertEqual(self.app.market, self.market)
        self.errors.assert_not_called()

    def test_close_during_automatic_refresh_does_not_touch_destroyed_widgets(self):
        gate, completed = threading.Event(), threading.Event()
        self.addCleanup(gate.set)
        def fetch(*_):
            gate.wait(2)
            completed.set()
            return self.market
        with patch('ui.app.fetch_market_data', side_effect=fetch):
            self.reopen_app()
            self.assertTrue(self.app.busy)
            self.app.close()
            gate.set()
            self.assertTrue(completed.wait(2))
        self.assertTrue(self.app.closed)
        self.assertIsNone(self.app.market)

    def test_startup_values_and_matching_chart_colors(self):
        self.assertEqual(dict(self.app.portfolio().allocation.totals), {STOCK_MARKET: 100, TREASURY: 0, CASH: 0})
        self.assertEqual(self.app.current_chart._data, {STOCK_MARKET: 90, CASH: 20})
        self.assertEqual(self.app.target_chart._data, {STOCK_MARKET: 100, CASH: 0, TREASURY: 0})
        self.assertEqual(self.app.current_chart._colors, self.app.target_chart._colors)
        self.assertIn('110 EUR', self.app.current_title.get())

    def test_load_edit_and_save_preserve_numeric_precision(self):
        allocation = AllocationTargets(
            {STOCK_MARKET: {name: 100 / 3 for name in ('A', 'B', 'C')}, TREASURY: {}, CASH: {'EUR': 0}})
        portfolio = Portfolio(tuple(Holding(name, name, 1) for name in ('A', 'B', 'C')),
                              allocation, {'EUR': 123.12345678912345})
        save_portfolio(portfolio, self.directory)
        portfolio = load_portfolio(self.directory)
        self.app._load_directory(self.directory)
        self.assertEqual(self.app.portfolio().cash, portfolio.cash)
        self.assertEqual(self.app.portfolio().holdings, portfolio.holdings)
        expected = self.app.portfolio()
        table = self.app.cash_table
        item = table.tree.get_children()[0]
        self.assertEqual(table.tree.set(item, 'amount'), '123')
        self.edit_money_row(table, None, 'EUR', item=item)
        self.assertEqual(self.app.portfolio(), expected)
        for row in self.app.cash_table.get_rows():
            self.assertEqual(self.app._validate_cash(row, row), row)
        self.assertTrue(self.app.save())
        self.assertEqual(load_portfolio(self.directory), expected)

    def test_repeated_group_rename_and_save_preserve_metadata(self):
        path = self.directory / 'asset_group.csv'
        with path.open(newline='') as stream:
            rows = list(csv.DictReader(stream))
        for row in rows:
            row['note'] = 'Retirement' if row['asset_group'] == 'Stocks' else 'Income'
        with path.open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=[*rows[0]])
            writer.writeheader()
            writer.writerows(rows)
        self.app._load_directory(self.directory)
        for name in ('Equities', 'Shares', 'Stocks'):
            old = next(row for row in self.app.class_table.get_rows() if row['main_ticker'] == 'MAIN')
            self.change_class(old, {'group': name, 'main_ticker': 'MAIN'})
            if name != 'Equities':
                self.assertTrue(self.app.save())
                with path.open() as stream:
                    notes = {row['asset_group']: row['note'] for row in csv.DictReader(stream)}
                self.assertEqual(notes[name], 'Retirement')

    def test_target_edit_immediately_updates_chart_and_invalidates_plan(self):
        self.app.generate_plan()
        self.finish_worker()
        self.assertIsNotNone(self.app.plan)
        self.set_targets(stock={'Bonds': 40, 'Stocks': 60})
        self.app.chart_scope.set(STOCK_MARKET)
        self.assertIsNone(self.app.plan)
        self.assertEqual(self.app.target_chart._data['Stocks'], 60)
        self.assertTrue(self.app.dirty)

    def test_invalid_target_total_does_not_appear_as_valid_pie(self):
        self.set_targets(stock={'Bonds': 40, 'Stocks': 40})
        self.assertEqual(self.app.target_chart._data, {})
        self.assertIn('100%', self.app.target_chart._empty_message)
        self.app.generate_plan()
        self.assertFalse(self.app.busy)
        self.errors.assert_called()

    def test_main_change_retains_every_existing_quantity(self):
        old = next(row for row in self.app.class_table.get_rows() if row['group'] == 'Stocks')
        self.change_class(old, {'group': 'Stocks', 'main_ticker': 'NEW'})
        holdings = {h.ticker: h for h in self.app.portfolio().holdings}
        self.assertEqual(holdings['MAIN'].shares, 3)
        self.assertEqual(holdings['OLD'].shares, 2)
        self.assertFalse(holdings['MAIN'].is_main)
        self.assertTrue(holdings['NEW'].is_main)
        self.assertEqual(holdings['NEW'].shares, 0)

    def test_class_rename_updates_targets_and_holdings(self):
        old = next(row for row in self.app.class_table.get_rows() if row['group'] == 'Stocks')
        self.change_class(old, {'group': 'Equities', 'main_ticker': 'MAIN'})
        portfolio = self.app.portfolio()
        self.assertEqual(portfolio.allocation.weights[STOCK_MARKET]['Equities'], 50)
        self.assertTrue(all(h.group == 'Equities' for h in portfolio.holdings if h.ticker in ('MAIN', 'OLD')))

    def test_class_cannot_steal_another_groups_ticker_or_delete_holdings(self):
        old = next(row for row in self.app.class_table.get_rows() if row['group'] == 'Stocks')
        with self.assertRaises(ValueError):
            self.app._validate_class({'group': 'Stocks', 'main_ticker': 'BOND'}, old)
        with self.assertRaises(ValueError):
            self.app._can_delete_class(old)
        with self.assertRaises(ValueError):
            self.app._can_delete_holding({'ticker': 'MAIN'})

    def test_all_modes_generate_plans_without_changing_inputs(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                self.app.mode.set(mode)
                self.app.generate_plan()
                self.finish_worker()
                self.assertIsNotNone(self.app.plan)
                self.assertTrue(self.app.plan_report.lower().startswith(f'Selling {mode}'.lower()))
                self.assertEqual(self.app.portfolio().holdings, self.portfolio.holdings)
                if mode == MODES[0]:
                    self.assertTrue(all(t.shares > 0 for t in self.app.plan.trades))
        self.errors.assert_not_called()

    def test_cash_deficit_is_outside_pie(self):
        self.app.cash_table.set_rows([{'currency': 'EUR', 'amount': '-15'}])
        self.app._edited()
        self.assertEqual(sum(self.app.current_chart._data.values()), 90)
        self.assertIn('75 EUR', self.app.current_title.get())
        self.assertTrue(self.app.cash_deficit_note.grid_info())
        self.app.chart_scope.set(STOCK_MARKET)
        self.assertFalse(self.app.cash_deficit_note.grid_info())
        self.assertEqual(self.app.current_title.get(), 'Current allocation · 90 EUR')
        self.assertNotIn('\n', self.app.current_title.get())

    def test_cash_target_funds_sales_and_aggregates_currencies(self):
        self.set_targets(stock={'Stocks': 0, 'Bonds': 0}, cash={'EUR': 50, 'USD': 50})
        self.app.cash_table.set_rows([{'currency': 'EUR', 'amount': '10'}, {'currency': 'USD', 'amount': '20'}])
        self.app._edited()
        self.app._accept_market(MarketData(self.market.quotes, {'USD': .5}))
        self.assertEqual(self.app.current_chart._data[CASH], 20)
        self.app.mode.set(MODES[1])
        self.app.generate_plan()
        self.finish_worker()
        self.assertIsNotNone(self.app.plan)
        self.assertAlmostEqual(self.app.plan.cash_value, 110)
        self.assertTrue(all(t.shares < 0 for t in self.app.plan.trades))
        self.assertEqual(self.app.portfolio().cash, {'EUR': 10, 'USD': 20})

    def test_zero_rows_sort_last_after_loading_and_edits(self):
        self.set_targets(stock={'Stocks': 80, 'Bonds': 0}, cash={'EUR': 20})
        expected = ['Stocks', 'Bonds']
        children = [row['group'] for row in self.app.target_table.get_rows() if row['parent'] == STOCK_MARKET]
        self.assertEqual(children, ['Stocks', 'Bonds'])
        table = self.app.class_table
        self.assertEqual([table.tree.set(item, 'group') for item in table.tree.get_children()], expected)
        self.app.holdings_table.set_rows([{'ticker': 'A', 'group': 'Stocks', 'shares': '0'},
                                         {'ticker': 'B', 'group': 'Stocks', 'shares': '3'}])
        table = self.app.holdings_table
        self.assertEqual([table.tree.set(item, 'ticker') for item in table.tree.get_children()], ['B', 'A'])
        self.app.cash_table.set_rows([{'currency': 'EUR', 'amount': '0'}, {'currency': 'HUF', 'amount': '-50'}])
        table = self.app.cash_table
        self.assertEqual([table.tree.set(item, 'currency') for item in table.tree.get_children()], ['HUF', 'EUR'])

    def test_asset_classes_only_define_stock_representatives(self):
        self.assertEqual(set(self.app._group_names()), {'Stocks', 'Bonds'})
        for group in (CASH, TREASURY, STOCK_MARKET):
            with self.subTest(group=group), self.assertRaisesRegex(ValueError, 'reserved'):
                self.app._validate_class({'group': group, 'main_ticker': 'BANK'}, None)
        self.assertEqual(self.app.portfolio().allocation.totals[CASH], 0)


    def test_background_fetch_updates_widgets_only_on_main_thread(self):
        main_thread = threading.get_ident()
        workers, completion = [], []
        self.app._run('Testing', lambda: workers.append(threading.get_ident()),
                      lambda result: completion.append(threading.get_ident()))
        self.assertFalse(self.app.holdings_table._enabled)
        self.finish_worker()
        self.assertNotEqual(workers, [main_thread])
        self.assertEqual(completion, [main_thread])
        self.assertTrue(self.app.holdings_table._enabled)

    def test_changed_inputs_discard_background_result(self):
        received = []
        self.app._run('Testing', lambda: 42, received.append)
        self.app._request_changed()
        self.finish_worker()
        self.assertEqual(received, [])
        self.assertIn('Inputs changed', self.app.status.get())

    def test_worker_failure_restores_editor_and_reports_error(self):
        def fail():
            raise ValueError('Price service unavailable')
        self.app._run('Testing', fail, lambda result: None)
        self.finish_worker()
        self.assertTrue(self.app.holdings_table._enabled)
        self.assertIn('unavailable', self.app.status.get())
        self.errors.assert_called_once()

    def test_failed_currency_refresh_clears_previous_prices_and_plan(self):
        self.app.generate_plan()
        self.finish_worker()
        self.assertIsNotNone(self.app.plan)
        with patch('ui.app.fetch_market_data', side_effect=ValueError('Market unavailable')) as fetch:
            self.app.base_currency.set('USD')
            self.assertTrue(self.app.busy)
            self.assertIsNone(self.app.market)
            self.assertIsNone(self.app.plan)
            self.finish_worker()
        fetch.assert_called_once_with(self.app.portfolio(), 'USD')
        self.assertIsNone(self.app.market)
        self.assertIn('unavailable', self.app.current_title.get())
        self.assertIn('Market unavailable', self.app.status.get())
        self.assertEqual(str(self.app.base_entry.cget('state')), 'readonly')
        self.errors.assert_not_called()

    def test_save_and_external_edit_protection(self):
        self.app.cash_table.set_rows([{'currency': 'EUR', 'amount': '35'}])
        self.app._edited()
        self.assertTrue(self.app.save())
        self.assertEqual(load_portfolio(self.directory).cash['EUR'], 35)
        path = self.directory / 'cash.csv'
        external = 'currency,amount\nEUR,99\n'
        path.write_text(external)
        self.assertFalse(self.app.save())
        self.assertEqual(path.read_text(), external)

    def test_table_modal_edit_changes_the_actual_row(self):
        table = self.app.holdings_table
        item = next(item for item in table.tree.get_children() if table._rows[item]['ticker'] == 'OLD')
        def edit_dialog():
            dialog = next(child for child in table.winfo_children() if isinstance(child, tk.Toplevel))
            form = dialog.winfo_children()[0]
            editors = [widget for widget in form.winfo_children() if isinstance(widget, ttk.Entry)]
            for editor in editors:
                if editor.get() == '2':
                    editor.delete(0, 'end')
                    editor.insert(0, '7')
            dialog.event_generate('<Return>')
            if dialog.winfo_exists():
                # invoke directly when a window manager does not focus a withdrawn root
                buttons = [widget for child in form.winfo_children() for widget in child.winfo_children()
                           if isinstance(widget, ttk.Button) and widget.cget('text') == 'Save']
                buttons[0].invoke()
        self.root.after(30, edit_dialog)
        table._edit(item)
        self.assertEqual(next(h.shares for h in self.app.portfolio().holdings if h.ticker == 'OLD'), 7)

    def test_add_existing_zero_share_ticker_updates_the_hidden_holding(self):
        old = next(row for row in self.app.class_table.get_rows() if row['group'] == 'Stocks')
        self.change_class(old, {'group': 'Stocks', 'main_ticker': 'NEW'})
        table = self.app.holdings_table
        original_count = len(table.get_rows())
        hidden = next(item for item in table.tree.get_children() if table.row(item)['ticker'] == 'NEW')
        self.assertNotIn((table, hidden), self.app.real_allocations._sources.values())
        failures = []

        def add_dialog():
            dialog = next(child for child in table.winfo_children() if isinstance(child, tk.Toplevel))
            try:
                form = dialog.winfo_children()[0]
                fields = [widget for widget in form.winfo_children() if isinstance(widget, ttk.Entry)]
                self.assertEqual(len(fields), 2)
                for editor, value in zip(fields, ('NEW', '4')):
                    if isinstance(editor, ttk.Combobox):
                        editor.set(value)
                    else:
                        editor.delete(0, 'end')
                        editor.insert(0, value)
                save = next(widget for child in form.winfo_children() for widget in child.winfo_children()
                            if isinstance(widget, ttk.Button) and widget.cget('text') == 'Save')
                save.invoke()
            except Exception as error:
                failures.append(error)
            finally:
                if dialog.winfo_exists():
                    dialog.destroy()

        self.root.after(30, add_dialog)
        table.add()
        if failures:
            raise failures[0]
        self.assertEqual(len(table.get_rows()), original_count)
        self.assertEqual(table.row(hidden), {'ticker': 'NEW', 'group': 'Stocks', 'shares': '4'})
        self.assertIn((table, hidden), self.app.real_allocations._sources.values())
        self.assertTrue(next(h.is_main for h in self.app.portfolio().holdings if h.ticker == 'NEW'))

    def test_home_add_resolves_catalogue_ticker_and_uses_its_asset_class(self):
        table = self.app.holdings_table
        item = self.app._existing_holding({'ticker': ' MAIN '})
        self.assertIsNotNone(item)
        row = self.app._validate_holding({'ticker': 'MAIN', 'group': 'Bonds', 'shares': '4'}, table.row(item))
        self.assertEqual((row['ticker'], row['group'], row['shares']), ('MAIN', 'Stocks', '4'))
        with self.assertRaisesRegex(ValueError, 'Assets'):
            self.app._validate_holding({'ticker': 'UNKNOWN', 'shares': '4'}, None)
        with self.assertRaisesRegex(ValueError, 'different asset'):
            self.app._validate_holding({'ticker': 'OLD', 'shares': '4'}, table.row(item))

    def test_pie_handles_zero_singleton_and_many_groups(self):
        chart = self.app.current_chart
        chart.set_data({}, {}, 'Nothing invested')
        self.assertIn('Nothing invested', chart.canvas.itemcget(chart.canvas.find_all()[0], 'text'))
        chart.set_data({'A': 1}, {'A': '#123456'})
        self.assertEqual(chart.canvas.type(chart.canvas.find_all()[0]), 'oval')
        chart.set_data({str(i): i + 1 for i in range(20)}, {})
        self.assertEqual(len(chart.canvas.find_all()), 20)
        self.assertAlmostEqual(sum(chart._fractions.values()), 1)

    def test_pie_legend_scrolls_only_when_content_overflows(self):
        window = tk.Toplevel(self.root)
        self.addCleanup(window.destroy)
        window.geometry('520x200')
        chart = PieChart(window)
        chart.pack(fill='both', expand=True)
        scrollbar = next(widget for widget in chart.winfo_children() if isinstance(widget, ttk.Scrollbar))

        def assert_fitted():
            self.assertEqual(chart.legend.yview(), (0.0, 1.0))
            self.assertEqual(chart.legend.canvasy(0), 0)
            self.assertFalse(scrollbar.winfo_ismapped())
            content_height = chart._legend_frame.winfo_reqheight() if chart._data else 0
            self.assertEqual(chart.legend.coords(chart._legend_window),
                             [0, (chart.legend.winfo_height() - content_height) / 2])

        chart.set_data({'A': 1, 'B': 2}, {})
        self.root.update()
        assert_fitted()
        for widget in (chart.legend, *chart._legend_frame.winfo_children()):
            for event, options in (('<Button-4>', {}), ('<Button-5>', {}),
                                   ('<MouseWheel>', {'delta': 120}), ('<MouseWheel>', {'delta': -120})):
                widget.event_generate(event, **options)
                assert_fitted()

        chart.set_data({f'Asset {index}': 1 for index in range(12)}, {})
        self.root.update()
        self.assertTrue(scrollbar.winfo_ismapped())
        self.assertEqual(chart.legend.coords(chart._legend_window), [0, 0])
        chart._legend_frame.winfo_children()[0].event_generate('<Button-5>')
        self.assertGreater(chart.legend.yview()[0], 0)
        chart.legend.event_generate('<Button-4>')
        self.assertEqual(chart.legend.yview()[0], 0)
        chart.legend.event_generate('<MouseWheel>', delta=-120)
        self.assertGreater(chart.legend.yview()[0], 0)

        window.geometry('520x500')
        self.root.update()
        assert_fitted()
        window.geometry('520x200')
        self.root.update()
        chart.legend.yview_moveto(1)
        self.assertGreater(chart.legend.yview()[0], 0)
        chart.set_data({'A': 1}, {})
        self.root.update()
        assert_fitted()
        chart.set_data({}, {})
        self.root.update()
        assert_fitted()

    def test_modal_add_and_confirmed_remove_keep_class_inputs_consistent(self):
        table = self.app.class_table
        def add_dialog():
            dialog = next(child for child in table.winfo_children() if isinstance(child, tk.Toplevel))
            form = dialog.winfo_children()[0]
            editors = [widget for widget in form.winfo_children() if isinstance(widget, ttk.Entry)]
            for editor, value in zip(editors, ('Extra', 'EXTRA')):
                if isinstance(editor, ttk.Combobox):
                    editor.set(value)
                else:
                    editor.insert(0, value)
            save = next(widget for child in form.winfo_children() for widget in child.winfo_children()
                        if isinstance(widget, ttk.Button) and widget.cget('text') == 'Save')
            save.invoke()
        self.root.after(30, add_dialog)
        table._edit()
        self.assertEqual(self.app.portfolio().allocation.weights[STOCK_MARKET]['Extra'], 0)
        self.assertIn(Holding('EXTRA', 'Extra', 0), self.app.portfolio().holdings)
        with patch('ui.widgets.messagebox.askyesno', return_value=False):
            table.remove_selected()
        self.assertIn('Extra', self.app.portfolio().allocation.weights[STOCK_MARKET])
        with patch('ui.widgets.messagebox.askyesno', return_value=True):
            table.remove_selected()
        self.assertEqual(self.app.portfolio(), self.loaded_portfolio)
        self.assertNotIn('Extra', self.app.group_origins)

    def test_canceling_modal_edit_leaves_inputs_unchanged(self):
        table = self.app.cash_table
        def cancel_dialog():
            dialog = next(child for child in table.winfo_children() if isinstance(child, tk.Toplevel))
            dialog.destroy()
        self.root.after(30, cancel_dialog)
        table._edit(table.tree.get_children()[0])
        self.assertEqual(self.app.portfolio(), self.loaded_portfolio)
        self.assertFalse(self.app.dirty)

    def test_navigation_selling_choices_and_window_icon(self):
        self.assertEqual(MODES, ('Not allowed', 'Allowed'))
        labels = [child for child in self.app.mode_box.master.winfo_children()
                  if isinstance(child, ttk.Label) and child.cget('text') == 'Selling']
        self.assertEqual(len(labels), 1)
        self.assertEqual(labels[0].grid_info()['row'], self.app.mode_box.grid_info()['row'])
        self.assertLess(labels[0].grid_info()['column'], self.app.mode_box.grid_info()['column'])
        self.assertEqual(tuple(self.app.mode_box.cget('values')), MODES)
        self.assertEqual([self.app.notebook.tab(tab, 'text') for tab in self.app.notebook.tabs()],
                         ['Home', 'Asset classes', 'Assets', 'Trade plan'])
        self.assertEqual(tuple(self.app.scope_box.cget('values')), ('All', STOCK_MARKET, TREASURY, CASH))
        self.assertEqual(self.app.chart_scope.get(), 'All')
        self.assertEqual((self.app.icon.width(), self.app.icon.height()), (128, 128))
        self.assertIn(CASH, self.app.portfolio().allocation.totals)
        self.assertIn(TREASURY, self.app.portfolio().allocation.totals)

    def edit_money_row(self, table, amount, currency, *, item=None, save=True):
        errors = []
        def interact():
            dialog = next(child for child in table.winfo_children() if isinstance(child, tk.Toplevel))
            try:
                form = dialog.winfo_children()[0]
                fields = [child for child in form.winfo_children() if isinstance(child, ttk.Entry)]
                amount_box = next(child for child in fields if not isinstance(child, ttk.Combobox))
                currency_box = next(child for child in fields if isinstance(child, ttk.Combobox)
                                    and 'HUF' in child.cget('values'))
                self.assertEqual(str(currency_box.cget('state')), 'readonly')
                self.assertIn(currency, currency_box.cget('values'))
                self.assertEqual(amount_box.grid_info()['row'], currency_box.grid_info()['row'])
                self.assertLess(amount_box.grid_info()['column'], currency_box.grid_info()['column'])
                if table is self.app.manual_table:
                    self.assertEqual(tuple(currency_box.cget('values')), ('EUR', 'HUF'))
                    self.assertEqual(len(fields), 2)
                if amount is not None:
                    amount_box.delete(0, 'end')
                    amount_box.insert(0, amount)
                currency_box.set(currency)
                currency_box.insert(0, 'invalid')
                self.assertEqual(currency_box.get(), currency)
                action = 'Save' if save else 'Cancel'
                button = next(child for frame in form.winfo_children() for child in frame.winfo_children()
                              if isinstance(child, ttk.Button) and child.cget('text') == action)
                button.invoke()
            except BaseException as error:
                errors.append(error)
            finally:
                if dialog.winfo_exists():
                    dialog.destroy()
        self.root.after(30, interact)
        table._edit(item)
        if errors:
            raise errors[0]

    def test_treasury_modal_add_selects_currency_and_saves_amount(self):
        table = self.app.manual_table
        self.edit_money_row(table, '40000.25', 'HUF')
        expected = (ManualHolding(0, 'EUR'), ManualHolding(40000.25, 'HUF'))
        self.assertEqual(self.app.portfolio().manual_holdings, expected)
        self.assertTrue(self.app.save())
        self.app._load_directory(self.directory)
        self.assertEqual(self.app.portfolio().manual_holdings, expected)
        item = next(item for item, row in table._rows.items() if row['currency'] == 'HUF')
        before = self.app.portfolio()
        self.edit_money_row(table, '999', 'EUR', item=item, save=False)
        self.assertEqual(self.app.portfolio(), before)
        self.assertFalse(self.app.dirty)
        self.errors.assert_not_called()

    def test_cash_modal_add_and_edit_use_amount_then_currency(self):
        table = self.app.cash_table
        self.edit_money_row(table, '125.50', 'GBP')
        self.assertEqual(dict(self.app.portfolio().cash), {'EUR': 20, 'GBP': 125.50})
        item = next(item for item, row in table._rows.items() if row['currency'] == 'GBP')
        self.edit_money_row(table, '250.75', 'GBP', item=item)
        self.assertTrue(self.app.save())
        self.app._load_directory(self.directory)
        self.assertEqual(dict(self.app.portfolio().cash), {'EUR': 20, 'GBP': 250.75})
        self.assertNotIn('GBP', self.app.portfolio().allocation.weights[CASH])
        self.errors.assert_not_called()

    def test_whole_unit_tables_preserve_fractional_amounts_through_edits_and_save(self):
        self.app.cash_table.set_rows([{'currency': 'EUR', 'amount': '1234.75'}])
        self.app.manual_table.set_rows([{'currency': 'HUF', 'amount': '40000.25'}])
        self.app._edited()
        before = self.app.portfolio()
        for category, table, currency, displayed in (
                (CASH, self.app.cash_table, 'EUR', '1\u202f235'),
                (TREASURY, self.app.manual_table, 'HUF', '40\u202f000')):
            item = next(item for item, row in table._rows.items() if row['currency'] == currency)
            self.assertEqual(table.tree.set(item, 'amount'), displayed)
            combined = next(key for key, source in self.app.real_allocations._sources.items()
                            if source == (table, item))
            self.assertEqual(self.app.real_allocations.tree.set(combined, 'amount'), displayed)
            self.edit_money_row(table, None, currency, item=item)
            self.assertEqual(self.app.portfolio(), before)
        item = self.app.cash_table.tree.get_children()[0]
        self.edit_money_row(self.app.cash_table, None, 'GBP', item=item)
        self.assertEqual(self.app.portfolio().cash, {'GBP': 1234.75})
        expected = self.app.portfolio()
        self.assertTrue(self.app.save())
        self.app._load_directory(self.directory)
        self.assertEqual(self.app.portfolio(), expected)
        self.errors.assert_not_called()

    def test_currency_and_selling_dropdowns_remain_readonly_after_work(self):
        self.assertIsInstance(self.app.base_entry, ttk.Combobox)
        for control in (self.app.base_entry, self.app.mode_box):
            self.assertEqual(str(control.cget('state')), 'readonly')
        self.assertEqual(tuple(self.app.base_entry.cget('values')), ('EUR', 'USD', 'HUF'))
        with patch('ui.app.fetch_market_data', return_value=self.market):
            self.app.refresh_prices()
            for control in (self.app.base_entry, self.app.mode_box):
                self.assertEqual(str(control.cget('state')), 'disabled')
            self.finish_worker()
        for control in (self.app.base_entry, self.app.mode_box):
            self.assertEqual(str(control.cget('state')), 'readonly')
        before = self.app.portfolio()
        dollar_market = MarketData(self.market.quotes, {'EUR': 2}, 'USD')
        with patch('ui.app.fetch_market_data', return_value=dollar_market) as fetch:
            self.app.base_entry.set('USD')
            self.assertTrue(self.app.busy)
            self.assertIsNone(self.app.market)
            self.assertIsNone(self.app.plan)
            self.finish_worker()
        fetch.assert_called_once_with(before, 'USD')
        self.assertIs(self.app.market, dollar_market)
        self.assertEqual(self.app.current_title.get(), 'Current allocation · 220 USD')
        self.assertEqual(self.app.portfolio(), before)
        self.assertFalse(self.app.dirty)
        self.assertEqual(str(self.app.base_entry.cget('state')), 'readonly')

    def test_currency_change_discards_a_late_result_for_the_old_currency(self):
        started, gate, completed = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(gate.set)
        dollar_market = MarketData(self.market.quotes, {'EUR': 2}, 'USD')
        def fetch(portfolio, base):
            if base == 'EUR':
                started.set()
                gate.wait(2)
                completed.set()
                return self.market
            return dollar_market
        with patch('ui.app.fetch_market_data', side_effect=fetch) as request:
            self.app.refresh_prices()
            self.assertTrue(started.wait(2))
            self.app.base_currency.set('USD')
            self.finish_worker()
            gate.set()
            self.assertTrue(completed.wait(2))
            self.root.after(120, self.root.quit)
            self.root.mainloop()
        self.assertEqual([call.args[1] for call in request.call_args_list], ['EUR', 'USD'])
        self.assertIs(self.app.market, dollar_market)
        self.assertIn('220 USD', self.app.current_title.get())
        self.assertFalse(self.app.busy)
        self.errors.assert_not_called()

    def test_add_treasury_manual_value_fx_and_save_roundtrip(self):
        self.assertIn(TREASURY, self.app.portfolio().allocation.totals)
        self.assertEqual(self.app.portfolio().manual_holdings, (ManualHolding(0, 'EUR'),))
        self.app.manual_table.set_rows([{'amount': '40000', 'currency': 'HUF'}])
        self.app._edited()
        market = MarketData(self.market.quotes, {'HUF': .0025})
        self.app._accept_market(market)
        self.assertEqual(self.app.current_chart._data[TREASURY], 100)
        self.assertIn('210 EUR', self.app.current_title.get())
        self.assertTrue(self.app.save())
        self.app._load_directory(self.directory)
        self.assertEqual(self.app.portfolio().manual_holdings, (ManualHolding(40000, 'HUF'),))
        self.assertEqual({row['group'] for row in self.app.class_table.get_rows()},
                         {'Stocks', 'Bonds'})

    def test_manual_holdings_participate_in_every_mode_and_preserve_inputs(self):
        self.set_targets(stock={'Stocks': 25, 'Bonds': 25}, treasury={'EUR': 50, 'HUF': 0})
        self.app.manual_table.set_rows([{'amount': '40', 'currency': 'EUR'}])
        self.app._edited()
        self.app._accept_market(self.market)
        original = self.app.portfolio()
        for mode in MODES:
            self.app.mode.set(mode)
            self.app.generate_plan()
            self.finish_worker()
            self.assertIsNotNone(self.app.plan)
            self.assertIn((TREASURY, 'EUR'), self.app.plan.allocation_values)
            self.assertIn(TREASURY, self.app.plan_report)
            self.assertEqual(self.app.portfolio(), original)
            if mode == MODES[0]:
                self.assertTrue(all(t.amount > 0 for t in self.app.plan.manual_trades))

    def test_treasury_has_only_amount_and_currency_and_no_class_settings(self):
        table = self.app.manual_table
        self.assertEqual(tuple(table.tree['columns']), ('amount', 'currency'))
        self.assertEqual(str(table.tree.column('amount', 'anchor')), 'e')
        self.assertEqual({row['group'] for row in self.app.class_table.get_rows()},
                         {'Stocks', 'Bonds'})
        self.assertEqual(tuple(self.app.class_table.tree['columns']), ('group', 'main_ticker'))
        with self.assertRaisesRegex(ValueError, 'reserved'):
            self.app._validate_class({'group': TREASURY, 'main_ticker': 'BOND'}, None)

    def test_treasury_currency_and_amount_validation(self):
        table = self.app.manual_table
        old = table.get_rows()[0]
        for bad in ({**old, 'amount': '-1'}, {**old, 'amount': '1.001'},
                    {**old, 'currency': 'USD'}):
            with self.subTest(row=bad), self.assertRaises(ValueError):
                self.app._validate_manual(bad, old)
        with self.assertRaisesRegex(ValueError, 'already'):
            self.app._validate_manual({'amount': '10', 'currency': 'EUR'}, None)
        self.assertEqual(self.app._validate_manual({'amount': '12.50', 'currency': 'huf'}, None),
                         {'amount': '12.5', 'currency': 'HUF'})

    def test_treasury_currency_edit_and_last_balance_removal(self):
        table = self.app.manual_table
        item = table.tree.get_children()[0]
        self.edit_money_row(table, '125.50', 'HUF', item=item)
        self.assertEqual(self.app.portfolio().manual_holdings, (ManualHolding(125.5, 'HUF'),))
        table.tree.selection_set(table.tree.get_children()[0])
        with patch('ui.widgets.messagebox.askyesno', return_value=True):
            table.remove_selected()
        self.assertEqual(table.get_rows(), [])
        self.assertEqual(self.app.portfolio().manual_holdings, ())
        self.assertEqual(dict(self.app.portfolio().allocation.weights[TREASURY]), {'EUR': 0, 'HUF': 0})
        self.assertTrue(self.app.save())
        self.assertEqual(load_portfolio(self.directory), self.app.portfolio())

    def test_removing_targeted_treasury_balance_preserves_target_with_zero_holding(self):
        self.set_targets(stock={'Stocks': 25, 'Bonds': 25}, treasury={'EUR': 50, 'HUF': 0})
        table = self.app.manual_table
        table.set_rows([{'amount': '25.50', 'currency': 'EUR'}])
        self.app._edited()
        table.tree.selection_set(table.tree.get_children()[0])
        with patch('ui.widgets.messagebox.askyesno', return_value=True):
            table.remove_selected()
        self.assertEqual(self.app.portfolio().manual_holdings, (ManualHolding(0, 'EUR'),))
        self.assertEqual(self.app.portfolio().allocation.weights[TREASURY]['EUR'], 50)
        self.assertEqual(table.get_rows(), [{'amount': '0.0', 'currency': 'EUR'}])

    def test_refresh_changes_fx_but_never_manual_values(self):
        self.app.manual_table.set_rows([{'amount': '40000', 'currency': 'HUF'}])
        self.app._edited()
        original = self.app.portfolio()
        market = MarketData(self.market.quotes, {'HUF': .0026})
        with patch('ui.app.fetch_market_data', return_value=market) as fetch:
            self.app.refresh_prices()
            self.finish_worker()
        fetch.assert_called_once_with(original, 'EUR')
        self.assertEqual(self.app.portfolio(), original)
        self.assertEqual(self.app.current_chart._data[TREASURY], 104)

    def test_manual_value_row_remains_visible_at_minimum_window_size(self):
        self.app.mode.set(MODES[1])
        self.app.chart_scope.set(TREASURY)
        self.root.deiconify()
        self.root.geometry('1050x760')
        self.root.update()
        tree = self.app.real_allocations.tree
        item = next(item for item, (table, _) in self.app.real_allocations._sources.items()
                    if table is self.app.manual_table)
        box = tree.bbox(item)
        self.assertTrue(box, 'Manual values disappeared below the table header')
        self.assertGreaterEqual(box[3], 25)
        for chart in (self.app.current_chart, self.app.target_chart):
            self.assertGreaterEqual(chart.canvas.winfo_height(), 100)

    def test_manual_editor_rejects_values_that_float_conversion_would_alter(self):
        old = self.app.manual_table.get_rows()[0]
        for text in ('70368744177664.01', '8000000.0000000001', '-1e-999'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.app._validate_manual({**old, 'amount': text}, old)

    def test_plan_tables_clear_when_inputs_change_and_export_uses_completed_result(self):
        self.app.generate_plan()
        self.finish_worker()
        view = self.app.plan_view
        self.assertTrue(view.transactions.get_children())
        self.assertTrue(view.allocations.get_children())
        self.assertTrue(view.cash.get_children())
        path = self.directory / 'plan.txt'
        with patch('ui.app.filedialog.asksaveasfilename', return_value=str(path)):
            self.app.export_plan()
        self.assertIn('Planned wealth:', path.read_text())
        self.app.mode.set('Allowed')
        self.assertIsNone(self.app.plan)
        for tree in (view.transactions, view.allocations, view.cash):
            self.assertEqual(tree.get_children(), ())
        self.assertEqual(self.app.plan_report, '')
        self.assertEqual(str(self.app.export_button.cget('state')), 'disabled')
        with patch('ui.app.filedialog.asksaveasfilename') as dialog:
            self.app.export_plan()
            dialog.assert_not_called()

    def test_inactive_targets_hide_without_removing_saved_definitions(self):
        new = self.app._validate_class({'group': 'Unused', 'main_ticker': 'IDLE'}, None)
        self.app.class_table.set_rows([*self.app.class_table.get_rows(), new])
        self.app._classes_changed('add', None, new)
        self.app.chart_scope.set(STOCK_MARKET)
        tree = self.app.target_table.tree
        root = next(item for item in tree.get_children() if tree.item(item, 'text') == STOCK_MARKET)
        self.assertNotIn('Unused', [tree.item(item, 'text') for item in tree.get_children(root)])
        self.assertNotIn('Unused', self.app.current_chart._data)
        self.assertNotIn('Unused', self.app.target_chart._data)
        self.assertEqual(self.app.portfolio().allocation.weights[STOCK_MARKET]['Unused'], 0)
        self.assertTrue(self.app.save())
        loaded = load_portfolio(self.directory)
        self.assertIn(Holding('IDLE', 'Unused', 0), loaded.holdings)
        self.assertEqual(loaded.allocation.weights[STOCK_MARKET]['Unused'], 0)

    def test_held_zero_target_hides_from_editor_without_removing_holding(self):
        self.app.market = None
        self.set_targets(stock={'Stocks': 0, 'Bonds': 100})
        tree = self.app.target_table.tree
        root = next(item for item in tree.get_children() if tree.item(item, 'text') == STOCK_MARKET)
        self.assertNotIn('Stocks', [tree.item(item, 'text') for item in tree.get_children(root)])
        self.app.chart_scope.set(STOCK_MARKET)
        self.assertNotIn('Stocks', self.app.target_chart._data)

    def test_zero_target_holding_remains_only_in_current_category_chart(self):
        self.set_targets(stock={'Stocks': 0, 'Bonds': 100})
        self.app.chart_scope.set(STOCK_MARKET)
        self.assertEqual(self.app.target_chart._data, {'Bonds': 100})
        self.assertEqual(self.app.current_chart._data, {'Bonds': 50, 'Stocks': 40})
        self.assertEqual(self.app.target_chart._colors, self.app.current_chart._colors)
        self.app.chart_scope.set('All')
        self.assertEqual(self.app.target_chart._data, {STOCK_MARKET: 100, CASH: 0, TREASURY: 0})

    def test_absolute_leaf_targets_derive_category_totals(self):
        self.set_targets(stock={'Stocks': 30, 'Bonds': 30},
                         treasury={'EUR': 12, 'HUF': 18}, cash={'EUR': 10})
        allocation = self.app.portfolio().allocation
        self.assertEqual(dict(allocation.weights[STOCK_MARKET]), {'Bonds': 30, 'Stocks': 30})
        self.assertEqual(allocation.weights[STOCK_MARKET]['Stocks'], 30)
        self.assertEqual(allocation.weights[TREASURY]['HUF'], 18)
        self.assertEqual(dict(allocation.totals), {STOCK_MARKET: 60, TREASURY: 30, CASH: 10})
        self.assertTrue(all(row['parent'] for row in self.app.target_table.get_rows()))
        self.app.chart_scope.set(TREASURY)
        self.assertEqual(self.app.target_chart._data, {'HUF': 18, 'EUR': 12})
        self.assertAlmostEqual(self.app.target_chart._fractions['HUF'], .6)
        self.assertAlmostEqual(self.app.target_chart._fractions['EUR'], .4)
        labels = [child.cget('text') for child in self.app.target_chart._legend_frame.winfo_children()
                  if isinstance(child, ttk.Label)]
        self.assertEqual(labels, ['HUF', '18.0%', 'EUR', '12.0%'])
        visible = {row['currency'] for row in self.app.manual_table.get_rows()}
        self.assertIn('HUF', visible)

    def test_common_scope_switches_all_four_views_without_changing_inputs(self):
        self.root.deiconify()
        before, revision = self.app.portfolio(), self.app.revision
        expected = {
            'All': ({STOCK_MARKET: 90, CASH: 20}, {STOCK_MARKET: 100, CASH: 0, TREASURY: 0}),
            STOCK_MARKET: ({'Bonds': 50, 'Stocks': 40}, {'Bonds': 50, 'Stocks': 50}),
            TREASURY: ({}, {}),
            CASH: ({'EUR': 20}, {}),
        }
        tables = {STOCK_MARKET: self.app.holdings_table, TREASURY: self.app.manual_table,
                  CASH: self.app.cash_table}
        for scope, (current, target) in expected.items():
            with self.subTest(scope=scope):
                self.app.chart_scope.set(scope)
                self.root.update()
                self.assertEqual(self.app.current_chart._data, current)
                self.assertEqual(self.app.target_chart._data, target)
                tree = self.app.target_table.tree
                roots = {tree.item(item, 'text') for item in tree.get_children()}
                self.assertEqual(roots, {STOCK_MARKET, TREASURY, CASH} if scope == 'All' else {scope})
                self.assertEqual(self.app.real_allocations.scope, scope)
                holdings_tree = self.app.real_allocations.tree
                self.assertTrue(holdings_tree.winfo_ismapped())
                visible_categories = {holdings_tree.item(item, 'text') for item in holdings_tree.get_children()}
                self.assertEqual(visible_categories, set(tables) if scope == 'All' else {scope})
                self.assertTrue(all(not table.winfo_ismapped() for table in tables.values()))
                self.assertFalse(self.app.dirty)
                self.assertEqual(self.app.revision, revision)
                self.assertEqual(self.app.portfolio(), before)

    def populate_multicurrency_values(self):
        self.set_targets(stock={'Stocks': 30, 'Bonds': 30},
                         treasury={'EUR': 7.5, 'HUF': 22.5}, cash={'EUR': 4, 'HUF': 2, 'USD': 4})
        self.app.manual_table.set_rows([{'currency': 'EUR', 'amount': '100'},
                                        {'currency': 'HUF', 'amount': '40000'}])
        self.app.cash_table.set_rows([{'currency': 'EUR', 'amount': '20'},
                                      {'currency': 'HUF', 'amount': '4000'},
                                      {'currency': 'USD', 'amount': '40'}])
        self.app._edited()
        self.app._accept_market(MarketData(self.market.quotes, {'HUF': .0025, 'USD': .5}))

    def test_value_is_one_line_for_only_the_selected_scope(self):
        self.populate_multicurrency_values()
        for scope, value in [('All', 340), (STOCK_MARKET, 90), (TREASURY, 200), (CASH, 50)]:
            with self.subTest(scope=scope):
                self.app.chart_scope.set(scope)
                self.assertEqual(self.app.current_title.get(), f'Current allocation · {value} EUR')
                self.assertAlmostEqual(sum(self.app.current_chart._data.values()), value)

    def test_heading_rounds_display_without_rounding_values_or_percentages(self):
        self.app.cash_table.set_rows([{'currency': 'EUR', 'amount': '20.75'}])
        self.set_targets(stock={'Stocks': 33.33, 'Bonds': 66.67})
        self.assertEqual(self.app.current_title.get(), 'Current allocation · 111 EUR')
        self.app.chart_scope.set(CASH)
        self.assertEqual(self.app.current_title.get(), 'Current allocation · 21 EUR')
        self.assertEqual(self.app.current_chart._data, {'EUR': 20.75})
        self.assertEqual(self.app.portfolio().cash, {'EUR': 20.75})
        self.app.chart_scope.set(STOCK_MARKET)
        self.assertEqual(self.app.target_chart._data, {'Bonds': 66.67, 'Stocks': 33.33})
        self.assertEqual(self.app.current_title.get(), 'Current allocation · 90 EUR')

    def test_saving_a_filtered_view_preserves_every_category(self):
        self.populate_multicurrency_values()
        before = self.app.portfolio()
        self.app.chart_scope.set(CASH)
        self.assertTrue(self.app.save())
        self.assertEqual(load_portfolio(self.directory), before)
        self.app._load_directory(self.directory)
        self.assertEqual(self.app.portfolio(), before)
        self.assertEqual(self.app.chart_scope.get(), CASH)
        self.assertEqual(self.app.real_allocations.scope, CASH)
        self.assertEqual({row['parent'] for row in self.app.target_table.get_rows()},
                         {STOCK_MARKET, TREASURY, CASH})

    def test_changing_scope_preserves_completed_plan_and_export(self):
        self.app.generate_plan()
        self.finish_worker()
        plan, report, revision = self.app.plan, self.app.plan_report, self.app.revision
        self.assertIsNotNone(plan)
        for scope in (STOCK_MARKET, TREASURY, CASH, 'All'):
            self.app.chart_scope.set(scope)
            self.assertIs(self.app.plan, plan)
            self.assertEqual(self.app.plan_report, report)
            self.assertEqual(self.app.revision, revision)
            self.assertFalse(self.app.dirty)
            self.assertEqual(str(self.app.export_button.cget('state')), 'normal')
            self.assertTrue(self.app.plan_view.allocations.get_children())

    def test_incomplete_refresh_preserves_previous_prices(self):
        self.populate_multicurrency_values()
        before = self.app.market
        with patch('ui.app.fetch_market_data', return_value=MarketData(self.market.quotes, {'USD': .5})):
            self.app.refresh_prices()
            self.finish_worker()
        self.assertIs(self.app.market, before)
        self.assertIn('HUF', self.app.status.get())
        self.errors.assert_called_once()

    def test_selected_category_ignores_missing_prices_in_another_category(self):
        self.app.manual_table.set_rows([{'currency': 'HUF', 'amount': '40000'}])
        self.app._edited()
        self.app.chart_scope.set(STOCK_MARKET)
        self.assertIn('90 EUR', self.app.current_title.get())
        self.app.chart_scope.set(TREASURY)
        self.assertIn('unavailable', self.app.current_title.get())
        self.assertIn('HUF', self.app.current_chart._empty_message)


    def test_matrix_headers_and_geometry_at_minimum_window_size(self):
        self.root.deiconify()
        self.root.geometry('1050x760')
        self.app.save_button.configure(text='Save inputs *')
        self.root.update()

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        self.assertNotIn('Portfolio planner', [widget.cget('text') for widget in descendants(self.root)
                                               if isinstance(widget, ttk.Label)])
        buttons = (self.app.open_button, self.app.save_button)
        self.assertEqual(self.app.open_button.cget('text'), 'Open directory')
        self.assertEqual(len({button.winfo_rooty() for button in buttons}), 1)
        self.assertTrue(all(button in set(descendants(self.app.portfolio_page)) for button in buttons))
        widgets = list(descendants(self.app.portfolio_page))
        labels = {widget.cget('text'): widget for widget in widgets if isinstance(widget, ttk.Label)}
        self.assertNotIn('All assets, including cash', labels)
        self.assertNotIn('Target allocations', labels)
        self.assertNotIn('Real allocations', labels)
        left_header = labels['Target allocation']
        headings = [widget for widget in widgets if isinstance(widget, ttk.Label)
                    and str(widget.cget('textvariable')) == str(self.app.current_title)]
        self.assertEqual(len(headings), 1)
        right_header = headings[0]
        source = next(widget for widget in widgets if isinstance(widget, ttk.Label)
                      and str(widget.cget('textvariable')) == str(self.app.source))
        self.assertNotIn('Refresh prices', [widget.cget('text') for widget in widgets
                                             if isinstance(widget, ttk.Button)])
        self.assertIs(source.master, self.app.scope_box.master)
        self.assertGreater(source.winfo_rootx(),
                           self.app.scope_box.winfo_rootx() + self.app.scope_box.winfo_width())
        self.assertLessEqual(source.winfo_rooty(),
                             self.app.scope_box.winfo_rooty() + self.app.scope_box.winfo_height())
        for header, chart in ((left_header, self.app.target_chart), (right_header, self.app.current_chart)):
            self.assertIs(header.master, self.app.matrix)
            self.assertLess(header.winfo_rooty(), chart.master.winfo_rooty())
        self.assertLess(left_header.winfo_rootx(), right_header.winfo_rootx())
        self.assertLess(self.app.target_chart.winfo_rootx(), self.app.current_chart.winfo_rootx())
        self.assertEqual(self.app.target_chart.winfo_rooty(), self.app.current_chart.winfo_rooty())
        self.assertLess(self.app.target_chart.winfo_rooty(), self.app.target_table.winfo_rooty())
        self.assertLess(self.app.current_chart.winfo_rooty(), self.app.real_allocations.winfo_rooty())
        self.assertLess(self.app.target_table.winfo_rootx(), self.app.real_allocations.winfo_rootx())
        controls = (self.app.open_button, self.app.save_button, self.app.base_entry,
                    self.app.scope_box, source)
        for first, second in zip(controls, controls[1:]):
            self.assertLess(first.winfo_rootx() + first.winfo_width(), second.winfo_rootx())
        row_top = max(control.winfo_rooty() for control in controls)
        row_bottom = min(control.winfo_rooty() + control.winfo_height() for control in controls)
        self.assertLess(row_top, row_bottom)
        self.assertLess(row_bottom, left_header.winfo_rooty())
        self.assertEqual(self.app.base_entry.winfo_rooty(), self.app.scope_box.winfo_rooty())
        heading_font = ttk.Style(self.root).lookup(right_header.cget('style'), 'font')
        self.assertEqual(font.Font(root=self.root, font=heading_font).actual('weight'), 'bold')
        heading_height = right_header.winfo_height()
        for panel in (self.app.target_panel, self.app.real_panel,
                      self.app.target_chart.master, self.app.current_chart.master):
            self.assertFalse(any(isinstance(child, ttk.Label) and child.winfo_ismapped()
                                 for child in panel.winfo_children()))
        tree = self.app.real_allocations.tree
        for scope in ('All', STOCK_MARKET, TREASURY, CASH):
            self.app.chart_scope.set(scope)
            self.root.update()
            self.assertEqual(right_header.winfo_height(), heading_height)
            self.assertNotIn('\n', self.app.current_title.get())
            self.assertTrue(tree.winfo_ismapped())
            self.assertTrue(tree.bbox(tree.get_children()[0]))
            self.assertLessEqual(tree.winfo_rooty() + tree.winfo_height(),
                                 self.root.winfo_rooty() + self.root.winfo_height())
            for chart in (self.app.target_chart, self.app.current_chart):
                self.assertGreaterEqual(chart.canvas.winfo_height(), 100)
            for control in (right_header, source, self.app.base_entry, self.app.scope_box,
                            self.app.open_button, self.app.save_button):
                self.assertTrue(control.winfo_ismapped())
                self.assertGreaterEqual(control.winfo_width(), control.winfo_reqwidth())
                self.assertLessEqual(control.winfo_rootx() + control.winfo_width(),
                                     self.root.winfo_rootx() + self.root.winfo_width())
        forint_market = MarketData(self.market.quotes, {'EUR': 400}, 'HUF')
        with patch('ui.app.fetch_market_data', return_value=forint_market):
            self.app.base_entry.set('HUF')
            self.finish_worker()
        self.root.update()
        self.assertGreaterEqual(source.winfo_width(), source.winfo_reqwidth())
        self.assertLessEqual(source.winfo_rootx() + source.winfo_width(),
                             self.root.winfo_rootx() + self.root.winfo_width())

    def test_currency_targets_remain_after_balance_row_removed(self):
        self.app.cash_table.set_rows([{'currency': 'EUR', 'amount': '20'}, {'currency': 'GBP', 'amount': '0'}])
        self.app._edited()
        self.assertNotIn('GBP', self.app.portfolio().allocation.weights[CASH])
        self.set_targets(stock={'Stocks': 40, 'Bonds': 40}, cash={'EUR': 10, 'GBP': 10})
        self.app.cash_table.set_rows([{'currency': 'EUR', 'amount': '20'}])
        self.app._edited()
        self.assertEqual(self.app.portfolio().allocation.weights[CASH]['GBP'], 10)
        with patch('ui.app.fetch_market_data', return_value=MarketData(self.market.quotes, {'GBP': 1.2})) as fetch:
            self.app.generate_plan()
            self.finish_worker()
        fetch.assert_called_once_with(self.app.portfolio(), 'EUR')
        self.assertIsNotNone(self.app.plan)
        self.assertGreater(self.app.plan.cash['GBP'], 0)

    def test_treasury_chart_uses_fx_and_separate_currency_ratios(self):
        self.set_targets(stock={'Stocks': 30, 'Bonds': 30}, treasury={'EUR': 10, 'HUF': 30})
        self.app.manual_table.set_rows([{'currency': 'EUR', 'amount': '100'},
                                        {'currency': 'HUF', 'amount': '40000'}])
        self.app._edited()
        self.app._accept_market(MarketData(self.market.quotes, {'HUF': .0025}))
        self.app.chart_scope.set(TREASURY)
        self.assertEqual(self.app.current_chart._data, {'EUR': 100, 'HUF': 100})
        self.assertEqual(self.app.target_chart._data, {'HUF': 30, 'EUR': 10})
        self.assertEqual(self.app.current_chart._fractions, {'EUR': .5, 'HUF': .5})

    def test_target_editor_keeps_selection_after_callback(self):
        table = self.app.target_table
        item = next(item for item, row in table._rows.items()
                    if row['parent'] == STOCK_MARKET and row['group'] == 'Stocks')
        table._set_target(item, 55)
        selected = table.tree.selection()
        self.assertEqual(len(selected), 1)
        self.assertEqual(table._rows[selected[0]]['group'], 'Stocks')

    def test_close_while_worker_finishes_does_not_touch_destroyed_widgets(self):
        gate = threading.Event()
        self.app._run('Waiting', lambda: gate.wait(.2), lambda result: self.fail('Closed callback ran'))
        self.app.close()
        gate.set()
        self.assertTrue(self.app.closed)
    def test_canceling_work_unlocks_editor_and_discards_late_result(self):
        gate, finished = threading.Event(), threading.Event()
        self.addCleanup(gate.set)
        received = []
        def wait():
            gate.wait(2)
            finished.set()
            return 'late'
        self.app._run('Waiting', wait, received.append)
        self.assertEqual(str(self.app.cancel_button.cget('state')), 'normal')
        self.app.cancel_button.invoke()
        self.assertFalse(self.app.busy)
        self.assertTrue(self.app.holdings_table._enabled)
        gate.set()
        self.assertTrue(finished.wait(2))
        self.app._run('New request', lambda: 'current', received.append)
        self.finish_worker()
        self.assertEqual(received, ['current'])
        self.assertEqual(str(self.app.cancel_button.cget('state')), 'disabled')

    def test_refresh_deadline_restores_editor_even_with_blocked_request(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        self.app._run('Waiting', lambda: gate.wait(2), lambda result: self.fail('Timed-out result used'), timeout=30)
        self.finish_worker()
        self.assertIn('timed out', self.app.status.get())
        self.assertTrue(self.app.holdings_table._enabled)
        self.assertIsNone(self.app.timeout_id)

    def test_corrected_totals_clear_validation_error_and_align_chart_order(self):
        self.set_targets(stock={'Stocks': 40, 'Bonds': 40})
        self.assertIn('80%', self.app.status.get())
        self.assertIn('add 20%', self.app.status.get())
        self.set_targets(stock={'Stocks': 40, 'Bonds': 60})
        self.assertIn('Targets valid', self.app.status.get())
        self.populate_multicurrency_values()
        self.app.chart_scope.set(CASH)
        self.assertEqual(list(self.app.current_chart._data), list(self.app.target_chart._data))

    def test_price_timestamp_is_preserved_in_plan_context(self):
        stamp = datetime.now(timezone.utc)
        self.app._accept_market(MarketData(self.market.quotes, {}, fetched_at=stamp))
        expected = f'Fetched {stamp.astimezone():%Y-%m-%d %H:%M}'
        self.assertEqual(self.app.source.get(), expected)
        self.app.generate_plan()
        self.finish_worker()
        self.assertIn(expected, self.app.plan_report)


if __name__ == '__main__':
    unittest.main()
