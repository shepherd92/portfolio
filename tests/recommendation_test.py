from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from math import exp, inf, log, log1p, nan
import unittest

from support import calculator_allocation as allocation

from portfolio.calculator import (CalculatorSettings, CashFlow, ReturnAssumptions,
                                  estimate_market_return, expense_cash_date, recommend, return_weights)

from portfolio.models import AllocationTargets, CASH, STOCK_MARKET, TREASURY


class RecommendationTests(unittest.TestCase):
    def recommend(self, settings, as_of, targets=None, *, total):
        return recommend(
            replace(settings, returns=ReturnAssumptions(0, 0, 0, 0)),
            as_of,
            targets or allocation(),
            total=total,
        )

    def test_defaults_and_emergency_money_are_separate_from_long_term_savings(self):
        settings = CalculatorSettings(outgoings=(CashFlow('Living costs', 100000, date.min, frequency='monthly'),))
        result = self.recommend(settings, date(2026, 10, 16), total=1_000_000)
        self.assertEqual([(row.amount, row.stock, row.mak) for row in result.rows],
                         [(600_000, 0, 600_000), (200_000, 0, 0), (200_000, 120_000, 80_000)])
        self.assertEqual((result.total, result.stock, result.mak), (1_000_000, 120_000, 680_000))
        self.assertAlmostEqual(result.stock_percent, 12)
        self.assertAlmostEqual(result.mak_percent, 68)
        self.assertAlmostEqual(result.cash_percent, 20)
        self.assertEqual(result.shortfall, 0)
        self.assertEqual(result.rows[-1].name, 'Long-term allocation')
        with self.assertRaises(FrozenInstanceError):
            settings.emergency_months = 12

    def test_expense_weights_are_linear_clamped_and_use_calendar_years(self):
        today = date(2024, 2, 29)
        dates = (date(2024, 4, 28), date(2024, 4, 29), date(2024, 8, 8),
                 date(2025, 4, 28), date(2025, 5, 1))
        settings = CalculatorSettings(horizon_years=1, outgoings=tuple((CashFlow(str(due), 1000, due) for due in dates)))
        result = self.recommend(settings, today, total=10_000)
        stocks = [row.stock for row in result.rows[2:-1]]
        self.assertEqual(stocks[:2], [0, 0])
        self.assertAlmostEqual(stocks[2], 1_000 * 93 / 365)
        self.assertAlmostEqual(stocks[3], 1_000 * 338 / 365)
        self.assertEqual(stocks[4], 1_000)
        four_year = self.recommend(
            replace(settings, horizon_years=4, outgoings=(CashFlow('Leap anniversary', 1000, date(2028, 5, 1)),)),
            today,
            total=10000,
        )
        self.assertEqual(four_year.rows[2].stock, 1_000)

    def test_overall_allocation_is_weighted_by_full_reserved_amounts(self):
        today = date(2025, 1, 1)
        settings = CalculatorSettings(
            emergency_months=10,
            horizon_years=1,
            outgoings=(
                CashFlow('Living costs', 100, date.min, frequency='monthly'),
                CashFlow('Due now', 2000, today),
                CashFlow('Later', 3000, date(2026, 3, 1)),
            ),
        )
        result = self.recommend(settings, today, allocation(25), total=10_000)
        self.assertEqual(result.rows[-1].amount, 3_800)
        self.assertEqual((result.stock, result.mak), (3_950, 3_850))
        self.assertAlmostEqual(result.stock_percent, 39.5)
        self.assertAlmostEqual(result.mak_percent, 38.5)
        self.assertAlmostEqual(result.cash_percent, 22)
        self.assertEqual(sum(row.amount for row in result.rows), 10_000)
        for row in result.rows:
            self.assertAlmostEqual(row.stock + row.mak + row.cash, row.amount)

    def test_zero_and_overcommitted_portfolios_fund_only_available_money_and_report_shortfall(self):
        empty = self.recommend(CalculatorSettings(), date.today(), total=0)
        self.assertEqual((empty.total, empty.stock, empty.mak, empty.cash, empty.shortfall), (0, 0, 0, 0, 0))
        settings = CalculatorSettings(
            outgoings=(
                CashFlow('Living costs', 20, date.min, frequency='monthly'),
                CashFlow('Bill', 50, date.today()),
            ),
        )
        result = self.recommend(settings, date.today(), total=100)
        self.assertEqual((result.total, result.stock, result.mak, result.shortfall), (100, 0, 10, 110))
        self.assertEqual([row.amount for row in result.rows], [10, 40, 50, 0])
        huge = CalculatorSettings(
            emergency_months=120,
            outgoings=(CashFlow('Living costs', 1e+308, date.min, frequency='monthly'),),
        )
        result = self.recommend(huge, date.today(), total=1e308)
        self.assertEqual((result.total, result.stock, result.mak), (1e308, 0, 0))
        self.assertEqual(result.shortfall, Decimal('1.21e310'))

    def test_overcommit_protects_mak_component_before_funding_stocks(self):
        today = date(2024, 1, 1)
        due = date(2024, 9, 2)
        settings = CalculatorSettings(horizon_years=1, outgoings=(CashFlow('Cost', 100, due),))
        defensive = 100 * (1 - 182 / 366)
        for stock_percent in (60, 100):
            result = self.recommend(settings, today, allocation(stock_percent), total=80)
            self.assertAlmostEqual(result.stock, 80 - defensive)
            self.assertAlmostEqual(result.mak, defensive)
            self.assertEqual(result.shortfall, 20)
            self.assertEqual(result.rows[2].amount, 80)
            self.assertEqual(result.rows[2].future_amount, 100)
            self.assertAlmostEqual(result.stock_percent, (80 - defensive) / 80 * 100)
            self.assertAlmostEqual(result.mak_percent, defensive / 80 * 100)

    def test_overcommit_funds_emergency_then_expense_components_chronologically(self):
        today = date(2024, 1, 1)
        settings = CalculatorSettings(
            horizon_years=1,
            outgoings=(
                CashFlow('Living costs', 10, date.min, frequency='monthly'),
                CashFlow('Later', 100, date(2025, 3, 1)),
                CashFlow('Sooner', 100, date(2024, 9, 2)),
            ),
        )
        result = self.recommend(settings, today, total=150)
        self.assertEqual([row.name for row in result.rows],
                         ['Emergency reserve', 'Bank cash buffer', 'Later', 'Sooner', 'Long-term allocation'])
        defensive = 100 * (1 - 182 / 366)
        self.assertEqual([(row.amount, row.stock, row.mak) for row in result.rows],
                         [(60, 0, 60), (20, 0, 0), (0, 0, 0),
                          (70, 70 - defensive, defensive), (0, 0, 0)])
        self.assertEqual((result.total, result.shortfall), (150, 130))
        self.assertAlmostEqual(result.stock, 70 - defensive)
        self.assertAlmostEqual(result.mak, 60 + defensive)
        constrained = self.recommend(settings, today, total=100)
        self.assertEqual([(row.amount, row.stock, row.mak) for row in constrained.rows],
                         [(60, 0, 60), (20, 0, 0), (0, 0, 0), (20, 0, 20), (0, 0, 0)])
        self.assertEqual((constrained.total, constrained.stock, constrained.mak), (100, 0, 80))

    def test_exactly_funded_expenses_keep_original_split_and_no_long_term_allocation(self):
        today = date(2024, 1, 1)
        result = self.recommend(
            CalculatorSettings(horizon_years=1, outgoings=(CashFlow('Cost', 100, date(2024, 9, 2)),)),
            today,
            total=100,
        )
        self.assertEqual((result.total, result.shortfall), (100, 0))
        self.assertAlmostEqual(result.stock, 100 * 182 / 366)
        self.assertAlmostEqual(result.mak, 100 * (1 - 182 / 366))
        self.assertEqual(result.rows[-1].amount, 0)

    def test_bank_cash_is_additional_and_its_horizon_does_not_reduce_emergency_mak(self):
        for cash_months in (0, 1, 2, 5):
            with self.subTest(cash_months=cash_months):
                result = self.recommend(
                    CalculatorSettings(
                        emergency_months=6,
                        cash_months=cash_months,
                        outgoings=(CashFlow('Living costs', 10, date.min, frequency='monthly'),),
                    ),
                    date(2025, 1, 1),
                    total=1000,
                )
                emergency, bank = result.rows[:2]
                self.assertEqual((emergency.amount, emergency.mak, emergency.cash), (60, 60, 0))
                self.assertEqual((bank.amount, bank.mak, bank.cash), (10 * cash_months, 0, 10 * cash_months))
                self.assertEqual(result.rows[-1].amount, 940 - 10 * cash_months)

    def test_expense_cash_deadlines_start_two_or_more_months_earlier_and_are_inclusive(self):
        cases = ((date(2025, 4, 1), 2, date(2025, 2, 1)),
                 (date(2024, 4, 1), 2, date(2024, 2, 1)),
                 (date(2025, 1, 1), 2, date(2024, 11, 1)),
                 (date(2025, 5, 1), 3, date(2025, 2, 1)),
                 (date(2025, 4, 1), 0, date(2025, 2, 1)),
                 (date(2025, 4, 1), 1, date(2025, 2, 1)),
                 (date.min, 2, date.min))
        for due, months, deadline in cases:
            with self.subTest(due=due, cash_months=months):
                self.assertEqual(expense_cash_date(due, months), deadline)
                settings = CalculatorSettings(cash_months=months, outgoings=(CashFlow('Cost', 100, due),))
                for today in (deadline, due, due + timedelta(days=1)):
                    row = self.recommend(settings, today, total=1_000).rows[2]
                    self.assertEqual((row.amount, row.cash, row.mak, row.stock), (100, 100, 0, 0))
                if deadline > date.min:
                    row = self.recommend(settings, deadline - timedelta(days=1), total=1_000).rows[2]
                    self.assertEqual(row.cash, 0)
                    self.assertGreater(row.stock, 0)
                    self.assertAlmostEqual(row.stock + row.mak, row.amount)

    def test_underfunding_protects_bank_then_chronological_expense_cash_before_mak(self):
        today = date(2025, 1, 1)
        settings = CalculatorSettings(
            outgoings=(
                CashFlow('Living costs', 10, date.min, frequency='monthly'),
                CashFlow('Later', 100, date(2025, 2, 1)),
                CashFlow('Now', 70, today),
                CashFlow('Future', 100, date(2026, 1, 1)),
            ),
        )
        result = self.recommend(settings, today, total=160)
        self.assertEqual([row.amount for row in result.rows], [0, 20, 70, 70, 0, 0])
        self.assertEqual((result.cash, result.mak, result.stock, result.shortfall), (160, 0, 0, 190))

    def test_invalid_settings_and_expenses_are_rejected_without_coercion(self):
        cases = [{'outgoings': None}, {'outgoings': ({'name': 'Bad'},)}]
        cases += [{name: value} for name in ('emergency_months', 'cash_months')
                  for value in (-1, 121, 6., True)]
        cases += [{'horizon_years': value} for value in (0, 101, 7., True)]
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                CalculatorSettings(**values)
        for name, amount in (('', 1), (' ', 1), (None, 1), ('A', 0), ('A', -1), ('A', inf),
                             ('A', False), ('A', .01), ('A', 1.001), ('A', 1_000_000_000.25)):
            with self.subTest(name=name, amount=amount), self.assertRaises(ValueError):
                CashFlow(name, amount, date.min, frequency='monthly')
        for name, amount, due in (('', 1, date.today()), (' ', 1, date.today()),
                                  ('A', 0, date.today()), ('A', -1, date.today()),
                                  ('A', inf, date.today()), ('A', True, date.today()),
                                  ('A', .01, date.today()), ('A', 1.001, date.today()),
                                  ('A', 1_000_000_000.25, date.today()),
                                  ('A', 1, '2026-01-01'), ('A', 1, datetime.now())):
            with self.subTest(name=name, amount=amount, due=due), self.assertRaises(ValueError):
                CashFlow(name, amount, due)
        with self.assertRaisesRegex(ValueError, 'calendar'):
            self.recommend(CalculatorSettings(), date(9999, 1, 1), total=1)

    def test_current_portfolio_value_validates_wealth_and_preserves_quote_fractions(self):
        settings = CalculatorSettings()
        for total in (-1, nan, inf, True, '100'):
            with self.subTest(total=total), self.assertRaisesRegex(ValueError, 'Portfolio value'):
                self.recommend(settings, date.today(), total=total)
        for total in (.01, 1.001, 1_000_000_000.25):
            with self.subTest(total=total):
                result = self.recommend(settings, date.today(), total=total)
                self.assertAlmostEqual(result.total, total)


