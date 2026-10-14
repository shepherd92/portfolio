"""Only held or buyable instruments require market data; definitions persist."""

from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from portfolio.models import (AllocationTargets, CASH, STOCK_MARKET, TREASURY, Holding,
                    ManualHolding, MarketSnapshot, Portfolio, Quote)
from portfolio.planning import rebalance
from portfolio.storage import fetch_snapshot, load_portfolio, save_portfolio


def dormant_portfolio():
    allocation = AllocationTargets(
        {STOCK_MARKET: 100, TREASURY: 0, CASH: 0},
        {STOCK_MARKET: {'Active': 100, 'Retired': 0},
         TREASURY: {'EUR': 100, 'HUF': 0}, CASH: {'EUR': 100}})
    return Portfolio((Holding('ACTIVE', 'Active', 0), Holding('OLD', 'Active', 0, False),
                      Holding('DORMANT', 'Retired', 0)), allocation, {'EUR': 20},
                     (ManualHolding(0, 'EUR'), ManualHolding(0, 'HUF')))


class MarketCoverageTests(unittest.TestCase):
    def test_required_quotes_include_held_positions_and_buyable_main_only(self):
        original = dormant_portfolio()
        portfolio = Portfolio((*original.holdings, Holding('HELD', 'Retired', 2, False)),
                              original.allocation, original.cash, original.manual_holdings)
        self.assertEqual([holding.ticker for holding in portfolio.required_holdings], ['ACTIVE', 'HELD'])
        self.assertEqual(portfolio.required_manual_holdings, ())

    def test_plans_preserve_dormant_rows_without_their_quotes_or_rates(self):
        portfolio = dormant_portfolio()
        market = MarketSnapshot({'ACTIVE': Quote(10, 'EUR')}, {})
        for selling in (False, True):
            with self.subTest(selling=selling):
                plan = rebalance(portfolio, market, allow_selling=selling)
                self.assertEqual(plan.final_shares, {'ACTIVE': 2, 'DORMANT': 0, 'OLD': 0})
                self.assertEqual(plan.final_manual_holdings, portfolio.manual_holdings)
                self.assertEqual(plan.allocation_values[TREASURY, 'HUF'], 0)
                self.assertEqual(sum(plan.allocation_values.values()), 20)
                self.assertEqual([trade.ticker for trade in plan.trades], ['ACTIVE'])

    def test_zero_parent_disables_buying_despite_saved_child_preferences(self):
        allocation = AllocationTargets(
            {STOCK_MARKET: 0, TREASURY: 0, CASH: 100},
            {STOCK_MARKET: {'Retired': 100}, TREASURY: {'HUF': 100}, CASH: {'EUR': 100}})
        portfolio = Portfolio((Holding('DORMANT', 'Retired', 0),), allocation,
                              {'EUR': 20}, (ManualHolding(0, 'HUF'),))
        market = MarketSnapshot({}, {})
        market.validate_portfolio(portfolio)
        plan = rebalance(portfolio, market)
        self.assertEqual(plan.final_shares, {'DORMANT': 0})
        self.assertEqual(plan.final_manual_holdings, portfolio.manual_holdings)
        self.assertEqual(plan.cash_value, 20)

    def test_missing_quotes_and_rates_are_reported_together(self):
        original = dormant_portfolio()
        allocation = AllocationTargets(original.allocation.top,
                                       {**original.allocation.children, CASH: {'USD': 100}})
        portfolio = Portfolio((*original.holdings, Holding('HELD', 'Retired', 2, False)),
                              allocation, {'GBP': 0}, (ManualHolding(.01, 'HUF'),))
        market = MarketSnapshot({}, {})
        with self.assertRaises(ValueError) as error:
            market.validate_portfolio(portfolio)
        self.assertEqual(str(error.exception),
                         'Missing quotes for ACTIVE, HELD; Missing exchange rate for GBP, HUF, USD')
        with self.assertRaisesRegex(ValueError, 'ACTIVE, HELD'):
            rebalance(portfolio, market)

    def test_active_treasury_and_residual_cash_preferences_need_rates(self):
        allocation = AllocationTargets(
            {STOCK_MARKET: 0, TREASURY: 100, CASH: 0},
            {STOCK_MARKET: {}, TREASURY: {'HUF': 100}, CASH: {'USD': 100}})
        portfolio = Portfolio((), allocation, {'EUR': 1})
        self.assertEqual(portfolio.required_currencies({}), {'EUR', 'HUF', 'USD'})
        with self.assertRaisesRegex(ValueError, 'HUF, USD'):
            MarketSnapshot({}, {}).validate_portfolio(portfolio)

    def test_fetch_skips_dormant_instruments_and_their_unused_currencies(self):
        portfolio = dormant_portfolio()
        with patch('yfinance.Ticker') as ticker:
            ticker.return_value.fast_info = {'last_price': 10, 'currency': 'EUR'}
            market = fetch_snapshot(portfolio)
        ticker.assert_called_once_with('ACTIVE')
        market.validate_portfolio(portfolio)
        self.assertEqual(set(market.quotes), {'ACTIVE'})
        self.assertEqual(market.rates, {'EUR': 1})
        self.assertEqual(datetime.fromisoformat(market.fetched_at).utcoffset(), timedelta(0))

    def test_storage_round_trip_preserves_dormant_definitions(self):
        portfolio = dormant_portfolio()
        with tempfile.TemporaryDirectory() as directory:
            save_portfolio(portfolio, Path(directory))
            restored = load_portfolio(directory)
        self.assertEqual(restored, portfolio)
        self.assertEqual(restored.required_holdings, portfolio.required_holdings)
        plan = rebalance(restored, MarketSnapshot({'ACTIVE': Quote(10, 'EUR')}, {}))
        self.assertEqual(set(plan.final_shares), {'ACTIVE', 'DORMANT', 'OLD'})


if __name__ == '__main__':
    unittest.main()
