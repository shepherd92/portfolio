from datetime import date, timedelta
import importlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from portfolio.models import AllocationTargets, CASH, STOCK_MARKET, TREASURY, Holding, MarketData, Portfolio, Quote
from main import main
from portfolio.market import BaseRate, fetch_market_data
from portfolio.planning import rebalance
from portfolio.storage import load_portfolio, save_settings
from portfolio.calculator import CalculatorSettings, CashFlow, ReturnAssumptions


class InputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.files = {
            'asset_group.csv': ('parent,asset_group,portfolio_allocation,category\nStock market,stock,100,Equities\n'),
            'asset.csv': 'asset_group,ticker,is_main,shares,ibkr_id\nstock,A,True,4,unused\n',
            'cash.csv': 'currency,amount\nEUR,20\n',
        }
        self.write_files()
        rate = patch('main.fetch_base_rate', return_value=BaseRate(5, date(2026, 1, 1)))
        rate.start()
        self.addCleanup(rate.stop)
        self.market = MarketData({'A': Quote(10, 'EUR')}, {'EUR': 1, 'HUF': .0025})

    def write_files(self):
        for name, text in self.files.items():
            (self.directory / name).write_text(text)

    def test_loads_portfolio_csvs(self):
        portfolio = load_portfolio(self.directory)
        self.assertEqual(portfolio.holdings, (Holding('A', 'stock', 4),))
        self.assertEqual(portfolio.cash, {'EUR': 20})
        self.assertEqual(portfolio.allocation.totals[STOCK_MARKET], 100)
        self.assertEqual(portfolio.allocation.weights[STOCK_MARKET], {'stock': 100})
        self.assertEqual(portfolio.manual_holdings, ())

    def test_invalid_csv(self):
        cases = [
            ('asset.csv', 'stock,A,False,4,unused\n'),
            ('asset.csv', 'stock,A,yes,4,unused\n'),
            ('asset.csv', 'stock,A,True,4.5,unused\n'),
            ('asset.csv', 'stock,A,True,-1,unused\n'),
            ('asset.csv', 'unknown,A,True,4,unused\n'),
            ('asset.csv', 'stock,A,True,4,unused\nstock,A,False,1,unused\n'),
            ('asset.csv', 'stock,A,True,4,unused\nstock,B,True,1,unused\n'),
            ('asset_group.csv', 'Stock market,stock,100\nStock market,stock,0\n'),
            ('asset_group.csv', 'Stock market,stock,90\n'),
            ('asset_group.csv', 'Stock market,stock,nan\n'),
            ('asset_group.csv', 'Stock market,stock,1e308\n'),
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

    def test_fetches_once_per_ticker_and_currency(self):
        portfolio = Portfolio((Holding('A', 'stock', 1), Holding('B', 'stock', 1, False)),
                              AllocationTargets({STOCK_MARKET: {'stock': 100}, TREASURY: {}, CASH: {}}),
                              {'USD': 20, 'EUR': 0})
        calls = []

        class Ticker:
            def __init__(self, symbol):
                calls.append(symbol)
                self.fast_info = {'last_price': .9} if symbol == 'USDEUR=X' else {
                    'last_price': 10, 'currency': 'USD'}

        with patch('yfinance.Ticker', Ticker):
            market = fetch_market_data(portfolio)
        self.assertEqual(calls, ['A', 'B', 'USDEUR=X'])
        self.assertEqual(market.price('B'), 9)

    def test_cli_operations(self):
        for operation in (['rebalance'], ['rebalance', '--allow-selling']):
            with (self.subTest(operation=operation),
                  patch('main.fetch_market_data', return_value=self.market) as fetch,
                  patch('sys.stdout', new_callable=io.StringIO) as output):
                status = main(['--config', str(self.directory), *operation])
                self.assertEqual(status, 0)
                self.assertIn('Planned wealth:', output.getvalue())
                fetch.assert_called_once_with(load_portfolio(self.directory), 'EUR', extra_currencies=('HUF',))

    def test_cli_combines_saved_currency_reserves_with_actual_wealth(self):
        today = date.today()
        settings = CalculatorSettings(outgoings=(
            CashFlow('Rent', .5, today, 'EUR', 'monthly'),
            CashFlow('Food', 200, today, frequency='monthly'),
            CashFlow('Purchase', 1, today + timedelta(days=100), 'GBP')),
            incomes=(CashFlow('Income', 5, today, 'USD', 'monthly'),),
            returns=ReturnAssumptions(mak_huf=0), cash_months=4)
        save_settings(settings, self.directory, None)
        market = MarketData(self.market.quotes, {'HUF': .0025, 'USD': .9, 'GBP': 1.2})
        before = {path.name: path.read_bytes() for path in self.directory.iterdir()}
        with (patch('main.fetch_market_data', return_value=market) as fetch,
              patch('main.rebalance', wraps=rebalance) as solve,
              patch('sys.stdout', new_callable=io.StringIO) as output):
            self.assertEqual(main(['--config', str(self.directory), 'rebalance']), 0)
        fetch.assert_called_once_with(load_portfolio(self.directory), 'EUR',
                                      extra_currencies=('EUR', 'GBP', 'HUF', 'USD'))
        self.assertIn('Planned wealth: 60 EUR', output.getvalue())
        self.assertIn('HUF MÁK', output.getvalue())
        reserves = solve.call_args.kwargs['minimum_values']
        self.assertAlmostEqual(reserves[(CASH, 'HUF')], 5.2)
        self.assertAlmostEqual(reserves[(TREASURY, 'HUF')], 6)
        self.assertEqual({path.name: path.read_bytes() for path in self.directory.iterdir()}, before)

    def test_no_command_launches_gui_with_selected_inputs(self):
        with patch('ui.app.launch', return_value=0) as launch:
            result = main(['--config', str(self.directory), '--base-currency', 'USD'])
        self.assertEqual(result, 0)
        launch.assert_called_once_with(self.directory, base_currency='USD')

    def test_cli_reports_market_data_errors(self):
        with (patch('main.fetch_market_data', side_effect=ValueError('Could not price A')),
              patch('sys.stderr', new_callable=io.StringIO) as output,
              self.assertRaises(SystemExit) as error):
            main(['--config', str(self.directory), 'rebalance'])
        self.assertEqual(error.exception.code, 2)
        self.assertIn('Could not price A', output.getvalue())

    def test_import_does_not_fetch_market_data(self):
        with patch('yfinance.Ticker', side_effect=AssertionError('network')):
            importlib.reload(importlib.import_module('main'))


if __name__ == '__main__':
    unittest.main()
