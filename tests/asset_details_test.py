"""Optional instrument metadata validates and persists without affecting valuation."""

import csv
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from portfolio.models import AllocationTargets, AssetDetails, CASH, STOCK_MARKET, TREASURY, Holding, Portfolio
from portfolio.storage import config_signature, fetch_market_data, load_portfolio, save_portfolio


class AssetDetailsTests(unittest.TestCase):
    def test_optional_details_are_normalized_and_immutable(self):
        details = AssetDetails(isin=' ie00b4l5y983 ', exchange=' XETRA ', currency=' eur ',
                               ter=.19, name=' Global, fund ', ibkr_name=' BROKER FUND ')
        self.assertEqual(details.isin, 'IE00B4L5Y983')
        self.assertEqual(details.exchange, 'XETRA')
        self.assertEqual(details.currency, 'EUR')
        self.assertEqual(details.name, 'Global, fund')
        self.assertEqual(details.ibkr_name, 'BROKER FUND')
        self.assertEqual(details.ter, .19)
        with self.assertRaises(FrozenInstanceError):
            details.name = 'Changed'
        self.assertEqual(Holding('A', 'Stocks', 0).details, AssetDetails())
        with self.assertRaisesRegex(ValueError, 'AssetDetails'):
            Holding('A', 'Stocks', 0, details={})

    def test_isin_checks_structure_and_checksum(self):
        for value in ('US0378331005', 'IE00B4L5Y983', 'IE00BK5BQT80'):
            with self.subTest(value=value):
                self.assertEqual(AssetDetails(isin=value).isin, value)
        for value in ('US0378331004', 'IE00B4L5Y98', 'IE00B4L5Y9830', '0000B4L5Y983',
                      'IE00B4L5Y98X', 'IE00 B4LY983', 'ＩＥ00B4L5Y983'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'ISIN'):
                AssetDetails(isin=value)

    def test_metadata_types_currency_and_ter_are_validated(self):
        for name in ('isin', 'exchange', 'currency', 'name', 'ibkr_name'):
            for value in (None, 12, []):
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    AssetDetails(**{name: value})
        for value in ('EU', 'EURO', '12X', '€UR'):
            with self.subTest(currency=value), self.assertRaisesRegex(ValueError, 'currency'):
                AssetDetails(currency=value)
        for value in (-1, 101, float('nan'), float('inf'), True, '.19'):
            with self.subTest(ter=value), self.assertRaisesRegex(ValueError, 'TER'):
                AssetDetails(ter=value)
        for value in (None, 0, .19, 100):
            self.assertEqual(AssetDetails(ter=value).ter, value)


class AssetDetailsStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.asset_path = self.directory / 'asset.csv'
        allocation = AllocationTargets({STOCK_MARKET: {'Stocks': 100}, TREASURY: {}, CASH: {}})
        self.portfolio = Portfolio((Holding('A.L', 'Stocks', 2),), allocation, {'EUR': 20})
        save_portfolio(self.portfolio, self.directory)

    def test_metadata_is_optional_on_load_and_added_on_save(self):
        self.asset_path.write_text('asset_group,ticker,is_main,shares,note\nStocks,A.L,True,2,Keep\n')
        original = self.asset_path.read_bytes()
        loaded = load_portfolio(self.directory)
        self.assertEqual(loaded, self.portfolio)
        self.assertEqual(self.asset_path.read_bytes(), original)
        save_portfolio(loaded, self.directory)
        with self.asset_path.open() as stream:
            row = next(csv.DictReader(stream))
        self.assertEqual(row['note'], 'Keep')
        self.assertTrue(all(row[name] == '' for name in ('isin', 'exchange', 'currency', 'ter', 'name', 'ibkr_name')))
        self.assertEqual(load_portfolio(self.directory), loaded)

    def test_existing_metadata_round_trips_with_arbitrary_extra_columns(self):
        self.asset_path.write_text(
            'ticker,name,asset_group,isin,is_main,shares,ter,ibkr_id,exchange,currency,ibkr_name,note\n'
            'A.L,"Global, fund",Stocks,IE00B4L5Y983,True,2,0.19,123,LSE,GBP,BROKER FUND,Keep\n')
        loaded = load_portfolio(self.directory)
        details = AssetDetails('IE00B4L5Y983', 'LSE', 'GBP', .19, 'Global, fund', 'BROKER FUND')
        self.assertEqual(loaded.holdings[0].details, details)
        updated = replace(loaded, holdings=(replace(loaded.holdings[0], shares=3,
                                                   details=replace(details, ter=0, name='Updated fund')),))
        save_portfolio(updated, self.directory)
        self.assertEqual(load_portfolio(self.directory), updated)
        with self.asset_path.open() as stream:
            row = next(csv.DictReader(stream))
        self.assertEqual(list(row), ['ticker', 'name', 'asset_group', 'isin', 'is_main', 'shares',
                                     'ter', 'ibkr_id', 'exchange', 'currency', 'ibkr_name', 'note'])
        self.assertEqual(row['ibkr_id'], '123')
        self.assertEqual(row['note'], 'Keep')
        self.assertEqual(row['ter'], '0.0')
        cleared = replace(updated, holdings=(replace(updated.holdings[0], details=AssetDetails()),))
        save_portfolio(cleared, self.directory)
        self.assertEqual(load_portfolio(self.directory), cleared)

    def test_partial_metadata_columns_are_read_and_missing_columns_added(self):
        self.asset_path.write_text('asset_group,ticker,is_main,shares,name\nStocks,A.L,True,2,Fund name\n')
        loaded = load_portfolio(self.directory)
        self.assertEqual(loaded.holdings[0].details, AssetDetails(name='Fund name'))
        save_portfolio(loaded, self.directory)
        self.assertEqual(load_portfolio(self.directory), loaded)

    def test_invalid_saved_metadata_does_not_write_any_files(self):
        for column, value in (('isin', 'US0378331004'), ('ter', 'nan'), ('ter', '101'),
                              ('ter', 'not a number'), ('currency', 'EURO')):
            self.asset_path.write_text(f'asset_group,ticker,is_main,shares,{column}\nStocks,A.L,True,2,{value}\n')
            original = {path: path.read_bytes() for path in self.directory.iterdir()}
            with self.subTest(column=column, value=value), self.assertRaises(ValueError):
                load_portfolio(self.directory)
            self.assertEqual({path: path.read_bytes() for path in self.directory.iterdir()}, original)

    def test_external_metadata_change_invalidates_save_signature(self):
        signature = config_signature(self.directory)
        self.asset_path.write_text('asset_group,ticker,is_main,shares,name\nStocks,A.L,True,2,Edited externally\n')
        original = self.asset_path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'changed outside'):
            save_portfolio(self.portfolio, self.directory, signature)
        self.assertEqual(self.asset_path.read_bytes(), original)

    def test_metadata_never_overrides_market_quote_currency(self):
        details = AssetDetails(isin='IE00B4L5Y983', exchange='XETRA', currency='USD', ter=.19)
        portfolio = replace(self.portfolio, holdings=(replace(self.portfolio.holdings[0], details=details),))
        ticker = Mock(side_effect=lambda symbol: SimpleNamespace(fast_info={
            'A.L': {'last_price': 1234, 'currency': 'GBp'},
            'GBPEUR=X': {'last_price': 1.2},
        }[symbol]))
        with patch.dict('sys.modules', {'yfinance': SimpleNamespace(Ticker=ticker)}):
            market = fetch_market_data(portfolio)
        self.assertEqual([call.args[0] for call in ticker.call_args_list], ['A.L', 'GBPEUR=X'])
        self.assertEqual(market.quotes['A.L'].currency, 'GBP')
        self.assertAlmostEqual(market.price('A.L'), 14.808)
        self.assertEqual(portfolio.holdings[0].details, details)


if __name__ == '__main__':
    unittest.main()
