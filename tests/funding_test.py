"""Monthly reserve policy, independently checked in native and base currencies."""

from dataclasses import replace
from datetime import date
from math import fsum
import unittest

from portfolio.calculator import CalculatorSettings, Expense, MonthlyCost, ReturnAssumptions, recommend
from portfolio.funding import prepare_allocation
from portfolio.models import (AllocationTargets, CASH, STOCK_MARKET, TREASURY,
                              Holding, ManualHolding, MarketData, Portfolio, Quote)


class FundingTests(unittest.TestCase):
    today = date(2024, 1, 1)

    def setUp(self):
        self.allocation = AllocationTargets({STOCK_MARKET: {'US': 40, 'EU': 20},
                                            TREASURY: {'EUR': 20, 'HUF': 20}, CASH: {}}, {'US': 'Equities', 'EU': 'Equities'})
        self.portfolio = Portfolio((Holding('US', 'US', 0), Holding('EU', 'EU', 0)),
                                   self.allocation, {'HUF': 1_000})
        self.market = MarketData({'US': Quote(1, 'EUR'), 'EU': Quote(1, 'EUR')},
                                 {'EUR': 1, 'HUF': .0025})
        self.settings = CalculatorSettings(monthly_costs=(MonthlyCost('Rent', 60), MonthlyCost('Food', 40)),
                                            returns=ReturnAssumptions(equities=0, mak=0))

    def prepare(self, **settings):
        return prepare_allocation(self.portfolio, self.market,
                                  replace(self.settings, **settings), self.today)

    def target_values(self, prepared):
        return {(parent, name): weight / 100 * prepared.recommendation.total
                for parent, leaves in prepared.portfolio.allocation.weights.items()
                for name, weight in leaves.items()}

    def test_actual_wealth_funds_reserves_without_changing_long_term_targets(self):
        result = self.prepare()
        self.assertEqual(result.recommendation.total, 1_000)
        expected = {(STOCK_MARKET, 'US'): 80, (STOCK_MARKET, 'EU'): 40,
                    (TREASURY, 'EUR'): 40, (TREASURY, 'HUF'): 640, (CASH, 'HUF'): 200}
        for key, value in expected.items():
            self.assertAlmostEqual(self.target_values(result)[key], value)
        self.assertEqual(dict(result.minimum_values), {(CASH, 'HUF'): .5, (TREASURY, 'HUF'): 1.5})
        self.assertEqual(self.portfolio.allocation, self.allocation)
        self.assertEqual(result.portfolio.cash, self.portfolio.cash)
        self.assertEqual(result.portfolio.holdings, self.portfolio.holdings)
        self.assertIn(ManualHolding(0, 'HUF'), result.portfolio.manual_holdings)

    def test_stock_manual_and_cash_wealth_give_same_targets_in_different_base_currencies(self):
        portfolio = replace(self.portfolio, holdings=(Holding('US', 'US', 2), Holding('EU', 'EU', 1)),
                            manual_holdings=(ManualHolding(100, 'HUF'),), cash={'EUR': 1})
        eur = prepare_allocation(portfolio, self.market, self.settings, self.today)
        huf_market = MarketData(self.market.quotes, {'EUR': 400}, base_currency='HUF')
        huf = prepare_allocation(portfolio, huf_market, self.settings, self.today)
        self.assertEqual(eur.recommendation.total, 1_700)
        self.assertEqual(eur.recommendation, huf.recommendation)
        self.assertEqual(eur.portfolio.allocation, huf.portfolio.allocation)
        self.assertEqual(dict(huf.minimum_values), {(CASH, 'HUF'): 200, (TREASURY, 'HUF'): 600})

    def test_no_reserves_preserve_home_targets_without_creating_protected_balances(self):
        result = self.prepare(monthly_costs=())
        self.assertEqual(result.portfolio.allocation, self.allocation)
        self.assertEqual(dict(result.minimum_values), {})
        self.assertEqual(result.recommendation.rows[-1].amount, 1_000)

    def test_emergency_is_mak_and_additional_bank_cash_has_priority(self):
        for months in (0, 1, 2, 3):
            with self.subTest(months=months):
                row = self.prepare(emergency_months=months).recommendation.rows[0]
                self.assertEqual((row.cash, row.mak), (0, months * 100))
        for cash, expected in ((150, (150, 0, 650)), (300, (200, 100, 500))):
            with self.subTest(cash=cash):
                portfolio = replace(self.portfolio, cash={'HUF': cash})
                result = prepare_allocation(portfolio, self.market, self.settings, self.today)
                recommendation = result.recommendation
                self.assertEqual((recommendation.cash, recommendation.mak, recommendation.shortfall), expected)
                self.assertEqual(recommendation.stock, 0)
                self.assertAlmostEqual(fsum(result.minimum_values.values()), cash * .0025)

    def test_expense_stock_uses_home_market_mix_and_defensive_portion_is_huf(self):
        result = self.prepare(monthly_costs=(), horizon_years=1,
                              expenses=(Expense('Half year', 200, date(2024, 9, 2)),))
        values = self.target_values(result)
        self.assertAlmostEqual(values[STOCK_MARKET, 'US'], 800 * .4 + 100 * 2 / 3)
        self.assertAlmostEqual(values[STOCK_MARKET, 'EU'], 800 * .2 + 100 / 3)
        self.assertAlmostEqual(values[TREASURY, 'EUR'], 160)
        self.assertAlmostEqual(values[TREASURY, 'HUF'], 260)
        self.assertEqual(dict(result.minimum_values), {(TREASURY, 'HUF'): .25})

    def test_long_term_cash_and_its_zero_return_are_separate_from_protected_cash(self):
        allocation = AllocationTargets({STOCK_MARKET: {'US': 40, 'EU': 20},
                                         TREASURY: {'EUR': 10, 'HUF': 10}, CASH: {'EUR': 20}}, {'US': 'Equities', 'EU': 'Equities'})
        settings = replace(self.settings, returns=ReturnAssumptions(equities=10, mak=5))
        result = prepare_allocation(replace(self.portfolio, allocation=allocation), self.market, settings, self.today)
        recommendation = result.recommendation
        self.assertEqual((recommendation.stock, recommendation.mak, recommendation.cash), (120, 640, 240))
        self.assertAlmostEqual(recommendation.expected_return, 4.4)
        self.assertEqual(result.minimum_values[CASH, 'HUF'], .5)
        self.assertEqual(self.target_values(result)[CASH, 'EUR'], 40)

    def test_actual_fractional_huf_is_not_rounded_and_signed_cash_is_included(self):
        portfolio = replace(self.portfolio, cash={'EUR': 1, 'HUF': -.5})
        result = prepare_allocation(portfolio, self.market, self.settings, self.today)
        self.assertEqual(result.recommendation.total, 399.5)
        self.assertAlmostEqual(fsum(result.minimum_values.values()), .99875)
        direct = recommend(self.settings, self.today, self.allocation, total=.125)
        self.assertEqual((direct.total, direct.cash, direct.mak), (.125, .125, 0))

    def test_untradeable_reserved_mak_fraction_stays_in_protected_cash(self):
        portfolio = replace(self.portfolio, cash={'EUR': 1, 'HUF': -.505})
        result = prepare_allocation(portfolio, self.market, self.settings, self.today)
        floors = result.minimum_values
        self.assertAlmostEqual(floors[TREASURY, 'HUF'] / .0025, 199.49)
        self.assertAlmostEqual(floors[CASH, 'HUF'] / .0025, 200.005)
        self.assertAlmostEqual(fsum(floors.values()), .9987375)
        for key, value in floors.items():
            self.assertAlmostEqual(self.target_values(result)[key] * .0025, value)

    def test_salary_already_received_is_current_wealth_and_future_payments_begin_next_month(self):
        expenses = (Expense('Now', 100, self.today), Expense('Next month', 100, date(2024, 4, 1)))
        result = self.prepare(monthly_costs=(), monthly_income=100, expenses=expenses)
        self.assertEqual(result.recommendation.total, 1_000)
        self.assertEqual([row.income_funded for row in result.recommendation.rows[2:-1]], [0, 100])
        self.assertEqual(result.recommendation.rows[-1].amount, 900)

    def test_reserve_stock_requires_home_mix_but_unfunded_stock_does_not(self):
        allocation = AllocationTargets({STOCK_MARKET: {'US': 0, 'EU': 0},
                                         TREASURY: {'HUF': 100}, CASH: {}})
        portfolio = replace(self.portfolio, allocation=allocation)
        settings = replace(self.settings, expenses=(Expense('Later', 100, date(2031, 1, 1)),))
        with self.assertRaisesRegex(ValueError, 'Stock market mix on Portfolio'):
            prepare_allocation(portfolio, self.market, settings, self.today)
        result = prepare_allocation(replace(portfolio, cash={'HUF': 100}), self.market, settings, self.today)
        self.assertEqual(result.recommendation.stock, 0)

    def test_zero_wealth_has_zero_recommendation_and_missing_huf_fx_fails_explicitly(self):
        result = prepare_allocation(replace(self.portfolio, cash={}), self.market, self.settings, self.today)
        self.assertEqual(result.recommendation.total, 0)
        self.assertEqual(result.recommendation.expected_return, 0)
        self.assertEqual(result.recommendation.cash_percent, 0)
        self.assertEqual(dict(result.minimum_values), {})
        self.assertEqual(result.portfolio.allocation, self.allocation)
        with self.assertRaisesRegex(ValueError, 'Missing exchange rate for HUF'):
            prepare_allocation(self.portfolio, MarketData({}, {}), self.settings, self.today)


if __name__ == '__main__':
    unittest.main()
