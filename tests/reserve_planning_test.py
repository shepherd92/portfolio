"""Protected reserves remain funded after integer trades and currency settlement."""

from decimal import Decimal
from datetime import date
from itertools import product
import unittest

from portfolio.calculator import CalculatorSettings, CashFlow, ReturnAssumptions
from portfolio.funding import prepare_allocation
from portfolio.models import (AllocationTargets, CASH, STOCK_MARKET, TREASURY,
                              Holding, ManualHolding, MarketData, Portfolio, Quote)
from portfolio.planning import PlanningError, rebalance


def portfolio(stock=50, mak=30, cash=20, *, shares=0, balance=100):
    return Portfolio((Holding('ETF', 'Stocks', shares),),
                     AllocationTargets({STOCK_MARKET: {'Stocks': stock},
                                        TREASURY: {'HUF': mak}, CASH: {'HUF': cash}}),
                     {'EUR': balance})


class ReservePlanningTests(unittest.TestCase):
    def test_plan_protects_extra_bank_cash_due_expense_cash_and_full_emergency_mak(self):
        source = Portfolio((Holding('ETF', 'Stocks', 0),),
                           AllocationTargets({STOCK_MARKET: {'Stocks': 60}, TREASURY: {'HUF': 40}, CASH: {}},
                                             {'Stocks': 'Equities'}), {'EUR': 100})
        market = MarketData({'ETF': Quote(10, 'EUR')}, {'HUF': .0025})
        settings = CalculatorSettings(
            incomes=(CashFlow('Salary', 1_000_000, date(2026, 1, 1), frequency='monthly'),),
            returns=ReturnAssumptions(mak_huf=5),
            outgoings=(
                CashFlow('Living costs', 1000, date.min, frequency='monthly'),
                CashFlow('Ready today', 2000, date(2026, 3, 1)),
            ),
        )
        funding = prepare_allocation(source, market, settings, date(2026, 1, 1))
        self.assertEqual(dict(funding.minimum_values), {(CASH, 'HUF'): 10, (TREASURY, 'HUF'): 15})
        self.assertEqual(funding.recommendation.rows[2].income_funded, 0)
        plan = rebalance(funding.portfolio, market, minimum_values=funding.minimum_values)
        self.assertGreaterEqual(plan.cash['HUF'], 4000)
        self.assertGreaterEqual(plan.allocation_values[TREASURY, 'HUF'] / .0025, 6000)
        self.assertAlmostEqual(sum(plan.allocation_values.values()), 100)

    def test_salary_funds_reserves_before_new_shares(self):
        source = portfolio()
        market = MarketData({'ETF': Quote(10, 'EUR')}, {'HUF': .0025})
        result = rebalance(source, market,
                           minimum_values={(CASH, 'HUF'): 20, (TREASURY, 'HUF'): 30})
        self.assertEqual(result.final_shares['ETF'], 5)
        self.assertAlmostEqual(result.cash['HUF'], 8000)
        self.assertEqual(result.final_manual_holdings, (ManualHolding(12000, 'HUF'),))
        self.assertAlmostEqual(sum(result.allocation_values.values()), 100)
        self.assertEqual(source.cash, {'EUR': 100})

    def test_cash_floor_overrules_rounding_to_nearest_share(self):
        source = portfolio(stock=56, mak=0, cash=44)
        market = MarketData({'ETF': Quote(10, 'EUR')}, {'HUF': .0025})
        ordinary = rebalance(source, market)
        protected = rebalance(source, market, minimum_values={(CASH, 'HUF'): 44})
        self.assertEqual(ordinary.final_shares['ETF'], 6)
        self.assertEqual(protected.final_shares['ETF'], 5)
        self.assertGreaterEqual(protected.allocation_values[CASH, 'HUF'], 44)

    def test_underfunded_reserves_require_sale_permission(self):
        source = portfolio(shares=10, balance=0)
        market = MarketData({'ETF': Quote(10, 'EUR')}, {'HUF': .0025})
        floors = {(CASH, 'HUF'): 20, (TREASURY, 'HUF'): 30}
        with self.assertRaisesRegex(PlanningError, 'Protected reserves.*selling disabled'):
            rebalance(source, market, minimum_values=floors)
        result = rebalance(source, market, allow_selling=True, minimum_values=floors)
        self.assertEqual(result.final_shares['ETF'], 5)
        self.assertAlmostEqual(result.allocation_values[CASH, 'HUF'], 20)
        self.assertAlmostEqual(result.allocation_values[TREASURY, 'HUF'], 30)

    def test_currency_settlement_protects_cash_before_other_currency_targets(self):
        source = Portfolio((Holding('ETF', 'Stocks', 8),),
                           AllocationTargets({STOCK_MARKET: {'Stocks': 0}, TREASURY: {},
                                              CASH: {'HUF': 20, 'EUR': 80}}), {'USD': 40})
        market = MarketData({'ETF': Quote(10, 'EUR')}, {'HUF': .0025, 'USD': .5})
        result = rebalance(source, market, minimum_values={(CASH, 'HUF'): 20})
        self.assertEqual(result.final_shares['ETF'], 8)
        self.assertAlmostEqual(result.cash['HUF'], 8000)
        self.assertAlmostEqual(result.cash['EUR'], 0)
        self.assertAlmostEqual(result.cash['USD'], 0)
        self.assertAlmostEqual(sum(result.allocation_values.values()), 100)

    def test_cash_floor_above_target_preserves_true_currency_error_objective(self):
        source = Portfolio((Holding('ETF', 'Stocks', 0),),
                           AllocationTargets({STOCK_MARKET: {'Stocks': 50}, TREASURY: {},
                                              CASH: {'EUR': 50}}), {'EUR': 100})
        market = MarketData({'ETF': Quote(10, 'EUR')}, {'HUF': .0025})
        result = rebalance(source, market, minimum_values={(CASH, 'HUF'): 20})
        # 30 EUR shares and 70 EUR cash tie with 50/50 on total error, but have
        # lower true currency cash error (20 versus 40 EUR).
        self.assertEqual(result.final_shares['ETF'], 3)
        self.assertAlmostEqual(result.cash['EUR'], 50)
        self.assertAlmostEqual(result.cash['HUF'], 8000)

    def test_all_cash_portfolio_needs_no_trading_positions(self):
        source = Portfolio((), AllocationTargets({STOCK_MARKET: {}, TREASURY: {},
                                                   CASH: {'EUR': 100}}), {'USD': 100})
        market = MarketData({}, {'HUF': .0025, 'USD': .5})
        result = rebalance(source, market, minimum_values={(CASH, 'HUF'): 20})
        self.assertEqual(result.trades, ())
        self.assertEqual(result.manual_trades, ())
        self.assertAlmostEqual(result.cash['HUF'], 8000)
        self.assertAlmostEqual(result.cash['EUR'], 30)
        with self.assertRaisesRegex(PlanningError, 'Protected reserves exceed'):
            rebalance(source, market, minimum_values={(CASH, 'HUF'): 51})

    def test_floor_can_create_missing_treasury_holding(self):
        source = Portfolio((), AllocationTargets({STOCK_MARKET: {}, TREASURY: {},
                                                   CASH: {'EUR': 100}}), {'EUR': 100})
        result = rebalance(source, MarketData({}, {'HUF': .0025}),
                           minimum_values={(TREASURY, 'HUF'): 30})
        self.assertEqual(result.final_manual_holdings, (ManualHolding(12000, 'HUF'),))
        self.assertAlmostEqual(result.cash['EUR'], 70)

    def test_treasury_floor_uses_executable_native_hundredths(self):
        source = Portfolio((), AllocationTargets({STOCK_MARKET: {}, TREASURY: {'HUF': 100},
                                                   CASH: {}}), {'HUF': .035})
        market = MarketData({}, {}, 'HUF')
        result = rebalance(source, market,
                           minimum_values={(TREASURY, 'HUF'): .015, (CASH, 'HUF'): .01})
        self.assertEqual(result.final_manual_holdings[0].amount, .02)
        self.assertAlmostEqual(result.cash['HUF'], .015)
        with self.assertRaisesRegex(PlanningError, 'Protected reserves cannot be funded'):
            rebalance(source, market, allow_selling=True,
                      minimum_values={(TREASURY, 'HUF'): .015, (CASH, 'HUF'): .02})

    def test_fractional_wealth_can_be_fully_reserved(self):
        for rate in (1, 1 / 399.7654321):
            source = Portfolio((), AllocationTargets({STOCK_MARKET: {}, TREASURY: {'HUF': 60},
                                                       CASH: {'HUF': 40}}), {'HUF': 2.345})
            market = MarketData({}, {'HUF': rate}, 'HUF' if rate == 1 else 'EUR')
            result = rebalance(source, market,
                               minimum_values={(TREASURY, 'HUF'): market.value(1.34, 'HUF'),
                                               (CASH, 'HUF'): market.value(1.005, 'HUF')})
            self.assertEqual(result.final_manual_holdings[0].amount, 1.34)
            self.assertAlmostEqual(result.cash['HUF'], 1.005)
            self.assertAlmostEqual(sum(result.allocation_values.values()), result.wealth)

    def test_large_fully_reserved_foreign_cash_avoids_false_solver_infeasibility(self):
        market = MarketData({}, {'EUR': 269.88530816185414}, 'HUF')
        cash = {'EUR': 8356.372357171655, 'HUF': 4853.253317234036}
        wealth = sum(market.value(amount, currency) for currency, amount in cash.items())
        mak = 1609321.38
        cash_floor = wealth - mak
        source = Portfolio((), AllocationTargets({STOCK_MARKET: {},
                           TREASURY: {'HUF': mak / wealth * 100},
                           CASH: {'HUF': cash_floor / wealth * 100}}), cash)
        for selling in (False, True):
            result = rebalance(source, market, allow_selling=selling,
                               minimum_values={(TREASURY, 'HUF'): mak, (CASH, 'HUF'): cash_floor})
            self.assertEqual(result.final_manual_holdings, (ManualHolding(mak, 'HUF'),))
            self.assertAlmostEqual(result.cash['HUF'], cash_floor)
            self.assertAlmostEqual(sum(result.allocation_values.values()), wealth)

    def test_large_mixed_currency_plan_is_stable_when_base_currency_changes(self):
        allocation = AllocationTargets({STOCK_MARKET: {'Stocks': 38.61993356056041},
                           TREASURY: {'HUF': 12.873311186853472, 'EUR': 12.873311186853472},
                           CASH: {'HUF': 35.63344406573265}})
        source = Portfolio((Holding('ETF', 'Stocks', 0),), allocation,
                           {'EUR': 8021.012558448328, 'HUF': 9926.542275721786})
        quotes = {'ETF': Quote(45.08801741535625, 'EUR')}
        markets = (MarketData(quotes, {'EUR': 332.56529571216134}, 'HUF'),
                   MarketData(quotes, {'HUF': 1 / 332.56529571216134}, 'EUR'))
        plans = [rebalance(source, market,
                           minimum_values={(CASH, 'HUF'): market.value(954063, 'HUF')})
                 for market in markets]
        self.assertEqual(plans[0].final_shares, plans[1].final_shares)
        self.assertEqual(plans[0].final_manual_holdings, plans[1].final_manual_holdings)
        for plan in plans:
            self.assertGreaterEqual(plan.cash['HUF'], 954063)
            self.assertAlmostEqual(sum(plan.allocation_values.values()), plan.wealth)

    def test_small_plans_match_independent_enumeration_with_reserve_bounds(self):
        # Integer cents of base currency avoid sharing the optimizer's float arithmetic.
        for shares, selling, cash_floor, mak_floor in product((0, 2), (False, True), (1, 3), (1, 2)):
            source = portfolio(shares=shares, balance=.10)
            market = MarketData({'ETF': Quote(.03, 'EUR')}, {'HUF': 1})
            wealth = shares * 3 + 10
            feasible = []
            for end_shares in range(wealth // 3 + 1):
                for end_mak in range(mak_floor, wealth + 1):
                    end_cash = wealth - end_shares * 3 - end_mak
                    if end_cash < cash_floor or (not selling and end_shares < shares):
                        continue
                    targets = (Decimal(wealth) / 2, Decimal(wealth) * Decimal('.3'),
                               Decimal(wealth) * Decimal('.2'))
                    errors = tuple(abs(Decimal(value) - target) for value, target in
                                   zip((end_shares * 3, end_mak, end_cash), targets))
                    feasible.append((sum(errors), errors[-1], abs(end_shares - shares) * 3 + end_mak))
            with self.subTest(shares=shares, selling=selling, cash=cash_floor, mak=mak_floor):
                result = rebalance(source, market, allow_selling=selling,
                                   minimum_values={(CASH, 'HUF'): cash_floor / 100,
                                                   (TREASURY, 'HUF'): mak_floor / 100})
                end_shares = result.final_shares['ETF']
                end_mak = result.final_manual_holdings[0].cents
                end_cash = wealth - end_shares * 3 - end_mak
                errors = tuple(abs(Decimal(value) - target) for value, target in
                               zip((end_shares * 3, end_mak, end_cash), targets))
                self.assertEqual((sum(errors), errors[-1], abs(end_shares - shares) * 3 + end_mak),
                                 min(feasible))

    def test_reserve_inputs_must_be_valid_finite_currency_values(self):
        source = portfolio()
        market = MarketData({'ETF': Quote(10, 'EUR')}, {'HUF': .0025})
        invalid = ([], {(STOCK_MARKET, 'Stocks'): 1}, {(CASH, 'HU'): 1},
                   {(TREASURY, 'USD'): 1}, {(CASH, 'HUF'): -1},
                   {(CASH, 'HUF'): float('nan')}, {(CASH, 'HUF'): True})
        for floors in invalid:
            with self.subTest(floors=floors), self.assertRaises(ValueError):
                rebalance(source, market, minimum_values=floors)
        with self.assertRaisesRegex(ValueError, 'Missing exchange rate'):
            rebalance(source, market, minimum_values={(CASH, 'USD'): 1})


if __name__ == '__main__':
    unittest.main()