class ExpectedReturnTests(unittest.TestCase):
    def setUp(self):
        self.today = date(2025, 1, 1)
        self.returns = ReturnAssumptions(equities=8, gold=2, bonds=4, mak=5)
        self.weights = {'Equities': 60, 'Gold': 20, 'Bonds': 20}

    def calculate(self, **values):
        targets = AllocationTargets({STOCK_MARKET: {'Stocks': 36, 'Gold': 12, 'Debt': 12},
                                     TREASURY: {'HUF': 40}, CASH: {}},
                                    {'Stocks': 'Equities', 'Gold': 'Gold', 'Debt': 'Bonds'})
        settings = CalculatorSettings(**(dict(returns=self.returns) | values))
        return recommend(settings, self.today, targets, total=1_000_000)

    def test_return_weights_are_absolute_targets_and_ignore_unallocated_categories(self):
        targets = AllocationTargets(
            {STOCK_MARKET: {'US': 10, 'EU': 10, 'Debt': 30, 'Bullion': 10, 'Unused': 0},
             TREASURY: {'EUR': 15, 'HUF': 20}, CASH: {'EUR': 5}},
            {'US': 'Equities', 'EU': 'Equities', 'Debt': 'Bonds', 'Bullion': 'Gold'})
        self.assertEqual(return_weights(targets), {'Equities': 20, 'Bonds': 30, 'Gold': 10, 'MÁK': 35})
        self.assertEqual(return_weights(allocation(0)), {'Equities': 0, 'Bonds': 0, 'Gold': 0, 'MÁK': 100})

    def test_active_stock_targets_need_categories_even_when_reserves_consume_all_wealth(self):
        targets = replace(allocation(), categories={})
        settings = CalculatorSettings(
            returns=self.returns,
            outgoings=(CashFlow('Living costs', 100, date.min, frequency='monthly'),),
        )
        with self.assertRaisesRegex(ValueError, 'Global.*Asset classes tab'):
            return_weights(targets)
        with self.assertRaisesRegex(ValueError, 'Global.*Asset classes tab'):
            recommend(settings, self.today, targets, total=100)

    def test_market_return_and_overall_return_use_amount_weighted_current_mix(self):
        result = self.calculate()
        self.assertAlmostEqual(result.market_return, 6)
        self.assertEqual(result.mak_return, 5)
        self.assertAlmostEqual(result.expected_return, 5.6)
        self.assertAlmostEqual(estimate_market_return(self.returns, self.weights), result.market_return)
        self.assertAlmostEqual(estimate_market_return(self.returns, {'Equities': 6, 'Gold': 2, 'Bonds': 2}),
                               result.market_return)
        changed = self.calculate(returns=ReturnAssumptions(equities=9, gold=9, bonds=9, mak=5))
        self.assertEqual(changed.market_return, 9)
        self.assertAlmostEqual(changed.expected_return, 7.4)
        changed_mix = recommend(CalculatorSettings(returns=self.returns),
                                self.today, allocation(), total=1_000_000)
        self.assertEqual(changed_mix.market_return, 8)
        self.assertAlmostEqual(changed_mix.expected_return, 6.8)

    def test_cash_can_offset_a_higher_mak_return_in_the_overall_portfolio(self):
        settings = CalculatorSettings(returns=ReturnAssumptions(equities=16 / 3, mak=5.5))
        invested = recommend(settings, self.today, allocation(stock=10), total=1_000_000)
        with_cash = recommend(settings, self.today, allocation(stock=10, cash=2.8), total=1_000_000)
        self.assertAlmostEqual(invested.expected_return, 5.483333333333333)
        self.assertAlmostEqual(with_cash.stock_percent, 10)
        self.assertAlmostEqual(with_cash.mak_percent, 87.2)
        self.assertAlmostEqual(with_cash.cash_percent, 2.8)
        # Cash earns zero: both returns round to 5.33%, despite different values.
        self.assertAlmostEqual(with_cash.expected_return, 5.329333333333333)
        self.assertNotEqual(with_cash.expected_return, with_cash.market_return)
        self.assertEqual(round(with_cash.expected_return, 2), round(with_cash.market_return, 2))

    def test_discount_and_stock_exposure_end_at_cash_deadline(self):
        horizon_days = (date(2032, 1, 1) - self.today).days
        for due, deadline in ((date(2026, 3, 1), date(2026, 1, 1)),
                              (date(2032, 3, 1), date(2032, 1, 1)),
                              (date(2034, 3, 1), date(2034, 1, 1))):
            days = (deadline - self.today).days
            with self.subTest(due=due):
                result = self.calculate(outgoings=(CashFlow('Future cost', 100000, due),))
                row = result.rows[2]
                years, horizon = days / 365.2425, horizon_days / 365.2425
                # Independent integral of log(1.05 + .01 * time_to_deadline / horizon).
                endpoint = 1.05 + .01 * min(years, horizon) / horizon
                primitive = lambda value: value * log(value) - value
                growth = (primitive(endpoint) - primitive(1.05)) * horizon / .01
                growth += max(0, years - horizon) * log1p(.06)
                self.assertEqual(row.future_amount, 100_000)
                self.assertAlmostEqual(row.amount * exp(growth), row.future_amount, delta=.01)
                self.assertAlmostEqual(row.stock / row.amount, min(days / horizon_days, 1))
                self.assertAlmostEqual(row.stock + row.mak + row.cash, row.amount)
                self.assertAlmostEqual(result.stock + result.mak, 1_000_000)
                self.assertAlmostEqual(result.stock_percent + result.mak_percent, 100)
                if days <= horizon_days:
                    constant_growth = years * log1p(.05 + .01 * days / horizon_days)
                    self.assertGreater(row.amount, row.future_amount * exp(-constant_growth))
        reserves = [self.calculate(outgoings=(CashFlow('Cost', 100000, self.today + timedelta(days=days)),)).rows[2].amount
                    for days in (0, 1, 365, horizon_days, horizon_days + 730)]
        self.assertEqual(reserves[0], reserves[1])
        self.assertTrue(all(later < earlier for earlier, later in zip(reserves[1:], reserves[2:])))

    def test_equal_returns_reduce_to_compound_discount_and_negative_returns_increase_reserve(self):
        due = date(2026, 1, 1)
        settings = CalculatorSettings(returns=ReturnAssumptions(10, 10, 10, 10), outgoings=(CashFlow('Cost', 100000, due),))
        result = recommend(settings, self.today, allocation(), total=200_000)
        self.assertAlmostEqual(result.rows[2].amount, 100_000 / 1.1 ** (304 / 365.2425))
        negative = recommend(replace(settings, returns=ReturnAssumptions(-10, -10, -10, -10)),
                             self.today, allocation(), total=200_000)
        self.assertGreater(negative.rows[2].amount, 100_000)
        overdue = recommend(
            replace(settings, outgoings=(CashFlow('Past', 100000, self.today - timedelta(days=1)),)),
            self.today,
            allocation(),
            total=200000,
        )
        self.assertEqual((overdue.rows[2].amount, overdue.rows[2].stock), (100_000, 0))

    def test_discounted_reserves_can_fit_when_future_amounts_exceed_current_savings(self):
        settings = CalculatorSettings(
            returns=ReturnAssumptions(10, 10, 10, 10),
            outgoings=(CashFlow('Cost', 110000, date(2027, 1, 1)),),
        )
        result = recommend(settings, self.today, allocation(), total=100_000)
        self.assertGreater(result.rows[-1].amount, 0)
        self.assertAlmostEqual(result.total, 100_000)
        constrained = recommend(settings, self.today, allocation(), total=1)
        self.assertEqual((constrained.total, constrained.stock, constrained.mak), (1, 0, 1))
        self.assertAlmostEqual(float(constrained.shortfall), result.rows[2].amount - 1)

    def test_market_mix_is_required_only_when_stock_is_funded_and_mnb_must_be_resolved(self):
        settings = CalculatorSettings(returns=self.returns)
        self.assertEqual(estimate_market_return(self.returns, {}), 0)
        all_mak = recommend(settings, self.today, allocation(0), total=100)
        self.assertEqual((all_mak.stock, all_mak.market_return, all_mak.expected_return), (0, 0, 5))
        emergency = recommend(
            replace(
                settings,
                emergency_months=1,
                outgoings=(CashFlow('Living costs', 100, date.min, frequency='monthly'),),
            ),
            self.today,
            allocation(),
            total=100,
        )
        self.assertEqual(emergency.stock, 0)
        with self.assertRaisesRegex(ValueError, 'MNB base rate'):
            recommend(CalculatorSettings(), self.today, allocation(), total=100)
        self.assertEqual(estimate_market_return(self.returns, {'Other': 0, 'Equities': 10}), 8)
        for weights in ({'Other': 100}, {'Equities': -1}, {'Equities': True}, {'Gold': inf}, []):
            with self.subTest(weights=weights), self.assertRaises(ValueError):
                estimate_market_return(self.returns, weights)

    def test_invalid_returns_and_projection_underflow_overflow_are_rejected(self):
        for value in (True, '6', nan, inf, -100, -101):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ReturnAssumptions(equities=value)
        for rate in (-99.9, 100):
            settings = CalculatorSettings(
                returns=ReturnAssumptions(rate, rate, rate, rate),
                outgoings=(CashFlow('Too far away', 1, date(9999, 1, 1)),),
            )
            with self.subTest(rate=rate), self.assertRaisesRegex(ValueError, 'numeric range'):
                recommend(settings, self.today, allocation(), total=100)


if __name__ == '__main__':
    unittest.main()
