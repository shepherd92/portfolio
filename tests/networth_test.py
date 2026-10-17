"""Net worth includes external assets once and preserves cash-transfer accounting."""

from dataclasses import FrozenInstanceError
from datetime import date
from math import fsum, isfinite
import unittest
from unittest.mock import patch

from portfolio.calculator import CashFlow
from portfolio.models import (CASH, STOCK_MARKET, TREASURY, AllocationTargets, Holding,
                              ManualHolding, MarketData, Quote)
from portfolio.networth import current_overview, net_worth_series
from portfolio.wealth import Loan, Pension, PensionContribution, Property, WealthSettings


TODAY = date(2026, 10, 16)
NOVEMBER = date(2026, 11, 1)
DECEMBER = date(2026, 12, 1)


class NetWorthTest(unittest.TestCase):
    def series(self, wealth, dates=(TODAY,), liquid=(100,), **kwargs):
        return net_worth_series(wealth, TODAY, dates, liquid, kwargs.pop('rates', None),
                                kwargs.pop('currency', 'HUF'), **kwargs)

    def test_values_and_debt_remain_separate_without_changing_liquid_portfolio(self):
        wealth = WealthSettings(properties=(Property('home', 'Home', 1000),),
                                pensions=(Pension('pension', 'Pension', balance=200, access=NOVEMBER),),
                                loans=(Loan('loan', 'Loan', 500),))
        result = self.series(wealth)
        self.assertEqual(result, {'Investment portfolio': (100,), 'Properties': (1000,),
                                  'Pensions': (200,), 'Loans': (500,), 'Net worth': (800,)})

    def test_negative_net_worth_is_valid(self):
        result = self.series(WealthSettings(loans=(Loan('loan', 'Loan', 500),)))
        self.assertEqual(result['Net worth'], (-400,))
        self.assertEqual(result['Loans'], (500,))

    def test_loan_principal_payment_does_not_change_net_worth(self):
        loan = Loan('loan', 'Loan', 100, CashFlow('Repayment', 20, NOVEMBER))
        result = self.series(WealthSettings(loans=(loan,)),
                             dates=(TODAY, NOVEMBER, NOVEMBER), liquid=(100, 100, 80),
                             before_payments=(False, True, False))
        self.assertEqual(result['Loans'], (100, 100, 80))
        self.assertEqual(result['Net worth'], (0, 0, 0))

    def test_pension_bank_transfer_preserves_net_worth(self):
        pension = Pension('pension', 'Pension', balance=100, access=NOVEMBER,
                          contributions=(PensionContribution(CashFlow('Saving', 20, NOVEMBER)),))
        result = self.series(WealthSettings(pensions=(pension,)),
                             dates=(NOVEMBER, NOVEMBER), liquid=(100, 80), before_payments=(True, False))
        self.assertEqual(result['Pensions'], (100, 120))
        self.assertEqual(result['Net worth'], (200, 200))

    def test_employer_contribution_changes_net_worth_only_after_payment(self):
        pension = Pension('pension', 'Pension', balance=100, access=NOVEMBER,
                          contributions=(PensionContribution(CashFlow('Employer', 20, NOVEMBER), 'employer'),))
        result = self.series(WealthSettings(pensions=(pension,)), dates=(NOVEMBER, NOVEMBER),
                             liquid=(100, 100), before_payments=(True, False))
        self.assertEqual(result['Pensions'], (100, 120))
        self.assertEqual(result['Net worth'], (200, 220))

    def test_pension_withdrawal_preserves_net_worth(self):
        pension = Pension('pension', 'Pension', balance=100, access=NOVEMBER,
                          withdrawals=(CashFlow('Retirement', 20, NOVEMBER),))
        result = self.series(WealthSettings(pensions=(pension,)), dates=(NOVEMBER, NOVEMBER),
                             liquid=(100, 120), before_payments=(True, False))
        self.assertEqual(result['Pensions'], (100, 80))
        self.assertEqual(result['Net worth'], (200, 200))

    def test_interpolates_growth_between_snapshots_with_one_projection(self):
        home = Property('home', 'Home', 1000, appreciation_percent=10)
        wealth = WealthSettings(properties=(home,))
        dates = (TODAY, date(2026, 11, 15), DECEMBER)
        original = WealthSettings.project
        calls = []

        def projected(settings, start, end):
            calls.append((start, end))
            return original(settings, start, end)

        with patch.object(WealthSettings, 'project', projected):
            result = self.series(wealth, dates=dates, liquid=(100, 100, 100))
        self.assertEqual(calls, [(TODAY, DECEMBER)])
        for day, value in zip(dates, result['Properties']):
            self.assertAlmostEqual(value, 1000 * 1.1 ** ((day - TODAY).days / 365.25))

    def test_existing_projection_is_reused(self):
        wealth = WealthSettings(properties=(Property('home', 'Home', 1000),))
        projected = wealth.project(TODAY, DECEMBER)
        with patch.object(WealthSettings, 'project', side_effect=AssertionError('Repeated projection')):
            result = self.series(wealth, projection=projected)
        self.assertEqual(result['Properties'], (1000,))
        with self.assertRaisesRegex(ValueError, 'cover'):
            self.series(wealth, dates=(date(2027, 1, 1),), projection=projected)

    def test_fixed_fx_converts_external_values_but_not_already_displayed_liquid_values(self):
        wealth = WealthSettings(properties=(Property('home', 'Home', 1000, currency='EUR'),),
                                loans=(Loan('loan', 'Loan', 10000),))
        result = self.series(wealth, liquid=(20,), currency='USD', rates={'EUR': 400, 'USD': 350})
        self.assertAlmostEqual(result['Investment portfolio'][0], 20)
        self.assertAlmostEqual(result['Properties'][0], 400000 / 350)
        self.assertAlmostEqual(result['Loans'][0], 10000 / 350)
        self.assertAlmostEqual(result['Net worth'][0], 20 + 390000 / 350)

    def test_empty_and_no_external_assets(self):
        self.assertTrue(all(not values for values in self.series(WealthSettings(), dates=(), liquid=()).values()))
        result = self.series(WealthSettings())
        self.assertEqual(result['Net worth'], result['Investment portfolio'])

    def test_missing_fx_and_mismatched_arrays_are_rejected(self):
        wealth = WealthSettings(properties=(Property('home', 'Home', 100, currency='EUR'),))
        with self.assertRaisesRegex(ValueError, 'Missing exchange rate'):
            self.series(wealth)
        for options in ({'liquid': ()}, {'before_payments': ()}, {'before_payments': (1,)},
                        {'dates': (date(2026, 1, 1),)}, {'liquid': (-1,)}, {'liquid': (float('inf'),)}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.series(WealthSettings(), **options)


class CurrentOverviewTest(unittest.TestCase):
    def test_gross_assets_and_liabilities_reconcile_without_enlarging_trading_wealth(self):
        market = MarketData({'ETF': Quote(100, 'EUR')}, {'HUF': .0025})
        holdings = (Holding('ETF', 'World', 6),)
        manual, cash = (ManualHolding(300, 'EUR'),), {'EUR': 100, 'HUF': -20000}
        wealth = WealthSettings(properties=(Property('home', 'Home', 4000, 'EUR'),),
                                pensions=(Pension('pension', 'Pension', 'EUR', balance=1000, access=NOVEMBER),),
                                loans=(Loan('loan', 'Loan', 2000, currency='EUR', property_id='home'),))
        overview = current_overview(holdings, manual, cash, market, wealth)
        self.assertEqual(dict(overview.allocations), {STOCK_MARKET: 600, TREASURY: 300, CASH: 100,
                                                      'Properties': 4000, 'Pension funds': 1000})
        self.assertEqual((overview.investment_total, overview.assets_total, overview.loans,
                          overview.cash_deficit, overview.net_worth), (950, 6000, 2000, 50, 3950))
        self.assertEqual(cash, {'EUR': 100, 'HUF': -20000})
        self.assertNotIn('Loans', overview.allocations)
        with self.assertRaises(TypeError):
            overview.allocations['Properties'] = 0
        with self.assertRaises(FrozenInstanceError):
            overview.net_worth = 0

    def test_negative_cash_is_clipped_per_currency_before_aggregation(self):
        overview = current_overview((), (), {'EUR': 100, 'USD': -200},
                                    MarketData({}, {'USD': .5}), WealthSettings())
        self.assertEqual(overview.investment_total, 0)
        self.assertEqual(overview.allocations[CASH], 100)
        self.assertEqual((overview.assets_total, overview.cash_deficit, overview.net_worth), (100, 100, 0))

    def test_debt_only_portfolio_has_no_pie_assets_and_negative_net_worth(self):
        overview = current_overview((), (), {'EUR': -20}, MarketData({}, {}),
                                    WealthSettings(loans=(Loan('loan', 'Loan', 100, currency='EUR'),)))
        self.assertFalse(any(overview.allocations.values()))
        self.assertEqual((overview.investment_total, overview.assets_total, overview.loans,
                          overview.cash_deficit, overview.net_worth), (-20, 0, 100, 20, -120))

    def test_income_only_pension_has_no_invented_market_value(self):
        pension = Pension('income', 'Promised pension', 'DKK', kind='income',
                          withdrawals=(CashFlow('Pension', 500, NOVEMBER, 'DKK', 'monthly'),))
        overview = current_overview((), (), {'EUR': 100}, MarketData({}, {}),
                                    WealthSettings(pensions=(pension,)))
        self.assertEqual(overview.allocations['Pension funds'], 0)
        self.assertEqual(overview.net_worth, 100)

    def test_manual_value_changes_do_not_change_tradable_money_or_forecast_growth(self):
        for value, appreciation in ((1000, 0), (2000, 100)):
            property = Property('home', 'Home', value, 'EUR', appreciation_percent=appreciation)
            overview = current_overview((), (), {'EUR': 100}, MarketData({}, {}),
                                        WealthSettings(properties=(property,)))
            self.assertEqual(overview.investment_total, 100)
            self.assertEqual(overview.allocations['Properties'], value)
            self.assertEqual(overview.net_worth, value + 100)

    def test_all_values_use_the_market_base_currency(self):
        market = MarketData({'ETF': Quote(10, 'EUR')}, {'EUR': 1.25, 'HUF': .0025}, base_currency='USD')
        overview = current_overview((Holding('ETF', 'World', 2),), (ManualHolding(1000, 'HUF'),),
                                    {'USD': 10}, market,
                                    WealthSettings(properties=(Property('home', 'Home', 1000, 'EUR'),),
                                                   loans=(Loan('loan', 'Loan', 10000),)))
        self.assertEqual(dict(overview.allocations), {STOCK_MARKET: 25, TREASURY: 2.5, CASH: 10,
                                                      'Properties': 1250, 'Pension funds': 0})
        self.assertEqual((overview.investment_total, overview.loans, overview.net_worth), (37.5, 25, 1262.5))

    def test_missing_nonzero_asset_or_loan_fx_fails_closed(self):
        market = MarketData({}, {})
        for wealth in (WealthSettings(properties=(Property('home', 'Home', 1000, 'USD'),)),
                       WealthSettings(pensions=(Pension('pension', 'Pension', 'USD', balance=1000, access=NOVEMBER),)),
                       WealthSettings(loans=(Loan('loan', 'Loan', 1000, currency='USD'),))):
            with self.subTest(wealth=wealth), self.assertRaisesRegex(ValueError, 'Missing exchange rate'):
                current_overview((), (), {'EUR': 100}, market, wealth)
        with self.assertRaisesRegex(ValueError, 'Missing quote'):
            current_overview((Holding('ETF', 'World', 1),), (), {}, market, WealthSettings())

    def test_zero_balances_need_no_quotes_or_exchange_rates(self):
        wealth = WealthSettings(properties=(Property('home', 'Home', 0, 'USD'),),
                                pensions=(Pension('pension', 'Pension', 'GBP', access=NOVEMBER),),
                                loans=(Loan('loan', 'Loan', 0, currency='DKK'),))
        overview = current_overview((Holding('ETF', 'World', 0),), (ManualHolding(0, 'HUF'),),
                                    {'JPY': 0}, MarketData({}, {}), wealth)
        self.assertEqual(overview.net_worth, 0)
        self.assertFalse(any(overview.allocations.values()))

    def test_overflow_reports_a_clear_value_error(self):
        wealth = WealthSettings(properties=(Property('a', 'A', 1e308, 'EUR'), Property('b', 'B', 1e308, 'EUR')))
        with self.assertRaisesRegex(ValueError, 'supported numeric range'):
            current_overview((), (), {}, MarketData({}, {}), wealth)


class CurrentTargetTest(unittest.TestCase):
    def allocation(self, stock=60, treasury=30, cash=10):
        return AllocationTargets({STOCK_MARKET: {'World': stock}, TREASURY: {'HUF': treasury},
                                  CASH: {'EUR': cash}})

    def test_target_mix_keeps_fixed_asset_values_and_identical_pie_fractions(self):
        wealth = WealthSettings(properties=(Property('home', 'Home', 4000, 'EUR'),),
                                pensions=(Pension('pension', 'Pension', 'EUR', balance=1000, access=NOVEMBER),),
                                loans=(Loan('loan', 'Loan', 2000, currency='EUR'),))
        overview = current_overview((), (), {'EUR': 1000}, MarketData({}, {}), wealth)
        before = dict(overview.allocations)
        for allocation in (self.allocation(), self.allocation(20, 80, 0)):
            weights = {parent: dict(values) for parent, values in allocation.weights.items()}
            targets = overview.target_values(allocation)
            self.assertEqual(fsum(targets.values()), overview.assets_total)
            self.assertNotIn('Loans', targets)
            for parent, expected in allocation.totals.items():
                self.assertAlmostEqual(targets[parent], expected * 10)
            for name in ('Properties', 'Pension funds'):
                self.assertEqual(targets[name], before[name])
                self.assertEqual(targets[name] / fsum(targets.values()), before[name] / overview.assets_total)
            self.assertEqual(allocation.weights, weights)
            targets['Properties'] = 0
            self.assertEqual(dict(overview.allocations), before)
        self.assertEqual((overview.investment_total, overview.net_worth), (1000, 4000))

    def test_negative_cash_does_not_reduce_the_display_pool_or_change_net_wealth(self):
        overview = current_overview((), (), {'EUR': 100, 'USD': -200}, MarketData({}, {'USD': .5}),
                                    WealthSettings(properties=(Property('home', 'Home', 300, 'EUR'),)))
        targets = overview.target_values(self.allocation())
        self.assertEqual(targets, {STOCK_MARKET: 60, TREASURY: 30, CASH: 10,
                                  'Properties': 300, 'Pension funds': 0})
        self.assertEqual(fsum(targets.values()), overview.assets_total)
        self.assertEqual((overview.investment_total, overview.cash_deficit, overview.net_worth), (0, 100, 300))

    def test_property_only_and_empty_portfolios_have_no_invented_investment_assets(self):
        for wealth in (WealthSettings(), WealthSettings(properties=(Property('home', 'Home', 300, 'EUR'),))):
            with self.subTest(wealth=wealth):
                overview = current_overview((), (), {}, MarketData({}, {}), wealth)
                self.assertEqual(overview.target_values(self.allocation()), overview.allocations)

    def test_large_property_does_not_erase_a_small_investment_pool(self):
        overview = current_overview((), (), {'EUR': .01}, MarketData({}, {}),
                                    WealthSettings(properties=(Property('home', 'Home', 1e25, 'EUR'),)))
        targets = overview.target_values(self.allocation())
        self.assertEqual(targets['Properties'], 1e25)
        self.assertEqual(targets[STOCK_MARKET], .006)
        self.assertEqual(targets[TREASURY], .003)
        self.assertEqual(targets[CASH], .001)

    def test_extreme_finite_investments_do_not_overflow_during_percentage_scaling(self):
        overview = current_overview((), (), {'EUR': 1e308}, MarketData({}, {}), WealthSettings())
        for allocation in (self.allocation(), self.allocation(100, 0, 0)):
            with self.subTest(allocation=allocation):
                targets = overview.target_values(allocation)
                self.assertTrue(all(isfinite(value) for value in targets.values()))
                self.assertAlmostEqual(fsum(targets.values()) / overview.assets_total, 1)
                self.assertAlmostEqual(targets[STOCK_MARKET] / 1e308, allocation.totals[STOCK_MARKET] / 100)

    def test_accepted_target_sum_tolerance_cannot_change_the_gross_pool(self):
        overview = current_overview((), (), {'EUR': 1e12}, MarketData({}, {}), WealthSettings())
        targets = overview.target_values(self.allocation(60, 40.000000005, 0))
        self.assertAlmostEqual(fsum(targets.values()), overview.assets_total, delta=.0002)

    def test_requires_validated_allocation_targets(self):
        overview = current_overview((), (), {}, MarketData({}, {}), WealthSettings())
        for allocation in (None, {}, {STOCK_MARKET: 100}):
            with self.subTest(allocation=allocation), self.assertRaisesRegex(ValueError, 'validated allocation'):
                overview.target_values(allocation)


if __name__ == '__main__':
    unittest.main()
