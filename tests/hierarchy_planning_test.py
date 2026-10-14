"""Hierarchical target semantics and independent exact optimization checks."""

from fractions import Fraction
import itertools
import random
import unittest

from portfolio.models import (AllocationTargets, CASH, STOCK_MARKET, TREASURY, Holding,
                    ManualHolding, MarketSnapshot, Portfolio, Quote)
from portfolio.planning import rebalance


def allocation(stock=0, treasury=0, *, stocks=None, bonds=None, cash=None):
    return AllocationTargets({STOCK_MARKET: stock, TREASURY: treasury, CASH: 100 - stock - treasury},
                             {STOCK_MARKET: stocks or {}, TREASURY: bonds or {},
                              CASH: {'EUR': 100} if cash is None else cash})


class HierarchyModelTests(unittest.TestCase):
    def test_targets_multiply_and_are_deeply_immutable(self):
        targets = allocation(60, 20, stocks={'US': 75, 'EU': 25}, bonds={'EUR': 25, 'HUF': 75})
        self.assertEqual(targets.effective[(STOCK_MARKET, 'US')], 45)
        self.assertEqual(targets.effective[(TREASURY, 'HUF')], 15)
        with self.assertRaises(TypeError):
            targets.children[CASH]['EUR'] = 0
        with self.assertRaises(TypeError):
            targets.top[CASH] = 0

    def test_currency_codes_are_normalized_and_duplicates_rejected(self):
        self.assertEqual(allocation(cash={'gbp': 100}).children[CASH], {'GBP': 100})
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            allocation(cash={'eur': 50, 'EUR': 50})

    def test_active_children_must_sum_to_one_hundred(self):
        with self.assertRaisesRegex(ValueError, 'sum to 100'):
            allocation(60, stocks={'US': 60})
        with self.assertRaisesRegex(ValueError, 'sum to 100'):
            allocation(cash={'EUR': 0})

    def test_zero_parent_accepts_unset_or_configured_children(self):
        self.assertEqual(allocation(bonds={'EUR': 0, 'HUF': 0}).top[TREASURY], 0)
        self.assertEqual(allocation(bonds={'EUR': 25, 'HUF': 75}).top[TREASURY], 0)
        with self.assertRaisesRegex(ValueError, 'sum to 100'):
            allocation(bonds={'EUR': 25})

    def test_only_eur_and_huf_are_treasury_currencies(self):
        with self.assertRaisesRegex(ValueError, 'EUR or HUF'):
            allocation(bonds={'USD': 100})

    def test_required_allocation_matches_listed_groups(self):
        targets = allocation(100, stocks={'US': 100})
        with self.assertRaisesRegex(ValueError, 'validated allocation'):
            Portfolio((Holding('A', 'US', 0),), None, {})
        with self.assertRaisesRegex(ValueError, 'every listed'):
            Portfolio((), targets, {})

    def test_missing_treasury_currencies_are_materialized_without_replacing_holdings(self):
        original = ManualHolding(2, 'EUR')
        portfolio = Portfolio((), allocation(0, 100, bonds={'EUR': 40, 'HUF': 60}), {}, (original,))
        self.assertIn(original, portfolio.manual_holdings)
        self.assertIn(ManualHolding(0, 'HUF'), portfolio.manual_holdings)

    def test_inactive_currencies_do_not_materialize_holdings_or_need_rates(self):
        portfolio = Portfolio((), allocation(bonds={'EUR': 0, 'HUF': 0}, cash={'EUR': 100, 'HUF': 0}), {'EUR': 10})
        self.assertEqual(portfolio.manual_holdings, ())
        plan = rebalance(portfolio, MarketSnapshot({}, {}))
        self.assertEqual(plan.cash_value, 10)


