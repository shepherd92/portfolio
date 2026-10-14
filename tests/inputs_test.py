import importlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from portfolio.models import AllocationTargets, CASH, STOCK_MARKET, TREASURY, Holding, Portfolio
from main import main
from portfolio.storage import fetch_snapshot, load_portfolio, load_snapshot


class InputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.files = {
            'asset_group.csv': ('parent,asset_group,target_allocation\n,Stock market,100\n,MÁK,0\n,Cash,0\n'
                                'Stock market,stock,100\n'),
            'asset.csv': 'asset_group,ticker,is_main,shares,ibkr_id\nstock,A,True,4,unused\n',
            'cash.csv': 'currency,amount\nEUR,20\n',
        }
        self.write_files()
        self.snapshot = self.directory / 'snapshot.json'
        self.snapshot.write_text(json.dumps({'base_currency': 'EUR', 'quotes': {
            'A': {'price': 10, 'currency': 'EUR'}}, 'rates': {'EUR': 1}}))

    def write_files(self):
        for name, text in self.files.items():
            (self.directory / name).write_text(text)

    def test_csv_and_snapshot(self):
        portfolio = load_portfolio(self.directory)
        self.assertEqual(portfolio.holdings, (Holding('A', 'stock', 4),))
        self.assertEqual(portfolio.cash, {'EUR': 20})
        self.assertEqual(portfolio.allocation.top[STOCK_MARKET], 100)
        self.assertEqual(portfolio.allocation.children[STOCK_MARKET], {'stock': 100})
        self.assertEqual(portfolio.manual_holdings, ())
        self.assertEqual(load_snapshot(self.snapshot).price('A'), 10)

    def test_invalid_csv(self):
        cases = [
            ('asset.csv', 'stock,A,False,4,unused\n'),
            ('asset.csv', 'stock,A,yes,4,unused\n'),
            ('asset.csv', 'stock,A,True,4.5,unused\n'),
            ('asset.csv', 'stock,A,True,-1,unused\n'),
            ('asset.csv', 'unknown,A,True,4,unused\n'),
            ('asset.csv', 'stock,A,True,4,unused\nstock,A,False,1,unused\n'),
            ('asset.csv', 'stock,A,True,4,unused\nstock,B,True,1,unused\n'),
            ('asset_group.csv', ',Stock market,100\n,MÁK,0\n,Cash,0\nStock market,stock,100\nStock market,stock,0\n'),
            ('asset_group.csv', ',Stock market,90\n,MÁK,0\n,Cash,0\nStock market,stock,100\n'),
            ('asset_group.csv', ',Stock market,100\n,MÁK,0\n,Cash,0\nStock market,stock,nan\n'),
            ('asset_group.csv', ',Stock market,100\n,MÁK,0\n,Cash,0\nStock market,stock,1e308\n'),
            ('cash.csv', 'EUR,20\neur,30\n'),
            ('cash.csv', 'EUR,inf\n'),
        ]
        for name, rows in cases:
            with self.subTest(name=name, rows=rows):
                original = self.files[name]
                self.files[name] = original.split('\n', 1)[0] + '\n' + rows
                self.write_files()
                with self.assertRaises(ValueError):
                    load_portfolio(self.directory)
                self.files[name] = original
        self.write_files()

    def test_duplicate_snapshot_keys(self):
        self.snapshot.write_text('{"base_currency":"EUR", "base_currency":"USD"}')
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            load_snapshot(self.snapshot)

    def test_malformed_quote_currency_has_validation_error(self):
        for currency in ([], {}, None, 123):
            self.snapshot.write_text(json.dumps({'base_currency': 'EUR', 'rates': {},
                'quotes': {'A': {'price': 10, 'currency': currency}}}))
            with self.subTest(currency=currency), self.assertRaisesRegex(ValueError, 'Invalid currency'):
                load_snapshot(self.snapshot)

    def test_fetches_once_per_ticker_and_currency(self):
        portfolio = Portfolio((Holding('A', 'stock', 1), Holding('B', 'stock', 1, False)),
                              AllocationTargets({STOCK_MARKET: 100, TREASURY: 0, CASH: 0},
                                                {STOCK_MARKET: {'stock': 100}, TREASURY: {}, CASH: {}}),
                              {'USD': 20, 'EUR': 0})
        calls = []

        class Ticker:
            def __init__(self, symbol):
                calls.append(symbol)
                self.fast_info = {'last_price': .9} if symbol == 'USDEUR=X' else {
                    'last_price': 10, 'currency': 'USD'}

        with patch('yfinance.Ticker', Ticker):
            market = fetch_snapshot(portfolio)
        self.assertEqual(calls, ['A', 'B', 'USDEUR=X'])
        self.assertEqual(market.price('B'), 9)

    def test_cli_operations(self):
        for operation in (['rebalance'], ['rebalance', '--allow-selling']):
            with self.subTest(operation=operation), patch('sys.stdout', new_callable=io.StringIO) as output:
                status = main(['--config', str(self.directory), '--snapshot', str(self.snapshot), *operation])
                self.assertEqual(status, 0)
                self.assertIn('Planned wealth:', output.getvalue())

    def test_no_command_launches_gui_with_selected_inputs(self):
        with patch('ui.app.launch', return_value=0) as launch:
            result = main(['--config', str(self.directory), '--snapshot', str(self.snapshot),
                           '--base-currency', 'USD'])
        self.assertEqual(result, 0)
        launch.assert_called_once_with(self.directory, base_currency='USD', snapshot_path=self.snapshot)

    def test_cli_error_and_import_side_effects(self):
        with patch('sys.stderr', new_callable=io.StringIO), self.assertRaises(SystemExit) as error:
            main(['--config', str(self.directory), '--snapshot', str(self.snapshot),
                  '--base-currency', 'USD', 'rebalance'])
        self.assertEqual(error.exception.code, 2)
        with patch('portfolio.storage.fetch_snapshot', side_effect=AssertionError('network')):
            importlib.import_module('main')


if __name__ == '__main__':
    unittest.main()
