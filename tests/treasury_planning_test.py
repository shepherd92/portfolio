"""Mixed listed-share/manual-value allocation and independent ledger checks."""

from decimal import Decimal
import math
import unittest

from portfolio.models import CASH, STOCK_MARKET, TREASURY, Holding, ManualHolding, MarketData, Quote
from portfolio.planning import PlanningError, rebalance
from support import exhaustive_objectives, portfolio_fixture


def asset_values(portfolio, market, shares=None, manual=None):
    shares = shares if shares is not None else {h.ticker: h.shares for h in portfolio.holdings}
    manual = portfolio.manual_holdings if manual is None else manual
    groups = {(category, name): 0. for category, weights in portfolio.allocation.weights.items()
              for name in weights if category != CASH}
    for holding in portfolio.holdings:
        groups[STOCK_MARKET, holding.group] += market.price(holding.ticker) * shares[holding.ticker]
    for holding in manual:
        groups[TREASURY, holding.currency] = groups.get((TREASURY, holding.currency), 0) + market.value(holding.amount, holding.currency)
    return groups



class TreasuryPlanningTests(unittest.TestCase):
    def assert_accounting(self, portfolio, market, plan):
        """Rebuild the native-currency ledger solely from public plan outputs."""
        balances = dict(portfolio.cash)
        shares = {h.ticker: h.shares for h in portfolio.holdings}
        manual = {h.currency: Decimal(str(h.amount)) for h in portfolio.manual_holdings}
        for trade in plan.trades:
            self.assertIsInstance(trade.shares, int)
            shares[trade.ticker] += trade.shares
            balances[trade.currency] = balances.get(trade.currency, 0.) - trade.shares * trade.price
        self.assertEqual(shares, dict(plan.final_shares))
        for trade in plan.manual_trades:
            amount = Decimal(str(trade.amount))
            self.assertNotEqual(amount, 0)
            self.assertEqual(amount * 100, (amount * 100).to_integral_value())
            manual[trade.currency] += amount
            balances[trade.currency] = balances.get(trade.currency, 0.) - trade.amount
        final_manual = {h.currency: Decimal(str(h.amount)) for h in plan.final_manual_holdings}
        self.assertEqual(manual, final_manual)
        self.assertTrue(all(amount >= 0 for amount in final_manual.values()))
        self.assertEqual(len(plan.manual_trades), len({t.currency for t in plan.manual_trades}))
        for conversion in plan.conversions:
            self.assertAlmostEqual(market.value(conversion.from_amount, conversion.from_currency),
                                   market.value(conversion.to_amount, conversion.to_currency), places=7)
            balances[conversion.from_currency] = balances.get(conversion.from_currency, 0.) - conversion.from_amount
            balances[conversion.to_currency] = balances.get(conversion.to_currency, 0.) + conversion.to_amount
        for currency in balances.keys() | plan.cash.keys():
            self.assertAlmostEqual(balances.get(currency, 0.), plan.cash.get(currency, 0.), places=7)
            self.assertGreaterEqual(plan.cash.get(currency, 0.), 0.)
        opening = math.fsum(asset_values(portfolio, market).values())
        wealth = opening + math.fsum(market.value(v, c) for c, v in portfolio.cash.items())
        groups = asset_values(portfolio, market, shares, plan.final_manual_holdings)
        for group, value in groups.items():
            self.assertAlmostEqual(plan.allocation_values[group], value, places=7)
        self.assertAlmostEqual(plan.wealth, wealth, places=7)
        self.assertAlmostEqual(plan.cash_value, math.fsum(market.value(v, c) for c, v in plan.cash.items()), places=7)
        self.assertAlmostEqual(math.fsum(groups.values()) + plan.cash_value, wealth, places=7)
        self.assertAlmostEqual(sum(plan.allocation_values.values()), plan.wealth)

    def test_empty_manual_holding_receives_entered_cash_without_a_quote(self):
        portfolio = portfolio_fixture((), {TREASURY: 100}, {"EUR": 12.34}, (ManualHolding(0, "EUR"),))
        market = MarketData({}, {})
        plan = rebalance(portfolio, market)
        self.assertEqual(plan.final_shares, {})
        self.assertEqual(plan.trades, ())
        self.assertEqual(plan.final_manual_holdings[0].amount, 12.34)
        self.assertEqual(plan.manual_trades[0].amount, 12.34)
        self.assert_accounting(portfolio, market, plan)

    def test_huf_purchase_stops_at_executable_cent_and_converts_cash(self):
        portfolio = portfolio_fixture((), {TREASURY: 100}, {"EUR": 100}, (ManualHolding(0, "HUF"),))
        market = MarketData({}, {"HUF": 1 / 399.7654321})
        plan = rebalance(portfolio, market)
        self.assertEqual(plan.final_manual_holdings[0].amount, 39976.54)
        self.assertGreater(plan.cash_value, 0)
        self.assertLess(plan.cash_value, market.value(.01, "HUF"))
        self.assertTrue(plan.conversions)
        self.assert_accounting(portfolio, market, plan)

    def test_zero_wealth_and_zero_manual_values_remain_present(self):
        portfolio = portfolio_fixture((), {TREASURY: 100}, {}, (ManualHolding(0, "HUF"),))
        market = MarketData({}, {"HUF": .0025})
        for plan in (rebalance(portfolio, market), rebalance(portfolio, market, allow_selling=True)):
            self.assertEqual(plan.manual_trades, ())
            self.assertEqual(plan.final_manual_holdings, portfolio.manual_holdings)
            self.assert_accounting(portfolio, market, plan)

    def test_large_affordable_manual_balance_is_not_rounded_out_of_bounds(self):
        for amount in (10000000000000.02, 70368744177664.10):
            portfolio = portfolio_fixture((), {TREASURY: 100}, {}, (ManualHolding(amount, 'EUR'),))
            with self.subTest(amount=amount):
                plan = rebalance(portfolio, MarketData({}, {}))
                self.assertEqual(plan.manual_trades, ())
                self.assertEqual(plan.final_manual_holdings, portfolio.manual_holdings)
                self.assertEqual(plan.cash_value, 0)

    def test_eur_and_huf_values_are_retained_when_balanced(self):
        portfolio = portfolio_fixture((), {TREASURY: 100}, {},
                              (ManualHolding(100, "EUR"), ManualHolding(40000, "HUF")))
        market = MarketData({}, {"HUF": .0025})
        plan = rebalance(portfolio, market, allow_selling=True)
        self.assertEqual(plan.manual_trades, ())
        self.assertEqual(plan.final_manual_holdings, portfolio.manual_holdings)
        self.assert_accounting(portfolio, market, plan)

    def test_manual_sales_fund_listed_purchases_and_foreign_debt(self):
        portfolio = portfolio_fixture((Holding("ETF", "Stocks", 0),), {"Stocks": 100, TREASURY: 0}, {"USD": -10},
                              (ManualHolding(40000, "HUF"),))
        market = MarketData({"ETF": Quote(10, "EUR")}, {"HUF": .0025, "USD": 1})
        plan = rebalance(portfolio, market, allow_selling=True)
        self.assertEqual(plan.final_shares["ETF"], 9)
        self.assertEqual(plan.final_manual_holdings[0].amount, 0)
        self.assertEqual(plan.manual_trades[0].amount, -40000)
        self.assert_accounting(portfolio, market, plan)

    def test_manual_debt_requires_sale_permission(self):
        portfolio = portfolio_fixture((), {TREASURY: 100}, {"EUR": -10}, (ManualHolding(40000, "HUF"),))
        market = MarketData({}, {"HUF": .0025})
        with self.assertRaises(PlanningError):
            rebalance(portfolio, market)
        plan = rebalance(portfolio, market, allow_selling=True)
        self.assertEqual(plan.final_manual_holdings[0].amount, 36000)
        self.assert_accounting(portfolio, market, plan)

    def test_four_and_eight_million_huf_balances_reconcile_in_every_mode(self):
        market = MarketData({"ETF": Quote(113.7889, "USD")},
                                  {"HUF": 1 / 399.7654321, "USD": .913371})
        for amount in (4_000_000, 8_000_000):
            portfolio = portfolio_fixture((Holding("ETF", "Stocks", 111),), {"Stocks": 61.2, TREASURY: 38.8}, {"EUR": 49.23},
                                  (ManualHolding(amount, "HUF"),))
            for selling in (False, True):
                with self.subTest(amount=amount, selling=selling):
                    plan = rebalance(portfolio, market, allow_selling=selling)
                    self.assert_accounting(portfolio, market, plan)
                    for trade in plan.manual_trades:
                        self.assertTrue(selling or trade.amount > 0)


    def test_tight_objective_lock_with_large_huf_cent_count_is_feasible(self):
        portfolio = portfolio_fixture((Holding("ETF", "Stock", 805),), {"Stock": 60, TREASURY: 40}, {"EUR": 2732.5},
                              (ManualHolding(1306924.59, "HUF"),))
        market = MarketData({"ETF": Quote(23.954, "EUR")}, {"HUF": .00242443165857462})
        plan = rebalance(portfolio, market, allow_selling=True)
        self.assertEqual(plan.final_shares["ETF"], 631)
        self.assertEqual(plan.final_manual_holdings[0].amount, 4153157.01)
        self.assertGreaterEqual(plan.cash_value, 0)
        self.assertLess(plan.cash_value, market.value(.01, "HUF"))
        self.assert_accounting(portfolio, market, plan)


    def test_rebalance_matches_exhaustive_mixed_unit_optimum(self):
        fixtures = (
            (portfolio_fixture((Holding("ETF", "Stocks", 2),), {"Stocks": 60, TREASURY: 40}, {"EUR": .04},
                       (ManualHolding(.03, "EUR"),)), MarketData({"ETF": Quote(.03, "EUR")}, {})),
            (portfolio_fixture((Holding("ETF", "Stocks", 1), Holding("OLD", "Stocks", 1, False)),
                       {"Stocks": 25, TREASURY: 75}, {"EUR": .02}, (ManualHolding(.04, "HUF"),)),
             MarketData({"ETF": Quote(.03, "EUR"), "OLD": Quote(.02, "EUR")}, {"HUF": .5})),
        )
        for portfolio, market in fixtures:
            for selling in (False, True):
                with self.subTest(portfolio=portfolio, selling=selling):
                    plan = rebalance(portfolio, market, allow_selling=selling)
                    self.assertEqual(exhaustive_objectives(portfolio, market, selling, plan),
                                     exhaustive_objectives(portfolio, market, selling))
                    self.assert_accounting(portfolio, market, plan)


    def test_large_huf_balances_preserve_cash_target_with_tight_objective_locks(self):
        market = MarketData({"ETF": Quote(23.954, "EUR")}, {"HUF": .00242443165857462})
        for cash_target in (5, 10, 25, 50, 99):
            portfolio = portfolio_fixture((Holding("ETF", "Stocks", 805),),
                                  {"Stocks": (100 - cash_target) * .6,
                                   TREASURY: (100 - cash_target) * .4, CASH: cash_target},
                                  {"EUR": 2732.5}, (ManualHolding(1306924.59, "HUF"),))
            with self.subTest(cash_target=cash_target):
                plan = rebalance(portfolio, market, allow_selling=True)
                self.assertLess(abs(plan.cash_value - plan.wealth * cash_target / 100),
                                market.value(.01, "HUF"))
                self.assert_accounting(portfolio, market, plan)

    def test_missing_manual_exchange_rate_fails_clearly(self):
        portfolio = portfolio_fixture((), {TREASURY: 100}, {}, (ManualHolding(1, "HUF"),))
        with self.assertRaisesRegex(ValueError, "Missing exchange rate for HUF"):
            rebalance(portfolio, MarketData({}, {}))


if __name__ == "__main__":
    unittest.main()