class HierarchyPlanningTests(unittest.TestCase):
    def test_all_branches_and_children_receive_correct_absolute_values(self):
        targets = allocation(60, 20, stocks={'US': 75, 'EU': 25},
                             bonds={'EUR': 25, 'HUF': 75}, cash={'EUR': 50, 'HUF': 50})
        portfolio = Portfolio((Holding('US', 'US', 0), Holding('EU', 'EU', 0)), targets, {'EUR': 100})
        market = MarketSnapshot({'US': Quote(1, 'EUR'), 'EU': Quote(1, 'EUR')}, {'HUF': .0025})
        plan = rebalance(portfolio, market)
        expected = {(STOCK_MARKET, 'US'): 45, (STOCK_MARKET, 'EU'): 15,
                    (TREASURY, 'EUR'): 5, (TREASURY, 'HUF'): 15,
                    (CASH, 'EUR'): 10, (CASH, 'HUF'): 10}
        for key, value in expected.items():
            self.assertAlmostEqual(plan.allocation_values[key], value)
        self.assertAlmostEqual(sum(plan.allocation_values.values()), plan.wealth)

    def test_cash_only_portfolio_funds_new_currency_without_selling(self):
        portfolio = Portfolio((), allocation(cash={'EUR': 50, 'GBP': 50}), {'EUR': 100})
        plan = rebalance(portfolio, MarketSnapshot({}, {'GBP': 2}))
        self.assertEqual(plan.cash, {'EUR': 50, 'GBP': 25})
        self.assertEqual(len(plan.conversions), 1)
        self.assertEqual(plan.trades, ())

    def test_existing_currency_with_zero_target_is_converted(self):
        portfolio = Portfolio((), allocation(), {'HUF': 40000})
        plan = rebalance(portfolio, MarketSnapshot({}, {'HUF': .0025}))
        self.assertAlmostEqual(plan.cash['EUR'], 100)
        self.assertAlmostEqual(plan.cash['HUF'], 0)

    def test_target_only_cash_currency_requires_exchange_rate(self):
        portfolio = Portfolio((), allocation(cash={'GBP': 100}), {'EUR': 100})
        with self.assertRaisesRegex(ValueError, 'Missing exchange rate for GBP'):
            rebalance(portfolio, MarketSnapshot({}, {}))

    def test_treasury_currency_switch_requires_selling_permission(self):
        portfolio = Portfolio((), allocation(0, 100, bonds={'HUF': 100}), {}, (ManualHolding(100, 'EUR'),))
        market = MarketSnapshot({}, {'HUF': .0025})
        retained = rebalance(portfolio, market)
        self.assertEqual(retained.manual_trades, ())
        switched = rebalance(portfolio, market, allow_selling=True)
        self.assertAlmostEqual(switched.allocation_values[(TREASURY, 'EUR')], 0)
        self.assertAlmostEqual(switched.allocation_values[(TREASURY, 'HUF')], 100)

    def test_zero_parent_and_zero_child_never_receive_purchases(self):
        targets = allocation(100, stocks={'US': 100, 'Other': 0}, bonds={'EUR': 100})
        portfolio = Portfolio((Holding('US', 'US', 0), Holding('Other', 'Other', 0)), targets, {'EUR': 50})
        market = MarketSnapshot({'US': Quote(100, 'EUR'), 'Other': Quote(1, 'EUR')}, {})
        for selling in (False, True):
            plan = rebalance(portfolio, market, allow_selling=selling)
            self.assertEqual(plan.trades, ())
            self.assertEqual(plan.manual_trades, ())
            self.assertEqual(plan.cash_value, 50)

    def test_unset_zero_cash_branch_preserves_indivisible_residual(self):
        portfolio = Portfolio((Holding('US', 'US', 0),), allocation(100, stocks={'US': 100}, cash={'EUR': 0, 'HUF': 0}), {'EUR': 100})
        market = MarketSnapshot({'US': Quote(30, 'EUR')}, {'HUF': .0025})
        plan = rebalance(portfolio, market)
        self.assertEqual(plan.final_shares['US'], 3)
        self.assertAlmostEqual(plan.cash['EUR'], 10)
        self.assertAlmostEqual(sum(plan.allocation_values.values()), 100)

    def test_non_main_tickers_are_retained(self):
        portfolio = Portfolio((Holding('NEW', 'US', 0), Holding('OLD', 'US', 5, False)), allocation(100, stocks={'US': 100}), {})
        market = MarketSnapshot({'NEW': Quote(10, 'EUR'), 'OLD': Quote(10, 'EUR')}, {})
        self.assertEqual(rebalance(portfolio, market, allow_selling=True).trades, ())

    def test_same_child_name_in_different_branches_is_distinct(self):
        portfolio = Portfolio((Holding('A', 'EUR', 0),), allocation(50, stocks={'EUR': 100}), {'EUR': 10})
        plan = rebalance(portfolio, MarketSnapshot({'A': Quote(1, 'EUR')}, {}))
        self.assertEqual(plan.allocation_values[(STOCK_MARKET, 'EUR')], 5)
        self.assertEqual(plan.allocation_values[(CASH, 'EUR')], 5)

    def test_seeded_small_portfolios_match_independent_exact_enumeration(self):
        rng = random.Random(913)
        for case in range(20):
            stock, treasury = rng.randrange(11) * 10, 0
            treasury = rng.randrange((100 - stock) // 10 + 1) * 10
            targets = allocation(stock, treasury, stocks={'US': 60, 'EU': 40}, bonds={'EUR': 100})
            holdings = (Holding('US', 'US', rng.randrange(3)), Holding('EU', 'EU', rng.randrange(3)))
            manual = (ManualHolding(rng.randrange(3) / 100, 'EUR'),)
            portfolio = Portfolio(holdings, targets, {'EUR': rng.randrange(4) / 100}, manual)
            market = MarketSnapshot({'US': Quote(.02, 'EUR'), 'EU': Quote(.03, 'EUR')}, {})
            for selling in (False, True):
                with self.subTest(case=case, selling=selling):
                    plan = rebalance(portfolio, market, allow_selling=selling)
                    owned = (holdings[0].shares, holdings[1].shares, manual[0].cents)
                    prices = (2, 3, 1)
                    wealth = sum(a * b for a, b in zip(owned, prices)) + round(portfolio.cash['EUR'] * 100)
                    effective = [Fraction(str(targets.effective[key])) / 100 for key in
                                 ((STOCK_MARKET, 'US'), (STOCK_MARKET, 'EU'), (TREASURY, 'EUR'))]

                    def score(units):
                        leaves = [quantity * price for quantity, price in zip(units, prices)]
                        cash = wealth - sum(leaves)
                        roots = {STOCK_MARKET: sum(leaves[:2]), TREASURY: leaves[2], CASH: cash}
                        top = sum(abs(value - wealth * Fraction(str(targets.top[key])) / 100)
                                  for key, value in roots.items())
                        cash_error = abs(cash - wealth * Fraction(str(targets.top[CASH])) / 100)
                        leaf = sum(abs(value - wealth * target) for value, target in zip(leaves, effective)) + cash_error
                        turnover = sum(abs(final - start) * price for final, start, price in zip(units, owned, prices))
                        return top, leaf, cash_error, turnover

                    ranges = [range(0 if selling else start, (wealth // price if target else start) + 1)
                              for start, price, target in zip(owned, prices, effective)]
                    best = min(score(units) for units in itertools.product(*ranges)
                               if sum(a * b for a, b in zip(units, prices)) <= wealth)
                    actual = (plan.final_shares['US'], plan.final_shares['EU'], plan.final_manual_holdings[0].cents)
                    self.assertEqual(score(actual), best)


if __name__ == '__main__':
    unittest.main()
