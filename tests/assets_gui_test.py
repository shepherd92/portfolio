"""The asset catalogue owns metadata; Home edits only existing share balances."""

from datetime import date
import os
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from portfolio.models import AllocationTargets, AssetDetails, CASH, STOCK_MARKET, TREASURY, Holding, MarketData, Portfolio, Quote
from portfolio.market import BaseRate
from portfolio.storage import load_portfolio, save_portfolio
from ui.app import PortfolioApp
from ui.target_mix import change_mix_share


@unittest.skipUnless(os.environ.get('REBALANCE_GUI_TESTS') == '1',
                     'GUI tests require REBALANCE_GUI_TESTS=1 and a display')
class AssetsGuiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.main_details = AssetDetails('IE00B4L5Y983', 'Manual venue', 'EUR', .19,
                                         'Manual full name', 'BROKER MAIN')
        self.old_details = AssetDetails(exchange='Other venue', currency='GBP', ter=0,
                                        name='Original name', ibkr_name='BROKER OLD')
        allocation = AllocationTargets({STOCK_MARKET: {'Stocks': 100, 'Bonds': 0}, TREASURY: {}, CASH: {}},
                                       {'Stocks': 'Equities', 'Bonds': 'Bonds'})
        portfolio = Portfolio((Holding('MAIN', 'Stocks', 3, details=self.main_details),
                               Holding('OLD', 'Stocks', 2, False, self.old_details),
                               Holding('BOND', 'Bonds', 0)), allocation, {'EUR': 20})
        save_portfolio(portfolio, self.directory)
        self.market = MarketData({ticker: Quote(10, 'EUR') for ticker in ('MAIN', 'OLD', 'BOND', 'NEW')}, {})
        self.root = tk.Tk()
        self.root.withdraw()
        for name, value in (('fetch_market_data', self.market), ('fetch_asset_details', AssetDetails()),
                            ('fetch_base_rate', BaseRate(5, date(2026, 1, 1))),
                            ('messagebox.showerror', None)):
            mock = patch(f'ui.app.{name}', return_value=value)
            self.addCleanup(mock.stop)
            setattr(self, name.split('.')[-1], mock.start())
        self.app = PortfolioApp(self.root, self.directory)
        self.addCleanup(self.close)
        self.finish_worker()

    def test_assets_table_contains_information_and_zero_share_catalogue_entries(self):
        table = self.app.assets_table
        self.assertEqual(tuple(table.tree['columns']),
                         ('ticker', 'group', 'name', 'isin', 'exchange', 'price', 'currency', 'ter', 'ibkr_name', 'color_hue'))
        self.assertEqual(table.tree.set(self.item(table, 'MAIN'), 'ter'), '0.19%')
        self.assertEqual(table.tree.set(self.item(table, 'OLD'), 'ter'), '0%')
        self.assertEqual({row['ticker'] for row in table.get_rows()}, {'MAIN', 'OLD', 'BOND'})
        self.assertNotIn('BOND', self.visible_tickers())
        self.assertEqual(next(holding.details for holding in self.app.portfolio().holdings
                              if holding.ticker == 'MAIN'), self.main_details)

    def test_ter_has_at_most_two_decimals_but_sorting_and_saving_keep_original_precision(self):
        table = self.app.assets_table
        for ticker, value in (('MAIN', '0.1949'), ('OLD', '0.1911')):
            self.edit(table, {'ter': value}, item=self.item(table, ticker))
            self.assertEqual(table.tree.set(self.item(table, ticker), 'ter'), '0.19%')
        click = table.tree.heading('ter', 'command')
        self.assertTrue(click)
        table.tree.tk.call(click)
        self.assertEqual([table.tree.set(item, 'ticker') for item in table.tree.get_children()],
                         ['OLD', 'MAIN', 'BOND'])
        table.tree.tk.call(click)
        self.assertEqual([table.tree.set(item, 'ticker') for item in table.tree.get_children()],
                         ['MAIN', 'OLD', 'BOND'])
        self.app._edited()
        self.assertEqual([table.tree.set(item, 'ticker') for item in table.tree.get_children()],
                         ['MAIN', 'OLD', 'BOND'])
        self.assertTrue(self.app.save())
        saved = {holding.ticker: holding.details.ter for holding in load_portfolio(self.directory).holdings}
        self.assertEqual(saved, {'MAIN': .1949, 'OLD': .1911, 'BOND': None})

    def test_manual_color_hue_saves_full_precision_and_clears_back_to_automatic(self):
        from ui.palette import hue_color
        table = self.app.assets_table
        for value in ('123.456789', '0', ''):
            self.edit(table, {'color_hue': value}, item=self.item(table, 'MAIN'))
            details = next(holding.details for holding in self.app.portfolio().holdings if holding.ticker == 'MAIN')
            self.assertEqual(details.color_hue, float(value) if value else None)
            shown = table.tree.set(self.item(table, 'MAIN'), 'color_hue')
            self.assertEqual(shown, f'{float(value):.2f}°' if value else 'Automatic')
            if value:
                self.assertEqual(self.app._chart_colors(STOCK_MARKET)['Stocks'], hue_color(float(value)))
            self.assertTrue(self.app.save())
            self.assertEqual(load_portfolio(self.directory), self.app.portfolio())

    def test_home_dropdown_uses_assets_and_adding_an_existing_ticker_updates_its_balance(self):
        self.edit(self.app.assets_table, {'ticker': 'NEW', 'group': 'Bonds', 'name': 'New fund'})
        table = self.app.holdings_table
        count = len(table.get_rows())

        def check(editors):
            self.assertEqual(set(editors), {'ticker', 'shares'})
            self.assertIsInstance(editors['ticker'], ttk.Combobox)
            self.assertEqual(str(editors['ticker']['state']), 'readonly')
            self.assertEqual(tuple(editors['ticker']['values']), ('BOND', 'MAIN', 'NEW', 'OLD'))

        self.edit(table, {'ticker': 'NEW', 'shares': '7'}, inspect=check)
        self.edit(table, {'ticker': 'NEW', 'shares': '9'})
        self.assertEqual(len(table.get_rows()), count)
        holding = next(holding for holding in self.app.portfolio().holdings if holding.ticker == 'NEW')
        self.assertEqual((holding.group, holding.shares, holding.details.name), ('Bonds', 9, 'New fund'))
        self.assertIn('NEW', self.visible_tickers())
        with self.assertRaisesRegex(ValueError, 'Assets'):
            self.app._validate_holding({'ticker': 'UNKNOWN', 'shares': '1'}, None)

    def test_home_removal_keeps_catalogue_metadata_and_representative(self):
        table = self.app.holdings_table
        for ticker in ('MAIN', 'OLD'):
            table.tree.selection_set(self.item(table, ticker))
            with patch('ui.table.messagebox.askyesno', return_value=True):
                table.remove_selected()
        holdings = {holding.ticker: holding for holding in self.app.portfolio().holdings}
        self.assertEqual(holdings['MAIN'].shares, 0)
        self.assertTrue(holdings['MAIN'].is_main)
        self.assertEqual(holdings['MAIN'].details, self.main_details)
        self.assertEqual(holdings['OLD'].details, self.old_details)
        self.assertFalse(self.visible_tickers())
        self.assertEqual({row['ticker'] for row in self.app.assets_table.get_rows()}, {'MAIN', 'OLD', 'BOND'})
        self.assertTrue(self.app.save())
        self.assertEqual(load_portfolio(self.directory), self.app.portfolio())

    def test_share_and_asset_class_edits_preserve_metadata_when_saved(self):
        assets = self.app.assets_table
        self.edit(assets, {'group': 'Bonds', 'ter': '0.35%', 'name': 'Edited fund'},
                  item=self.item(assets, 'OLD'))
        holdings = self.app.holdings_table
        self.edit(holdings, {'shares': '17'}, item=self.item(holdings, 'OLD'))
        classes = self.app.class_table
        item = next(item for item in classes.tree.get_children() if classes.row(item)['group'] == 'Bonds')
        self.edit(classes, {'group': 'Income'}, item=item)
        self.assertTrue(self.app.save())
        loaded = {holding.ticker: holding for holding in load_portfolio(self.directory).holdings}
        self.assertEqual((loaded['OLD'].group, loaded['OLD'].shares), ('Income', 17))
        self.assertEqual(loaded['OLD'].details, AssetDetails(exchange='Other venue', currency='GBP',
                         ter=.35, name='Edited fund', ibkr_name='BROKER OLD'))
        self.assertEqual(loaded['MAIN'].details, self.main_details)

    def test_representative_identity_is_locked_and_catalogue_deletion_requires_zero_shares(self):
        table = self.app.assets_table

        def check(editors):
            self.assertEqual(str(editors['ticker']['state']), 'readonly')
            self.assertEqual(str(editors['group']['state']), 'readonly')
            self.assertEqual(str(editors['name']['state']), 'normal')

        self.edit(table, {'name': 'Updated main'}, item=self.item(table, 'MAIN'), inspect=check)
        main = table.row(self.item(table, 'MAIN'))
        with self.assertRaisesRegex(ValueError, 'representative'):
            self.app._validate_asset({**main, 'group': 'Bonds'}, main)
        for ticker, message in (('MAIN', 'representative'), ('OLD', 'zero')):
            with self.subTest(ticker=ticker), self.assertRaisesRegex(ValueError, message):
                self.app._can_delete_asset(table.row(self.item(table, ticker)))
        self.edit(self.app.holdings_table, {'shares': '0'}, item=self.item(self.app.holdings_table, 'OLD'))
        table.tree.selection_set(self.item(table, 'OLD'))
        with patch('ui.table.messagebox.askyesno', return_value=True):
            table.remove_selected()
        self.assertNotIn('OLD', self.app._holding_tickers())

    def test_class_removal_requires_zero_target_even_without_holdings(self):
        self.app.holdings_table.set_rows([{**row, 'shares': '0'} for row in self.app.holdings_table.get_rows()])
        self.app._edited()
        table = self.app.class_table
        item = next(item for item in table.tree.get_children() if table.row(item)['group'] == 'Stocks')
        table.tree.selection_set(item)
        before = self.app.portfolio()
        with patch('ui.table.messagebox.showerror') as error, patch('ui.table.messagebox.askyesno') as confirm:
            table.remove_selected()
        self.assertIn('Stock market → Allocations', error.call_args.args[1])
        confirm.assert_not_called()
        self.assertEqual(self.app.portfolio(), before)

        self.app.target_table.set_rows(change_mix_share(self.app.target_table.get_rows(), CASH, 'EUR', 100, activation_total=100))
        self.app._edited()
        with patch('ui.table.messagebox.askyesno', return_value=True):
            table.remove_selected()
        self.assertNotIn('Stocks', self.app.portfolio().allocation.weights[STOCK_MARKET])
        self.assertEqual(self.app.portfolio().allocation.totals, {STOCK_MARKET: 0, TREASURY: 0, CASH: 100})
        self.assertTrue(self.app.save())
        self.assertEqual(load_portfolio(self.directory), self.app.portfolio())

    def test_metadata_lookup_updates_shared_holdings_without_unlocking_summary(self):
        fetched = AssetDetails('IE00B4L5Y983', 'Provider venue', 'USD', .75, 'Provider name')
        self.fetch_asset_details.return_value = fetched
        original_items = set(self.app.holdings_table.tree.get_children())
        self.app.notebook.select(self.app.portfolio_page)
        for middle, inner in ((self.app.summary_page, self.app.assets_page),
                              (self.app.stock_page, self.app.classes_page),
                              (self.app.stock_page, self.app.allocation_pages[STOCK_MARKET]),
                              (self.app.allocation_pages[TREASURY], self.app.assets_page)):
            self.app.portfolio_notebook.select(middle)
            self.app.stock_notebook.select(inner)
            self.poll()
            self.fetch_asset_details.assert_not_called()
        self.app.portfolio_notebook.select(self.app.stock_page)
        self.app.stock_notebook.select(self.app.assets_page)
        self.app.notebook.select(self.app.wealth.pages['loans'])
        self.poll()
        self.fetch_asset_details.assert_not_called()
        self.app.notebook.select(self.app.portfolio_page)
        self.poll()
        self.finish_worker()
        self.assertEqual({call.args[0] for call in self.fetch_asset_details.call_args_list}, {'OLD', 'BOND'})
        holdings = {holding.ticker: holding for holding in self.app.portfolio().holdings}
        self.assertEqual(holdings['MAIN'].details, self.main_details)
        self.assertEqual(holdings['OLD'].details, AssetDetails('IE00B4L5Y983', 'Other venue', 'GBP',
                         0, 'Original name', 'BROKER OLD'))
        self.assertEqual(holdings['BOND'].details, fetched)
        self.assertTrue(self.app.dirty)
        self.app.notebook.select(0)
        self.app.portfolio_notebook.select(self.app.summary_page)
        current_items = set(self.app.holdings_table.tree.get_children())
        self.assertTrue(original_items.isdisjoint(current_items))
        for view in (self.app.real_allocations, self.app.allocation_pages[STOCK_MARKET].holdings):
            self.assertTrue(all(source in current_items for table, source in view._sources.values()
                                if table is self.app.holdings_table))
            leaf = next(item for item, (table, source) in view._sources.items()
                        if table is self.app.holdings_table and table.row(source)['ticker'] == 'OLD')
            view.tree.selection_set(leaf)
            view._update_buttons()
            with patch.object(self.app.holdings_table, 'edit_selected') as edit:
                view._edit_selected()
                if view is self.app.real_allocations:
                    self.assertFalse(hasattr(view, 'edit_button'))
                    edit.assert_not_called()
                else:
                    self.assertFalse(view.edit_button.instate(['disabled']))
                    edit.assert_called_once()
                    self.assertEqual(self.app.holdings_table.row(self.app.holdings_table.tree.selection()[0])['ticker'], 'OLD')
        self.assertTrue(self.app.save())
        self.assertEqual(load_portfolio(self.directory), self.app.portfolio())
        self.poll()
        self.assertEqual(self.fetch_asset_details.call_count, 2)

    def test_failed_lookup_preserves_inputs_and_can_be_retried_explicitly(self):
        before = self.app.portfolio()
        self.fetch_asset_details.side_effect = ValueError('No provider data')
        self.app.notebook.select(self.app.portfolio_page)
        self.app.portfolio_notebook.select(self.app.stock_page)
        self.app.stock_notebook.select(self.app.assets_page)
        self.poll()
        self.finish_worker()
        self.assertEqual(self.app.portfolio(), before)
        self.assertFalse(self.app.dirty)
        self.assertIn('No provider data', self.app.status.get())
        self.showerror.assert_not_called()
        self.poll()
        self.assertEqual(self.fetch_asset_details.call_count, 2)
        self.fetch_asset_details.side_effect = None
        self.fetch_asset_details.return_value = AssetDetails(name='Available now')
        self.app.assets_table.lookup_button.invoke()
        self.finish_worker()
        self.assertEqual(next(holding.details.name for holding in self.app.portfolio().holdings
                              if holding.ticker == 'BOND'), 'Available now')

    def test_canceled_lookup_discards_late_result(self):
        before = self.app.portfolio()
        release, finished = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def lookup(ticker, *, cache):
            release.wait(2)
            finished.set()
            return AssetDetails(name='Too late')

        self.fetch_asset_details.side_effect = lookup
        self.app.lookup_assets(['BOND'])
        self.assertTrue(self.app.busy)
        self.assertTrue(self.app.assets_table.lookup_button.instate(['disabled']))
        self.app.cancel_work()
        release.set()
        self.assertTrue(finished.wait(2))
        deadline = time.monotonic() + 2
        while self.app.events.empty() and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertFalse(self.app.events.empty(), 'Canceled worker did not return')
        self.poll()
        self.assertEqual(self.app.portfolio(), before)
        self.assertFalse(self.app.dirty)
        self.assertFalse(self.app.assets_table.lookup_button.instate(['disabled']))

    def item(self, table, ticker):
        return next(item for item in table.tree.get_children() if table.row(item)['ticker'] == ticker)

    def visible_tickers(self):
        return {table.row(item)['ticker'] for table, item in self.app.real_allocations._sources.values()
                if table is self.app.holdings_table}

    def edit(self, table, values, *, item=None, inspect=None):
        def interact(dialog, first):
            form = dialog.winfo_children()[0]
            fields = [child for child in form.winfo_children() if isinstance(child, ttk.Entry)]
            editors = dict(zip((key for row in table.field_rows for key in row), fields))
            if inspect:
                inspect(editors)
            for key, value in values.items():
                editor = editors[key]
                if isinstance(editor, ttk.Combobox):
                    editor.set(value)
                else:
                    editor.delete(0, 'end')
                    editor.insert(0, value)
            save = next(child for frame in form.winfo_children() for child in frame.winfo_children()
                        if isinstance(child, ttk.Button) and child['text'] == 'Save')
            save.invoke()
            if dialog.winfo_exists():
                dialog.destroy()
                self.fail('The editor did not accept valid inputs.')

        with patch('ui.table.show_modal', side_effect=interact):
            table._edit(item)

    def poll(self):
        self.root.after_cancel(self.app.poll_id)
        self.app._poll()

    def finish_worker(self):
        deadline = time.monotonic() + 5
        while self.app.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertFalse(self.app.busy, 'Background work did not finish')

    def close(self):
        self.app.dirty = False
        self.app.close()
