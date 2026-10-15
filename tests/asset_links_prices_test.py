"""Security links and live prices do not change editable portfolio inputs."""

import os
from pathlib import Path
import tempfile
import time
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from portfolio.models import (AllocationTargets, AssetDetails, CASH, STOCK_MARKET, TREASURY,
                              Conversion, Holding, ManualHolding, ManualTrade, MarketData,
                              Plan, Portfolio, Quote, Trade)
from portfolio.storage import load_portfolio, save_portfolio
from ui.app import PortfolioApp
from ui import links
from ui.links import isin_url, open_cell_link, quote_url


class SecurityUrlTests(unittest.TestCase):
    def test_security_identifiers_are_encoded_as_url_data(self):
        self.assertEqual(quote_url('VWCE.DE'), 'https://finance.yahoo.com/quote/VWCE.DE/')
        self.assertEqual(quote_url('A/B?x=1'), 'https://finance.yahoo.com/quote/A%2FB%3Fx%3D1/')
        self.assertEqual(quote_url('^GSPC'), 'https://finance.yahoo.com/quote/%5EGSPC/')
        self.assertEqual(isin_url('IE00BK5BQT80'),
                         'https://www.justetf.com/en-de/etf-profile.html?isin=IE00BK5BQT80')

    def test_empty_identifiers_have_no_link(self):
        self.assertFalse(quote_url(''))
        self.assertFalse(isin_url(''))

    def test_frozen_browser_launch_restores_host_library_path_without_changing_process_environment(self):
        for original in (None, '', '/host/libraries'):
            environment = {'LD_LIBRARY_PATH': '/bundled/libraries', 'PATH': '/usr/bin'}
            if original is not None:
                environment['LD_LIBRARY_PATH_ORIG'] = original
            with self.subTest(original=original), patch.dict(os.environ, environment, clear=True), \
                    patch.object(links.sys, 'frozen', True, create=True), \
                    patch.object(links.sys, 'platform', 'linux'), patch('ui.links.subprocess.Popen') as launch:
                links._open_browser(quote_url('VWCE.DE'))
                self.assertEqual(launch.call_args.args, (['xdg-open', quote_url('VWCE.DE')],))
                child_environment = launch.call_args.kwargs['env']
                self.assertEqual(child_environment.get('LD_LIBRARY_PATH'), original)
                self.assertNotIn('LD_LIBRARY_PATH_ORIG', child_environment)
                self.assertEqual(dict(os.environ), environment)


@unittest.skipUnless(os.environ.get('REBALANCE_GUI_TESTS') == '1',
                     'GUI tests require REBALANCE_GUI_TESTS=1 and a display')
class AssetLinksPricesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        details = AssetDetails('IE00BK5BQT80', 'Manual venue', 'GBP', .1949, 'Manual name', 'Broker label')
        targets = AllocationTargets({STOCK_MARKET: {'Stocks': 100}, TREASURY: {}, CASH: {}})
        self.portfolio = Portfolio((Holding('MAIN', 'Stocks', 1, details=details),
                                    Holding('OLD', 'Stocks', 1, False, details),
                                    Holding('EMPTY', 'Stocks', 0, False, details)),
                                   targets, {'EUR': 20}, (ManualHolding(100, 'EUR'),))
        save_portfolio(self.portfolio, self.directory)
        self.market = MarketData({'MAIN': Quote(10.004, 'EUR'), 'OLD': Quote(10.001, 'USD')}, {'USD': .9})
        self.root = tk.Tk()
        self.root.geometry('1500x900')
        for name, value in (('fetch_market_data', self.market), ('fetch_asset_details', AssetDetails()),
                            ('messagebox.showerror', None)):
            mocked = patch(f'ui.app.{name}', return_value=value)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.app = PortfolioApp(self.root, self.directory)
        self.addCleanup(self.close)
        deadline = time.monotonic() + 5
        while self.app.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertFalse(self.app.busy)
        self.root.update()

    def close(self):
        self.app.dirty = False
        self.app.close()

    def item(self, ticker):
        table = self.app.assets_table
        return next(item for item in table.tree.get_children() if table.row(item)['ticker'] == ticker)

    def cell(self, tree, item, column):
        tree.see(item)
        self.root.update()
        box = tree.bbox(item, column)
        self.assertTrue(box, (item, column))
        x, y, width, height = box
        return SimpleNamespace(x=x + width // 2, y=y + height // 2, widget=tree)

    def show_assets(self):
        self.app.notebook.select(self.app.assets_page)
        self.root.update()
        return self.app.assets_table

    def test_assets_links_open_browser_without_opening_editor(self):
        table = self.show_assets()
        item = self.item('MAIN')
        for column, url in (('ticker', quote_url('MAIN')), ('isin', isin_url('IE00BK5BQT80'))):
            with self.subTest(column=column), patch('webbrowser.open_new_tab') as browser, \
                    patch.object(table, '_edit') as edit:
                table._double_click(self.cell(table.tree, item, column))
                browser.assert_called_once_with(url)
                edit.assert_not_called()
        with patch('webbrowser.open_new_tab') as browser, patch.object(table, '_edit') as edit:
            table._double_click(self.cell(table.tree, item, 'name'))
            browser.assert_not_called()
            edit.assert_called_once_with(item)

    def test_link_hit_testing_ignores_headings_empty_space_and_indicators(self):
        table = self.show_assets()
        tree = table.tree
        event = self.cell(tree, self.item('MAIN'), 'ticker')
        links = {'ticker': lambda _item: quote_url('MAIN')}
        with patch('webbrowser.open_new_tab') as browser:
            self.assertFalse(open_cell_link(SimpleNamespace(x=event.x, y=2), tree, links))
            self.assertFalse(open_cell_link(SimpleNamespace(x=event.x, y=tree.winfo_height() - 5), tree, links))
            self.app.notebook.select(self.app.portfolio_page)
            self.root.update()
            view = self.app.real_allocations
            tree = view.tree
            stock = next(item for item in tree.get_children() if tree.item(item, 'text') == STOCK_MARKET)
            x, y, width, height = tree.bbox(stock, '#0')
            indicator = next(position for position in range(x, x + width)
                             if 'indicator' in tree.identify_element(position, y + height // 2))
            event = SimpleNamespace(x=indicator, y=y + height // 2)
            self.assertFalse(open_cell_link(event, tree, {'#0': lambda _item: quote_url('MAIN')}))
            with patch.object(view, '_edit_selected') as edit:
                view._double_click(event)
                edit.assert_not_called()
            browser.assert_not_called()

    def test_representative_and_home_security_cells_open_yahoo(self):
        self.app.notebook.select(self.app.classes_page)
        table = self.app.class_table
        item = table.tree.get_children()[0]
        with patch('webbrowser.open_new_tab') as browser, patch.object(table, '_edit') as edit:
            table._double_click(self.cell(table.tree, item, 'main_ticker'))
            browser.assert_called_once_with(quote_url('MAIN'))
            edit.assert_not_called()
        self.app.notebook.select(self.app.portfolio_page)
        view = self.app.real_allocations
        item = next(item for item, (table, source) in view._sources.items()
                    if table is self.app.holdings_table and table.row(source)['ticker'] == 'OLD')
        with patch('webbrowser.open_new_tab') as browser, patch.object(view, '_edit_selected') as edit:
            view._double_click(self.cell(view.tree, item, 'asset'))
            browser.assert_called_once_with(quote_url('OLD'))
            edit.assert_not_called()

    def test_plan_links_only_listed_security_transactions(self):
        plan = Plan((Trade('MAIN', 1, 10.004, 'EUR'),), {'MAIN': 2, 'OLD': 1, 'EMPTY': 0},
                    {'EUR': 9.996, 'USD': 10}, (Conversion('EUR', 'USD', 10, 10),), 139, 'EUR',
                    (ManualTrade(-10, 'EUR'),), (ManualHolding(90, 'EUR'),))
        view = self.app.plan_view
        view.show_plan(plan, self.portfolio, self.market)
        self.app.notebook.select(self.app.plan_page)
        tree = view.transactions
        for item in tree.get_children():
            with self.subTest(action=tree.set(item, 'action')), patch('webbrowser.open_new_tab') as browser:
                event = self.cell(tree, item, 'asset')
                view._double_click(event)
                if tree.set(item, 'unit') == 'shares':
                    browser.assert_called_once_with(quote_url('MAIN'))
                else:
                    browser.assert_not_called()

    def test_prices_use_quote_currency_without_changing_metadata_or_editable_fields(self):
        table = self.app.assets_table
        columns = tuple(table.tree['columns'])
        self.assertEqual(columns[columns.index('price') + 1], 'currency')
        self.assertEqual(table.tree.set(self.item('MAIN'), 'price'), '10.00')
        self.assertEqual(table.tree.set(self.item('MAIN'), 'currency'), 'EUR')
        self.assertEqual(table.tree.set(self.item('OLD'), 'price'), '10.00')
        self.assertEqual(table.tree.set(self.item('OLD'), 'currency'), 'USD')
        self.assertEqual(table.tree.set(self.item('EMPTY'), 'price'), '—')
        self.assertEqual(table.tree.set(self.item('EMPTY'), 'currency'), 'GBP')
        self.assertNotIn('price', [field for row in table.field_rows for field in row])
        self.assertEqual(self.app.portfolio(), self.portfolio)
        self.assertFalse(self.app.dirty)
        self.assertTrue(self.app.save())
        self.assertEqual(load_portfolio(self.directory), self.portfolio)

    def test_price_sort_uses_original_precision_and_reapplies_without_rebuilding_rows(self):
        table = self.app.assets_table
        before = table.get_rows()
        item = self.item('MAIN')
        table.tree.selection_set(item)
        table.tree.tk.call(table.tree.heading('price', 'command'))
        self.assertEqual([table.tree.set(item, 'ticker') for item in table.tree.get_children()],
                         ['OLD', 'MAIN', 'EMPTY'])
        table.set_market(MarketData({'MAIN': Quote(9.998, 'EUR'), 'OLD': Quote(10.001, 'USD')}, {'USD': .9}))
        self.assertEqual([table.tree.set(item, 'ticker') for item in table.tree.get_children()],
                         ['MAIN', 'OLD', 'EMPTY'])
        self.assertEqual(table.tree.selection(), (item,))
        self.assertEqual({row['ticker']: row for row in table.get_rows()},
                         {row['ticker']: row for row in before})
        table.show_holdings(before)
        self.assertEqual(table.tree.set(self.item('MAIN'), 'price'), '10.00')
        self.assertEqual(table.tree.set(self.item('OLD'), 'currency'), 'USD')

    def test_market_acceptance_updates_prices_without_dirtying_inputs_or_invalidating_plan(self):
        existing_plan = object()
        self.app.plan = existing_plan
        revision = self.app.revision
        updated = MarketData({'MAIN': Quote(1234.5678, 'EUR'), 'OLD': Quote(7, 'USD')}, {'USD': .9})
        self.app._accept_market(updated)
        self.assertEqual(self.app.assets_table.tree.set(self.item('MAIN'), 'price'), '1\u202f234.57')
        self.assertEqual(self.app.portfolio(), self.portfolio)
        self.assertEqual(self.app.revision, revision)
        self.assertIs(self.app.plan, existing_plan)
        self.assertFalse(self.app.dirty)

    def test_base_change_and_directory_reload_clear_previous_quotes(self):
        with patch.object(self.app, 'refresh_prices'):
            self.app.base_currency.set('USD')
        table = self.app.assets_table
        self.assertEqual(table.tree.set(self.item('MAIN'), 'price'), '—')
        self.assertEqual(table.tree.set(self.item('MAIN'), 'currency'), 'GBP')
        table.set_market(self.market)
        self.app._load_directory(self.directory)
        self.assertEqual(table.tree.set(self.item('MAIN'), 'price'), '—')
        self.assertEqual(table.tree.set(self.item('MAIN'), 'currency'), 'GBP')


if __name__ == '__main__':
    unittest.main()
