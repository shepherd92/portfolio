"""Projection wealth accounting, expense timing and protected reserves."""

from dataclasses import replace
from datetime import date
from math import exp, fsum, isfinite, log1p
import unittest

from portfolio.calculator import CalculatorSettings, CashFlow, ReturnAssumptions, recommend
from portfolio.models import AllocationTargets, CASH, STOCK_MARKET, TREASURY
from portfolio.projection import project


TODAY = date(2026, 1, 31)
ALLOCATION = AllocationTargets({STOCK_MARKET: {'World': 60}, TREASURY: {'HUF': 40}, CASH: {}},
                               {'World': 'Equities'})


def settings(**values):
    return CalculatorSettings(returns=ReturnAssumptions(equities=0, bonds=0, gold=0, mak=0), **values)


def total_at(result, index):
    return fsum(values[index] for values in result.portfolio.values())


class ProjectionTests(unittest.TestCase):
    def test_future_income_keeps_the_full_expense_stock_path_and_adds_only_owned_money(self):
        today = date(2026, 1, 1)
        income_date, due = date(2027, 1, 1), date(2032, 3, 1)
        inputs = settings(emergency_months=0, cash_months=0, horizon_years=10,
                          incomes=(CashFlow('Bonus', 200, income_date),),
                          outgoings=(CashFlow('Goal', 1000, due),))
        reserve = recommend(inputs, today, ALLOCATION, total=1000).rows[2]
        baseline = project(replace(inputs, incomes=()), today, ALLOCATION, total=1000, years=7)
        funded = project(inputs, today, ALLOCATION, total=reserve.amount, years=7)
        for day in set(baseline.dates) & set(funded.dates):
            first = max(index for index, value in enumerate(baseline.dates) if value == day)
            second = max(index for index, value in enumerate(funded.dates) if value == day)
            self.assertAlmostEqual(baseline.portfolio[STOCK_MARKET][first],
                                   funded.portfolio[STOCK_MARKET][second])
            expected = 800 + (200 if day >= income_date else 0) - (1000 if day >= due else 0)
            self.assertAlmostEqual(total_at(funded, second), expected)
        self.assertFalse(funded.shortfalls)
        self.assertFalse(funded.deadline_shortfalls)

    def test_defensive_income_cap_never_borrows_during_adverse_glidepaths(self):
        today, deadline, due = date(2026, 1, 1), date(2035, 1, 1), date(2035, 3, 1)
        for stock_return, mak_return in ((-95, 20), (-99.99, -20), (2, 8), (8, 2), (-20, -30), (6, 6)):
            with self.subTest(stock_return=stock_return, mak_return=mak_return):
                inputs = CalculatorSettings(
                    emergency_months=0, cash_months=0, horizon_years=10,
                    incomes=(CashFlow('Bonus', 100000, deadline),),
                    outgoings=(CashFlow('Goal', 1000, due),),
                    returns=ReturnAssumptions(equities=stock_return, mak=mak_return),
                )
                row = recommend(inputs, today, ALLOCATION, total=1e10).rows[2]
                result = project(inputs, today, ALLOCATION, total=row.amount, years=10)
                self.assertAlmostEqual(result.portfolio[STOCK_MARKET][0], row.stock)
                self.assertAlmostEqual(result.portfolio[TREASURY][0], row.mak)
                self.assertTrue(all(value >= 0 for values in result.portfolio.values() for value in values))
                ready = max(index for index, day in enumerate(result.dates) if day == deadline)
                self.assertAlmostEqual(result.purpose['Planned expenses'][ready], 1000, places=7)
                self.assertFalse(result.shortfalls)
                self.assertFalse(result.deadline_shortfalls)

    def test_small_income_commitment_still_grows_to_fund_its_expense(self):
        inputs = CalculatorSettings(
            incomes=(CashFlow('Salary', 100, date(2026, 1, 1), frequency='monthly'),),
            emergency_months=0,
            cash_months=0,
            horizon_years=100,
            returns=ReturnAssumptions(equities=60, mak=60),
            outgoings=(CashFlow('Goal', 1000000, date(2125, 12, 1)),),
        )
        today = date(2026, 1, 1)
        initial = recommend(inputs, today, ALLOCATION, total=1000000).rows[2].amount
        result = project(inputs, today, ALLOCATION, total=initial, years=100)
        self.assertFalse(result.shortfalls)
        self.assertFalse(result.deadline_shortfalls)

    def test_combined_expense_growth_overflow_is_a_validation_error(self):
        inputs = CalculatorSettings(
            returns=ReturnAssumptions(equities=100, mak=100),
            outgoings=tuple((CashFlow(str(index), 1e+308, date(2030, 1, 1)) for index in range(5))),
        )
        today = date(2026, 1, 1)
        initial = recommend(inputs, today, ALLOCATION, total=1e308)
        total = fsum(row.amount for row in initial.rows[2:-1])
        with self.assertRaisesRegex(ValueError, 'numeric range'):
            project(inputs, today, ALLOCATION, total=total, years=10)

    def test_initial_values_match_recommendation_and_both_partitions_conserve(self):
        inputs = settings(
            incomes=(CashFlow('Salary', 250, TODAY, frequency='monthly'),),
            outgoings=(
                CashFlow('Living costs', 100, date.min, frequency='monthly'),
                CashFlow('Car', 10000, date(2028, 8, 30)),
            ),
        )
        inputs = replace(inputs, returns=ReturnAssumptions(equities=8, mak=4))
        expected = recommend(inputs, TODAY, ALLOCATION, total=20000)
        result = project(inputs, TODAY, ALLOCATION, total=20000, years=3)
        for name, amount in ((STOCK_MARKET, expected.stock), (TREASURY, expected.mak), (CASH, expected.cash)):
            self.assertAlmostEqual(result.portfolio[name][0], amount)
        self.assertEqual(result.dates[0], TODAY)
        self.assertEqual(result.dates[-1], date(2029, 1, 31))
        self.assertEqual(tuple(sorted(result.dates)), result.dates)
        for index in range(len(result.dates)):
            self.assertAlmostEqual(
                total_at(result, index),
                fsum((values[index] for values in result.purpose.values())),
            )
            self.assertTrue(all(isfinite(values[index]) and values[index] >= 0
                                for values in (*result.portfolio.values(), *result.purpose.values())))
        self.assertFalse(result.shortfalls)

    def test_zero_return_conserves_income_and_expenses_in_the_same_month(self):
        inputs = settings(
            incomes=(CashFlow('Salary', 350, TODAY, frequency='monthly'),),
            outgoings=(
                CashFlow('Living costs', 100, date.min, frequency='monthly'),
                CashFlow('First', 200, date(2026, 4, 30)),
                CashFlow('Second', 500, date(2026, 9, 1)),
            ),
        )
        result = project(inputs, TODAY, ALLOCATION, total=10000, years=1)
        self.assertEqual(total_at(result, -1), 10000 + 12 * 250 - 700)
        february = [index for index, day in enumerate(result.dates) if day == date(2026, 2, 1)]
        march = [index for index, day in enumerate(result.dates) if day == date(2026, 3, 1)]
        self.assertEqual(total_at(result, february[-1]) - total_at(result, february[0]), 250)
        self.assertEqual(total_at(result, march[-1]) - total_at(result, march[0]), 250)
        for expense in inputs.scheduled_payments(TODAY):
            indices = [index for index, day in enumerate(result.dates) if day == expense.due]
            salary = 250
            self.assertEqual(total_at(result, indices[0]) - total_at(result, indices[-1]), expense.amount - salary)

    def test_future_income_is_not_owned_today_or_counted_twice_between_goals(self):
        inputs = settings(
            emergency_months=0,
            cash_months=0,
            incomes=(CashFlow('Salary', 100, TODAY, frequency='monthly'),),
            outgoings=(CashFlow('A', 300, date(2026, 6, 30)), CashFlow('B', 300, date(2026, 7, 30))),
        )
        result = project(inputs, TODAY, ALLOCATION, total=1000, years=1)
        self.assertEqual(total_at(result, 0), 1000)
        self.assertEqual(total_at(result, -1), 1000 + 1200 - 600)
        self.assertFalse(result.shortfalls)

    def test_income_below_spending_is_zero_and_living_costs_are_not_deducted_twice(self):
        inputs = settings(incomes=(CashFlow('Salary', 50, TODAY, frequency='monthly'),), outgoings=(CashFlow('Living costs', 100, date.min, frequency='monthly'),))
        result = project(inputs, TODAY, ALLOCATION, total=10000, years=1)
        self.assertEqual(total_at(result, -1), 10000)
        self.assertEqual(set(result.purpose['Emergency fund']), {600})
        self.assertEqual(set(result.purpose['Monthly living costs']), {200})

    def test_fixed_emergency_and_cash_buffers_are_separate_and_full(self):
        inputs = settings(
            incomes=(CashFlow('Salary', 1000, TODAY, frequency='monthly'),),
            emergency_months=6,
            cash_months=4,
            outgoings=(CashFlow('Living costs', 100, date.min, frequency='monthly'),),
        )
        result = project(inputs, TODAY, ALLOCATION, total=2000, years=1)
        self.assertEqual(set(result.purpose['Emergency fund']), {600})
        self.assertEqual(set(result.purpose['Monthly living costs']), {400})
        self.assertEqual(result.purpose['Long-term investment'][0], 1000)
        self.assertEqual(result.purpose['Long-term investment'][-1], 1000 + 12 * 900)

    def test_expense_is_cash_at_deadline_but_paid_only_at_due_date(self):
        due = date(2026, 7, 1)
        inputs = settings(emergency_months=0, cash_months=2, outgoings=(CashFlow('Trip', 1000, due),))
        result = project(inputs, TODAY, ALLOCATION, total=1000, years=1)
        indices = [index for index, day in enumerate(result.dates) if day == date(2026, 5, 1)]
        self.assertEqual(len(indices), 2)
        self.assertEqual([result.portfolio[CASH][index] for index in indices], [0, 1000])
        self.assertEqual([result.portfolio[TREASURY][index] for index in indices], [1000, 0])
        deadline = indices[-1]
        self.assertEqual(result.portfolio[CASH][deadline], 1000)
        self.assertEqual(result.purpose['Planned expenses'][deadline], 1000)
        before = result.dates.index(due)
        after = max(index for index, day in enumerate(result.dates) if day == due)
        self.assertEqual(total_at(result, before), 1000)
        self.assertEqual(total_at(result, after), 0)
        self.assertEqual(total_at(result, -1), 0)

    def test_longer_cash_horizon_moves_deadline_without_advancing_payment(self):
        inputs = settings(emergency_months=0, cash_months=4, outgoings=(CashFlow('Trip', 1000, date(2026, 7, 1)),))
        result = project(inputs, TODAY, ALLOCATION, total=1000, years=1)
        deadline = max(index for index, day in enumerate(result.dates) if day == date(2026, 3, 1))
        self.assertEqual(result.portfolio[CASH][deadline], 1000)
        self.assertEqual(total_at(result, deadline), 1000)
        self.assertEqual(total_at(result, -1), 0)

    def test_discounted_expense_and_committed_income_grow_to_exact_payment(self):
        due = date(2030, 8, 1)
        inputs = settings(emergency_months=0, cash_months=0, incomes=(CashFlow('Salary', 100, TODAY, frequency='monthly'),), outgoings=(CashFlow('Goal', 20000, due),))
        inputs = replace(inputs, returns=ReturnAssumptions(equities=9, mak=5))
        recommendation = recommend(inputs, TODAY, ALLOCATION, total=50000)
        initial = recommendation.rows[2].amount
        result = project(inputs, TODAY, ALLOCATION, total=initial, years=5)
        deadline = max(index for index, day in enumerate(result.dates) if day == date(2030, 6, 1))
        self.assertAlmostEqual(result.purpose['Planned expenses'][deadline], 20000, places=7)
        self.assertAlmostEqual(result.portfolio[CASH][deadline], 20000, places=7)
        self.assertFalse(result.shortfalls)
        paid = [index for index, day in enumerate(result.dates) if day == due]
        self.assertAlmostEqual(total_at(result, paid[0]) - total_at(result, paid[-1]), 20000 - 100, places=7)

    def test_due_today_and_overdue_expenses_are_paid_now_once(self):
        inputs = settings(
            emergency_months=0,
            cash_months=0,
            outgoings=(CashFlow('Overdue', 100, date(2025, 12, 31)), CashFlow('Now', 200, TODAY)),
        )
        result = project(inputs, TODAY, ALLOCATION, total=1000, years=1)
        self.assertEqual(result.dates[:2], (TODAY, TODAY))
        self.assertEqual(total_at(result, 0), 1000)
        self.assertEqual(total_at(result, 1), 700)
        self.assertEqual(total_at(result, -1), 700)

    def test_unaffordable_expense_records_shortfall_and_never_raids_fixed_buffers(self):
        inputs = settings(
            outgoings=(
                CashFlow('Living costs', 100, date.min, frequency='monthly'),
                CashFlow('Trip', 2000, date(2026, 7, 31)),
            ),
        )
        result = project(inputs, TODAY, ALLOCATION, total=1000, years=1)
        self.assertAlmostEqual(total_at(result, -1), 800)
        self.assertEqual(result.purpose['Emergency fund'][-1], 600)
        self.assertEqual(result.purpose['Monthly living costs'][-1], 200)
        self.assertEqual(result.shortfalls, ((date(2026, 7, 1), 1800),))
        self.assertTrue(all(value >= 0 for values in result.portfolio.values() for value in values))

    def test_savings_replenish_underfunded_fixed_buffers_before_planned_commitments(self):
        inputs = settings(
            incomes=(CashFlow('Salary', 200, TODAY, frequency='monthly'),),
            outgoings=(
                CashFlow('Living costs', 100, date.min, frequency='monthly'),
                CashFlow('Trip', 300, date(2026, 7, 31)),
            ),
        )
        result = project(inputs, TODAY, ALLOCATION, total=0, years=1)
        february = max(index for index, day in enumerate(result.dates) if day == date(2026, 2, 28))
        self.assertEqual(result.purpose['Monthly living costs'][february], 100)
        self.assertEqual(result.purpose['Emergency fund'][february], 0)
        self.assertEqual(result.purpose['Planned expenses'][february], 0)
        self.assertEqual(result.purpose['Emergency fund'][-1], 600)
        self.assertEqual(result.purpose['Monthly living costs'][-1], 200)
        self.assertEqual(total_at(result, -1), 1200)
        self.assertEqual(result.shortfalls, ((date(2026, 7, 1), 300),))

    def test_underfunded_expense_uses_available_long_term_money_at_cash_deadline(self):
        due = date(2028, 1, 1)
        inputs = settings(
            incomes=(CashFlow('Salary', 100, TODAY, frequency='monthly'),),
            outgoings=(CashFlow('Living costs', 100, date.min, frequency='monthly'), CashFlow('Goal', 1000, due)),
        )
        inputs = replace(inputs, returns=ReturnAssumptions(equities=5, mak=100))
        initial = recommend(inputs, TODAY, ALLOCATION, total=1000)
        self.assertGreater(initial.shortfall, 0)
        result = project(inputs, TODAY, ALLOCATION, total=1000, years=2)
        deadline = max(index for index, day in enumerate(result.dates) if day == date(2027, 11, 1))
        self.assertAlmostEqual(result.purpose['Planned expenses'][deadline], 1000)
        self.assertGreater(result.purpose['Long-term investment'][deadline], 0)
        self.assertFalse(result.deadline_shortfalls)
        self.assertFalse(result.shortfalls)

    def test_income_on_cash_deadline_is_committed_once_and_remainder_funds_next_goal(self):
        today = date(2026, 1, 1)
        inputs = settings(
            emergency_months=0,
            cash_months=0,
            incomes=(CashFlow('Salary', 200, today, frequency='monthly'),),
            outgoings=(CashFlow('First', 100, date(2026, 4, 1)), CashFlow('Second', 200, date(2026, 5, 1))),
        )
        result = project(inputs, today, ALLOCATION, total=0, years=1)
        february = max(index for index, day in enumerate(result.dates) if day == date(2026, 2, 1))
        march = max(index for index, day in enumerate(result.dates) if day == date(2026, 3, 1))
        self.assertEqual(result.purpose['Planned expenses'][february], 200)
        self.assertEqual(result.portfolio[CASH][february], 100)
        self.assertEqual(result.portfolio[TREASURY][february], 100)
        self.assertEqual(result.purpose['Planned expenses'][march], 300)
        self.assertEqual(result.purpose['Long-term investment'][march], 100)
        self.assertFalse(result.deadline_shortfalls)
        self.assertFalse(result.shortfalls)

    def test_late_income_fills_expense_cash_but_does_not_erase_missed_deadline(self):
        inputs = settings(
            emergency_months=0,
            cash_months=0,
            incomes=(CashFlow('Salary', 100, TODAY, frequency='monthly'),),
            outgoings=(CashFlow('Goal', 600, date(2026, 8, 31)),),
        )
        result = project(inputs, TODAY, ALLOCATION, total=0, years=1)
        self.assertEqual(result.deadline_shortfalls, ((date(2026, 6, 1), 100),))
        july = max(index for index, day in enumerate(result.dates) if day == date(2026, 7, 31))
        self.assertEqual(result.purpose['Planned expenses'][july], 600)
        self.assertEqual(result.portfolio[CASH][july], 600)
        self.assertFalse(result.shortfalls)
        self.assertEqual(total_at(result, -1), 600)

    def test_emergency_interest_is_released_to_long_term(self):
        inputs = settings(
            emergency_months=6,
            cash_months=2,
            outgoings=(CashFlow('Living costs', 100, date.min, frequency='monthly'),),
        )
        inputs = replace(inputs, returns=ReturnAssumptions(equities=6, mak=5))
        result = project(inputs, TODAY, ALLOCATION, total=800, years=1)
        self.assertEqual(set(result.purpose['Emergency fund']), {600})
        self.assertEqual(set(result.purpose['Monthly living costs']), {200})
        self.assertGreater(result.purpose['Long-term investment'][-1], 0)
        self.assertGreater(total_at(result, -1), 800)

    def test_long_term_growth_uses_weighted_category_assumptions(self):
        allocation = AllocationTargets({STOCK_MARKET: {'Shares': 30, 'Gold': 20, 'Bonds': 10},
                                         TREASURY: {'EUR': 30}, CASH: {'EUR': 10}},
                                        {'Shares': 'Equities', 'Gold': 'Gold', 'Bonds': 'Bonds'})
        inputs = replace(settings(emergency_months=0, cash_months=0),
                         returns=ReturnAssumptions(equities=10, gold=2, bonds=4, mak=5))
        result = project(inputs, TODAY, allocation, total=10000, years=1)
        annual = .3 * 10 + .2 * 2 + .1 * 4 + .3 * 5
        expected = 10000 * exp(log1p(annual / 100) * 365 / 365.2425)
        self.assertAlmostEqual(total_at(result, -1), expected)

    def test_currencies_are_fixed_and_both_partitions_convert_once(self):
        inputs = settings(
            incomes=(CashFlow('Salary', 10, TODAY, 'GBP', frequency='monthly'),),
            outgoings=(
                CashFlow('Rent', 5, date.min, 'EUR', frequency='monthly'),
                CashFlow('Food', 2000, date.min, frequency='monthly'),
                CashFlow('Goal', 100, date(2026, 8, 1), 'USD'),
            ),
        )
        rates = {'EUR': 400, 'GBP': 500, 'USD': 350}
        huf = project(inputs, TODAY, ALLOCATION, total=100000, years=1, rates=rates)
        eur = project(inputs, TODAY, ALLOCATION, total=100000, years=1, rates=rates, currency='EUR')
        self.assertEqual(eur.currency, 'EUR')
        self.assertEqual(huf.dates, eur.dates)
        self.assertAlmostEqual(total_at(eur, -1), (100000 + 12000 - 35000) / 400)
        for left, right in ((huf.portfolio, eur.portfolio), (huf.purpose, eur.purpose)):
            for name in left:
                for a, b in zip(left[name], right[name]):
                    self.assertAlmostEqual(a / 400, b)

    def test_rejects_invalid_horizons_missing_fx_and_numeric_overflow(self):
        inputs = settings()
        for years in (0, 101, 1.5, True):
            with self.subTest(years=years), self.assertRaisesRegex(ValueError, 'horizon'):
                project(inputs, TODAY, ALLOCATION, total=1000, years=years)
        with self.assertRaisesRegex(ValueError, 'Missing exchange rate'):
            project(inputs, TODAY, ALLOCATION, total=1000, currency='EUR')
        with self.assertRaisesRegex(ValueError, 'calendar'):
            project(inputs, date(9990, 1, 1), ALLOCATION, total=1000, years=20)
        with self.assertRaisesRegex(ValueError, 'numeric range'):
            project(inputs, TODAY, ALLOCATION, total=1e308, currency='EUR', rates={'EUR': 1e-308})
        large = replace(
            inputs,
            emergency_months=10,
            cash_months=0,
            returns=ReturnAssumptions(equities=5, mak=5),
            outgoings=(CashFlow('Living costs', 10 ** 307, date.min, frequency='monthly'),),
        )
        with self.assertRaisesRegex(ValueError, 'numeric range'):
            project(large, TODAY, ALLOCATION, total=1.79e308, years=1)
        inputs = replace(inputs, returns=ReturnAssumptions(equities=1e300, mak=1e300))
        with self.assertRaisesRegex(ValueError, 'numeric range'):
            project(inputs, TODAY, ALLOCATION, total=1000, years=100)


if __name__ == '__main__':
    unittest.main()
