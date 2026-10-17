import unittest
from unittest.mock import Mock, patch

import pandas as pd

from portfolio.metadata import fetch_asset_details
from portfolio.market import MarketCache
from portfolio.models import AssetDetails


class MetadataTests(unittest.TestCase):
    def lookup(self, info, fee=pd.NA, *, symbol='VWCE.DE', fee_symbol=None):
        asset = Mock()
        asset.get_info.return_value = info
        asset.funds_data.fund_operations = pd.DataFrame(
            {fee_symbol or symbol: [fee], 'Category Average': [.01]},
            index=['Annual Report Expense Ratio'])
        with patch('yfinance.Ticker', return_value=asset) as constructor:
            result = fetch_asset_details(symbol)
        constructor.assert_called_once_with(symbol.upper())
        asset.get_isin.assert_not_called()
        return result

    def test_exact_listing_currency_not_fund_currency_and_fee_units(self):
        result = self.lookup({'symbol': 'VWCE.DE', 'longName': 'Vanguard All-World Acc',
                              'fullExchangeName': 'XETRA', 'exchange': 'GER',
                              'currency': 'EUR', 'financialCurrency': 'USD',
                              'quoteType': 'ETF', 'netExpenseRatio': .19}, .0019)
        self.assertEqual(result, AssetDetails(exchange='XETRA', currency='EUR', ter=.19,
                                               name='Vanguard All-World Acc'))

    def test_unknown_fee_is_not_zero_or_category_average(self):
        for fee in (pd.NA, None, float('nan'), float('inf'), -.01, True, '.0019', 2):
            with self.subTest(fee=fee):
                result = self.lookup({'longName': 'Fund', 'quoteType': 'ETF'}, fee)
                self.assertIsNone(result.ter)

    def test_zero_fee_is_preserved(self):
        result = self.lookup({'longName': 'Fund', 'quoteType': 'ETF'}, 0)
        self.assertEqual(result.ter, 0)

    def test_other_listing_fee_is_not_reused(self):
        result = self.lookup({'longName': 'Fund', 'quoteType': 'ETF'}, .0019, fee_symbol='VWRL.L')
        self.assertIsNone(result.ter)

    def test_fund_data_failure_preserves_listing_details(self):
        asset = Mock()
        asset.get_info.return_value = {'longName': 'Fund', 'quoteType': 'ETF'}
        asset.funds_data.fund_operations.loc.__getitem__ = Mock(side_effect=TimeoutError('offline'))
        with patch('yfinance.Ticker', return_value=asset):
            self.assertEqual(fetch_asset_details('VWCE.DE'), AssetDetails(name='Fund'))

    def test_listing_and_minor_unit_currency_normalization(self):
        result = self.lookup({'symbol': 'vwrl.l', 'currency': 'GBp', 'exchange': 'LSE'},
                             symbol='vwrl.l')
        self.assertEqual(result, AssetDetails(exchange='LSE', currency='GBP'))

    def test_only_explicit_valid_isin_is_accepted(self):
        for isin, expected in (('IE00BK5BQT80', 'IE00BK5BQT80'),
                               ('IE00BK5BQT81', ''), ('-', ''), (None, '')):
            with self.subTest(isin=isin):
                result = self.lookup({'longName': 'Fund', 'isin': isin})
                self.assertEqual(result.isin, expected)
                self.assertEqual(result.ibkr_name, '')

    def test_mismatched_symbol_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'instead of VWCE.DE'):
            self.lookup({'symbol': 'VWRL.L', 'longName': 'Different listing'})

    def test_missing_or_invalid_details_fail_clearly(self):
        for info in ({}, None, {'currency': 'bad currency', 'exchange': [],
                               'longName': {}, 'shortName': 'Truncated name'}):
            with self.subTest(info=info), self.assertRaisesRegex(ValueError, 'VWCE.DE'):
                self.lookup(info)

    def test_provider_failure_has_ticker_context(self):
        with patch('yfinance.Ticker', side_effect=TimeoutError('offline')):
            with self.assertRaisesRegex(ValueError, 'Could not look up VWCE.DE: offline'):
                fetch_asset_details('VWCE.DE')

    def test_session_cache_reuses_successful_partial_details_for_same_listing(self):
        cache = MarketCache()
        asset = Mock()
        asset.get_info.return_value = {'symbol': 'VWCE.DE', 'longName': 'Fund'}
        with patch('yfinance.Ticker', return_value=asset) as constructor:
            first = fetch_asset_details('vwce.de', cache=cache)
            second = fetch_asset_details('VWCE.DE', cache=cache)
        self.assertIs(first, second)
        self.assertEqual(second, AssetDetails(name='Fund'))
        constructor.assert_called_once_with('VWCE.DE')
        asset.get_info.assert_called_once_with()

    def test_failed_metadata_lookup_can_retry_with_same_cache(self):
        cache = MarketCache()
        asset = Mock()
        asset.get_info.side_effect = [{}, {'longName': 'Fund'}]
        with patch('yfinance.Ticker', return_value=asset) as constructor:
            with self.assertRaisesRegex(ValueError, 'No usable details'):
                fetch_asset_details('VWCE.DE', cache=cache)
            self.assertEqual(fetch_asset_details('VWCE.DE', cache=cache), AssetDetails(name='Fund'))
            fetch_asset_details('VWCE.DE', cache=cache)
        self.assertEqual(constructor.call_count, 2)
        self.assertEqual(asset.get_info.call_count, 2)
