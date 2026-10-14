"""Cash is an allocation target; tiny exact enumerations provide an independent oracle."""

import random
import unittest

from portfolio.models import CASH, STOCK_MARKET, TREASURY, Holding, ManualHolding, MarketSnapshot, Quote
from portfolio.planning import PlanningError, rebalance
from support import exhaustive_objectives, portfolio_fixture


class CashPlanningTests(unittest.TestCase):
    def test_zero_target_classes_never_receive_purchases(self):
        portfolio = portfolio_fixture((Holding('ETF', 'Stocks', 0), Holding('EXTRA', 'Other', 0)),
                              {'Stocks': 100, 'Other': 0, TREASURY: 0}, {'EUR': 50},
                              (ManualHolding(0, 'EUR'),))
        snapshot = MarketSnapshot({'ETF': Quote(100, 'EUR'), 'EXTRA': Quote(1, 'EUR')}, {})
        for selling in (False, True):
            with self.subTest(selling=selling):
                plan = rebalance(portfolio, snapshot, allow_selling=selling)
                self.assertEqual(plan.trades, ())
                self.assertEqual(plan.manual_trades, ())
                self.assertEqual(plan.cash_value, 50)

    def test_cash_is_reserved_for_currency_balances(self):
        with self.assertRaisesRegex(ValueError, 'reserved'):
            Holding('ETF', CASH, 0)
        with self.assertRaisesRegex(ValueError, 'sum to 100'):
            portfolio_fixture((Holding('ETF', 'Stocks', 0),), {'Stocks': 100, CASH: 10}, {})

    def test_purchases_leave_requested_cash_reserve(self):
        portfolio = portfolio_fixture((Holding('ETF', 'Stocks', 0),), {'Stocks': 60, CASH: 40}, {'EUR': 100})
        plan = rebalance(portfolio, MarketSnapshot({'ETF': Quote(10, 'EUR')}, {}))
        self.assertEqual(plan.final_shares['ETF'], 6)
        self.assertEqual(plan.allocation_values, {(STOCK_MARKET, 'Stocks'): 60, (CASH, 'EUR'): 40})
        self.assertEqual(sum(plan.allocation_values.values()), plan.wealth)

    def test_cash_target_uses_nearest_executable_allocation(self):
        snapshot = MarketSnapshot({'ETF': Quote(10, 'EUR')}, {})
        for cash_target, expected in ((44, 6), (46, 5)):
            portfolio = portfolio_fixture((Holding('ETF', 'Stocks', 10),),
                                  {'Stocks': 100 - cash_target, CASH: cash_target}, {})
            with self.subTest(target=cash_target):
                plan = rebalance(portfolio, snapshot, allow_selling=True)
                self.assertEqual(plan.final_shares['ETF'], expected)
                self.assertEqual(exhaustive_objectives(portfolio, snapshot, True, plan),
                                 exhaustive_objectives(portfolio, snapshot, True))

    def test_equal_allocation_error_prefers_cash_target_then_turnover(self):
        portfolio = portfolio_fixture((Holding('A', 'A', 6), Holding('B', 'B', 0)),
                              {'A': 20, 'B': 30, CASH: 50}, {'EUR': 4})
        snapshot = MarketSnapshot({'A': Quote(1, 'EUR'), 'B': Quote(4, 'EUR')}, {})
        plan = rebalance(portfolio, snapshot, allow_selling=True)
        self.assertEqual(dict(plan.final_shares), {'A': 1, 'B': 1})
        self.assertEqual(plan.cash_value, 5)
        tied = portfolio_fixture((Holding('ETF', 'Stocks', 1),), {'Stocks': 50, CASH: 50}, {})
        plan = rebalance(tied, MarketSnapshot({'ETF': Quote(10, 'EUR')}, {}), allow_selling=True)
        self.assertEqual(plan.trades, ())

    def test_full_cash_target_sells_main_nonmain_and_manual_holdings(self):
        portfolio = portfolio_fixture((Holding('ETF', 'Stocks', 2), Holding('OLD', 'Stocks', 3, False)),
                              {'Stocks': 0, TREASURY: 0, CASH: 100}, {'EUR': -2},
                              (ManualHolding(4000, 'HUF'), ManualHolding(1.23, 'EUR')))
        snapshot = MarketSnapshot({'ETF': Quote(10, 'EUR'), 'OLD': Quote(2, 'USD')},
                                  {'USD': .5, 'HUF': .0025})
        plan = rebalance(portfolio, snapshot, allow_selling=True)
        self.assertEqual(set(plan.final_shares.values()), {0})
        self.assertTrue(all(h.amount == 0 for h in plan.final_manual_holdings))
        self.assertAlmostEqual(plan.cash_value, 32.23)
        self.assertAlmostEqual(plan.cash_value, plan.wealth)
        self.assertTrue(all(value >= 0 for value in plan.cash.values()))

    def test_no_selling_cannot_raise_cash_but_does_not_buy_against_full_cash_target(self):
        portfolio = portfolio_fixture((Holding('ETF', 'Stocks', 2),), {'Stocks': 0, CASH: 100}, {'EUR': 15})
        plan = rebalance(portfolio, MarketSnapshot({'ETF': Quote(10, 'EUR')}, {}))
        self.assertEqual(plan.trades, ())
        self.assertEqual(plan.cash_value, 15)
        self.assertEqual(plan.allocation_values, {(STOCK_MARKET, 'Stocks'): 20, (CASH, 'EUR'): 15})

    def test_cash_target_aggregates_currencies_and_funds_foreign_debt(self):
        portfolio = portfolio_fixture((Holding('ETF', 'Stocks', 0),), {'Stocks': 40, CASH: 60},
                              {'EUR': -5, 'USD': 20, 'HUF': 4000})
        snapshot = MarketSnapshot({'ETF': Quote(3, 'EUR')}, {'USD': .5, 'HUF': .0025})
        plan = rebalance(portfolio, snapshot)
        self.assertEqual(plan.final_shares['ETF'], 2)
        self.assertAlmostEqual(plan.cash_value, 9)
        self.assertTrue(plan.conversions)
        self.assertTrue(all(value >= 0 for value in plan.cash.values()))

    def test_all_cash_portfolio_settles_debt_without_quotes_or_trades(self):
        snapshot = MarketSnapshot({}, {'USD': .5, 'HUF': .0025})
        for cash in ({}, {'EUR': -5, 'USD': 20, 'HUF': 4000}):
            portfolio = portfolio_fixture((), {CASH: 100}, cash)
            for selling in (False, True):
                with self.subTest(cash=cash, selling=selling):
                    plan = rebalance(portfolio, snapshot, allow_selling=selling)
                    self.assertEqual(plan.trades, ())
                    self.assertEqual(plan.final_shares, {})
                    self.assertAlmostEqual(sum(plan.allocation_values.values()), plan.wealth)
                    self.assertTrue(all(value >= 0 for value in plan.cash.values()))
        with self.assertRaises(PlanningError):
            rebalance(portfolio_fixture((), {CASH: 100}, {'EUR': -1}), snapshot)

    def test_manual_cash_target_rounding_uses_native_hundredths(self):
        for currency, rate in (('EUR', 1), ('HUF', .0025)):
            portfolio = portfolio_fixture((), {TREASURY: 50, CASH: 50}, {},
                                  (ManualHolding(.03, currency),))
            snapshot = MarketSnapshot({}, {currency: rate})
            plan = rebalance(portfolio, snapshot, allow_selling=True)
            self.assertEqual(plan.final_manual_holdings[0].amount, .02)
            self.assertEqual(exhaustive_objectives(portfolio, snapshot, True, plan),
                             exhaustive_objectives(portfolio, snapshot, True))

    def test_seeded_small_portfolios_match_exact_exhaustive_optimum(self):
        rng = random.Random(2042)
        for case in range(50):
            a_target = rng.randrange(0, 101, 10)
            b_target = rng.randrange(0, 101 - a_target, 10)
            holdings = (Holding('A', 'Stocks', rng.randrange(3)),
                        Holding('OLD', 'Stocks', rng.randrange(3), False))
            manual = (ManualHolding(rng.randrange(5) / 100, 'HUF'),)
            portfolio = portfolio_fixture(holdings, {'Stocks': a_target, TREASURY: b_target,
                                            CASH: 100 - a_target - b_target},
                                  {'EUR': rng.randrange(-2, 5) / 100, 'USD': .02}, manual)
            snapshot = MarketSnapshot({'A': Quote(rng.randrange(1, 5) / 100, 'EUR'),
                                       'OLD': Quote(rng.randrange(1, 5) / 100, 'USD')},
                                      {'USD': .5, 'HUF': .5})
            for selling in (False, True):
                with self.subTest(case=case, selling=selling):
                    best = exhaustive_objectives(portfolio, snapshot, selling)
                    if best is None:
                        with self.assertRaises(PlanningError):
                            rebalance(portfolio, snapshot, allow_selling=selling)
                    else:
                        plan = rebalance(portfolio, snapshot, allow_selling=selling)
                        self.assertEqual(exhaustive_objectives(portfolio, snapshot, selling, plan), best)
                        self.assertTrue(all(value >= 0 for value in plan.cash.values()))


if __name__ == '__main__':
    unittest.main()
