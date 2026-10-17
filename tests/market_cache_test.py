"""Session caching reuses fresh inputs without concealing their original age."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from portfolio.market import MarketCache, fetch_base_rate, fetch_exchange_rate, fetch_market_data
from portfolio.models import AllocationTargets, CASH, STOCK_MARKET, TREASURY, Holding, Portfolio


class MarketCacheTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
        self.clock = patch('portfolio.market.datetime').start()
        self.addCleanup(patch.stopall)
        self.clock.now.return_value = self.now
        self.info = {
            'MAIN': {'last_price': 10, 'currency': 'USD'},
            'NEW': {'last_price': 20, 'currency': 'GBP'},
            'USDEUR=X': {'last_price': .8},
            'GBPEUR=X': {'last_price': 1.2},
            'HUFEUR=X': {'last_price': .0025},
        }
        self.ticker = Mock(side_effect=lambda symbol: SimpleNamespace(fast_info=self.info[symbol]))
        patch.dict('sys.modules', {'yfinance': SimpleNamespace(Ticker=self.ticker)}).start()
        self.cache = MarketCache()
        self.portfolio = Portfolio(
            (Holding('MAIN', 'Stocks', 2),),
            AllocationTargets({STOCK_MARKET: {'Stocks': 100}, TREASURY: {}, CASH: {}}),
            {'EUR': 10})

    def test_consumers_and_display_currency_changes_reuse_native_quotes_and_fx(self):
        first = fetch_market_data(self.portfolio, extra_currencies=('HUF',), cache=self.cache)
        self.clock.now.return_value = self.now + timedelta(seconds=100)
        changed = replace(self.portfolio, cash={'EUR': 100})
        second = fetch_market_data(changed, 'USD', extra_currencies=('HUF',), cache=self.cache)
        third = fetch_market_data(changed, 'HUF', extra_currencies=('HUF',), cache=self.cache)
        self.assertCountEqual(self.ticker.call_args_list,
                              [call('MAIN'), call('USDEUR=X'), call('HUFEUR=X')])
        self.assertIs(first.quotes['MAIN'], second.quotes['MAIN'])
        self.assertEqual(second.rates['EUR'], 1.25)
        self.assertAlmostEqual(second.rates['HUF'], .003125)
        self.assertEqual(third.rates['USD'], 320)
        self.assertEqual(first.fetched_at, second.fetched_at)
        self.assertEqual(first.fetched_at, third.fetched_at)

    def test_added_ticker_and_currency_only_fetch_missing_inputs(self):
        first = fetch_market_data(self.portfolio, cache=self.cache)
        self.ticker.reset_mock()
        self.clock.now.return_value = self.now + timedelta(seconds=100)
        expanded = replace(self.portfolio, holdings=(*self.portfolio.holdings,
                                                     Holding('NEW', 'Stocks', 1, False)))
        second = fetch_market_data(expanded, extra_currencies=('HUF',), cache=self.cache)
        self.assertCountEqual(self.ticker.call_args_list,
                              [call('NEW'), call('GBPEUR=X'), call('HUFEUR=X')])
        self.assertEqual(second.fetched_at, first.fetched_at)
        second.validate_portfolio(expanded)

    def test_expiry_is_per_input_and_exactly_fifteen_minutes(self):
        fetch_market_data(self.portfolio, cache=self.cache)
        self.clock.now.return_value = self.now + timedelta(seconds=100)
        fetch_market_data(self.portfolio, extra_currencies=('HUF',), cache=self.cache)
        self.ticker.reset_mock()
        self.clock.now.return_value = self.now + timedelta(seconds=899)
        fetch_market_data(self.portfolio, extra_currencies=('HUF',), cache=self.cache)
        self.ticker.assert_not_called()
        self.clock.now.return_value = self.now + timedelta(seconds=900)
        market = fetch_market_data(self.portfolio, extra_currencies=('HUF',), cache=self.cache)
        self.assertCountEqual(self.ticker.call_args_list, [call('MAIN'), call('USDEUR=X')])
        self.assertEqual(market.fetched_at, self.now + timedelta(seconds=100))

    def test_failed_request_is_retried_but_successful_partial_inputs_survive(self):
        self.info.pop('USDEUR=X')
        with self.assertRaisesRegex(ValueError, 'USD/EUR'):
            fetch_market_data(self.portfolio, cache=self.cache)
        self.info['USDEUR=X'] = {'last_price': .8}
        market = fetch_market_data(self.portfolio, cache=self.cache)
        self.assertEqual(self.ticker.call_args_list,
                         [call('MAIN'), call('USDEUR=X'), call('USDEUR=X')])
        market.validate_portfolio(self.portfolio)

    def test_expired_failed_refresh_does_not_return_a_stale_fallback(self):
        fetch_market_data(self.portfolio, cache=self.cache)
        self.clock.now.return_value = self.now + timedelta(seconds=900)
        self.info['MAIN']['last_price'] = 0
        with self.assertRaisesRegex(ValueError, 'Could not price MAIN'):
            fetch_market_data(self.portfolio, cache=self.cache)
        self.info['MAIN']['last_price'] = 12
        self.assertEqual(fetch_market_data(self.portfolio, cache=self.cache).quotes['MAIN'].price, 12)

    def test_inverse_and_cross_rates_reuse_the_same_pivot_requests(self):
        self.assertEqual(fetch_exchange_rate('USD', 'EUR', cache=self.cache), .8)
        self.assertEqual(fetch_exchange_rate('EUR', 'USD', cache=self.cache), 1.25)
        self.assertAlmostEqual(fetch_exchange_rate('USD', 'GBP', cache=self.cache), 2 / 3)
        self.assertEqual(fetch_exchange_rate('GBP', 'GBP', cache=self.cache), 1)
        self.assertEqual(self.ticker.call_args_list, [call('USDEUR=X'), call('GBPEUR=X')])

    def test_overlapping_workers_share_an_inflight_success(self):
        started, release, second_started = Event(), Event(), Event()
        original = self.ticker.side_effect

        def fetch(symbol):
            if symbol == 'MAIN':
                started.set()
                if not release.wait(2):
                    raise RuntimeError('Request was not released')
            return original(symbol)

        def overlapping():
            second_started.set()
            return fetch_market_data(self.portfolio, cache=self.cache)

        self.ticker.side_effect = fetch
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(fetch_market_data, self.portfolio, cache=self.cache)
            try:
                self.assertTrue(started.wait(2))
                second = pool.submit(overlapping)
                self.assertTrue(second_started.wait(2))
            finally:
                release.set()
            self.assertEqual(first.result(timeout=2), second.result(timeout=2))
        self.assertEqual(self.ticker.call_args_list, [call('MAIN'), call('USDEUR=X')])

    def test_base_rate_reuses_success_expires_and_retries_errors(self):
        html = ('<table class="datatable"><tr><td>2026. július 22., szerda</td>'
                '<td>5,75%</td></tr></table>').encode('utf-8')
        with patch('portfolio.market.urlopen') as request:
            request.return_value.__enter__.return_value.read.return_value = html
            first = fetch_base_rate(cache=self.cache)
            self.assertEqual(first.fetched_at, self.now)
            self.clock.now.return_value = self.now + timedelta(seconds=899)
            self.assertIs(fetch_base_rate(cache=self.cache), first)
            self.assertEqual(first.fetched_at, self.now)
            self.assertEqual(request.call_count, 1)
            self.clock.now.return_value = self.now + timedelta(seconds=900)
            request.side_effect = OSError('Offline')
            with self.assertRaisesRegex(ValueError, 'Offline'):
                fetch_base_rate(cache=self.cache)
            request.side_effect = None
            self.assertEqual(fetch_base_rate(cache=self.cache), first)
            self.assertEqual(request.call_count, 3)

    def test_separate_sessions_do_not_share_cache_entries(self):
        fetch_market_data(self.portfolio, cache=self.cache)
        fetch_market_data(self.portfolio, cache=MarketCache())
        self.assertEqual(self.ticker.call_args_list,
                         [call('MAIN'), call('USDEUR=X'), call('MAIN'), call('USDEUR=X')])


if __name__ == '__main__':
    unittest.main()
