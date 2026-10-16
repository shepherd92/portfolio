from dataclasses import replace
from datetime import date
from decimal import Decimal
from math import fsum, inf, nan
import unittest

from support import calculator_allocation as allocation

from portfolio.calculator import CalculatorSettings, Expense, MonthlyCost, ReturnAssumptions, expense_cash_date, recommend




class FutureIncomeTests(unittest.TestCase):
    def settings(self, **values):
        defaults = dict(monthly_income=100,
                        returns=ReturnAssumptions(equities=0, mak=0))
        return CalculatorSettings(**(defaults | values))

    def test_savings_use_net_income_and_income_requires_whole_nonnegative_huf(self):
        for income, expected in ((0, 0), (99, 0), (100, 0), (101, 1), (250, 150)):
            with self.subTest(income=income):
                self.assertEqual(self.settings(monthly_income=income, monthly_costs=(MonthlyCost('Living costs', 100),)).monthly_savings(),
                                 expected)
        for income in (-1, .01, Decimal('100.000000000000001'), True, '100', nan, inf):
            with self.subTest(income=income), self.assertRaises(ValueError):
                self.settings(monthly_income=income)
        self.assertEqual(self.settings(monthly_income=Decimal('100.0')).monthly_savings(), 100)

    def test_no_surplus_matches_zero_income_and_does_not_model_withdrawals(self):
        today = date(2025, 1, 1)
        settings = self.settings(monthly_income=0, monthly_costs=(MonthlyCost('Living costs', 50),),
                                 expenses=(Expense('Soon', 200, date(2025, 3, 1)),
                                           Expense('Later', 300, date(2026, 1, 1))))
        expected = recommend(settings, today, allocation(), total=10_000)
        for income in (0, 25, 50):
            with self.subTest(income=income):
                self.assertEqual(recommend(replace(settings, monthly_income=income), today, allocation(), total=10_000), expected)
        self.assertEqual([row.income_funded for row in expected.rows], [None, None, 0, 0, None])
        without_expenses = replace(settings, expenses=())
        self.assertEqual(recommend(replace(without_expenses, monthly_income=1_000), today, allocation(), total=10_000),
                         recommend(without_expenses, today, allocation(), total=10_000))

    def test_monthly_payments_keep_original_day_and_include_the_cash_deadline(self):
        cases = ((date(2025, 1, 31), date(2025, 3, 30), 0),
                 (date(2025, 1, 31), date(2025, 3, 31), 0),
                 (date(2025, 1, 31), date(2025, 4, 27), 0),
                 (date(2025, 1, 31), date(2025, 4, 28), 100),
                 (date(2025, 1, 31), date(2025, 5, 30), 100),
                 (date(2025, 1, 31), date(2025, 5, 31), 200),
                 (date(2024, 1, 31), date(2024, 4, 28), 0),
                 (date(2024, 1, 31), date(2024, 4, 29), 100),
                 (date(2024, 1, 31), date(2024, 5, 31), 200))
        for today, due, covered in cases:
            with self.subTest(today=today, due=due):
                result = recommend(self.settings(expenses=(Expense('Cost', 500, due),)), today, allocation(), total=10_000)
                self.assertEqual((result.rows[2].income_funded, result.rows[2].amount),
                                 (covered, 500 - covered))

    def test_income_after_cash_deadline_never_funds_expense_even_before_actual_due_date(self):
        due = date(2025, 5, 1)
        for today, months, covered in ((date(2025, 1, 1), 2, 200),
                                       (date(2025, 2, 1), 2, 100),
                                       (date(2025, 3, 1), 2, 0),
                                       (date(2025, 4, 1), 2, 0),
                                       (date(2025, 1, 1), 3, 100),
                                       (date(2025, 1, 1), 0, 200)):
            with self.subTest(today=today, cash_months=months):
                result = recommend(self.settings(cash_months=months, expenses=(Expense('Cost', 500, due),)),
                                   today, allocation(), total=10_000)
                row = result.rows[2]
                self.assertEqual((row.income_funded, row.amount), (covered, 500 - covered))
                if today >= expense_cash_date(due, months):
                    self.assertEqual(row.cash, 500)
                    self.assertEqual((row.mak, row.stock), (0, 0))

    def test_earlier_expenses_use_income_once_even_when_rows_are_out_of_order(self):
        today = date(2025, 1, 1)
        expenses = (Expense('Later', 300, date(2025, 7, 1)),
                    Expense('Sooner', 300, date(2025, 5, 1)))
        result = recommend(self.settings(expenses=expenses), today, allocation(), total=10_000)
        self.assertEqual([row.name for row in result.rows[2:-1]], ['Later', 'Sooner'])
        self.assertEqual([(row.income_funded, row.amount) for row in result.rows[2:-1]],
                         [(200, 100), (200, 100)])
        self.assertEqual(result.total, 10_000)

    def test_clamped_deadline_ties_assign_income_to_the_earlier_actual_expense(self):
        expenses = (Expense('Later', 100, date(2025, 4, 30)),
                    Expense('Earlier', 100, date(2025, 4, 28)))
        self.assertEqual([expense_cash_date(expense.due) for expense in expenses],
                         [date(2025, 2, 28), date(2025, 2, 28)])
        result = recommend(self.settings(expenses=expenses), date(2025, 1, 28), allocation(), total=10_000)
        self.assertEqual([(row.name, row.income_funded, row.amount) for row in result.rows[2:-1]],
                         [('Later', 0, 100), ('Earlier', 100, 0)])

    def test_partial_payments_are_shared_across_expenses_and_unspent_income_carries_forward(self):
        today = date(2025, 1, 1)
        cases = (((Expense('Later', 250, date(2025, 6, 1)),
                   Expense('First', 50, date(2025, 4, 1))), [(250, 0), (50, 0)]),
                 ((Expense('Later', 250, date(2025, 6, 1)),
                   Expense('First', 50, date(2025, 4, 1)),
                   Expense('Same day', 75, date(2025, 4, 1))), [(200, 50), (50, 0), (50, 25)]))
        for expenses, expected in cases:
            with self.subTest(expenses=expenses):
                result = recommend(self.settings(expenses=expenses), today, allocation(), total=10_000)
                self.assertEqual([(row.income_funded, row.amount) for row in result.rows[2:-1]], expected)

    def test_mak_growth_matches_independent_cashflow_sum_and_only_residual_is_discounted(self):
        today = date(2025, 1, 15)
        payments = [date(2025, month, 15) for month in range(2, 8)]
        expenses = (Expense('Last', 1_000, date(2025, 9, 14)),
                    Expense('First', 50, date(2025, 4, 20)),
                    Expense('Middle', 175, date(2025, 6, 16)))
        for rate in (-20, 0, 12):
            with self.subTest(mak_return=rate):
                settings = self.settings(returns=ReturnAssumptions(equities=7, mak=rate),
                                         expenses=expenses)
                result = recommend(settings, today, allocation(), total=10_000)
                without_income = recommend(replace(settings, monthly_income=0), today, allocation(), total=10_000)
                covered = {}
                for expense in sorted(expenses, key=lambda item: item.due):
                    # Sum every payment separately and subtract earlier withdrawals grown
                    # to this date: an independent oracle for allocating partial deposits.
                    deadline = expense_cash_date(expense.due)
                    growth = lambda start: (1 + rate / 100) ** ((deadline - start).days / 365.2425)
                    balance = fsum(100 * growth(payment) for payment in payments if payment <= deadline)
                    balance -= fsum(amount * growth(due) for due, amount in covered.values())
                    covered[expense.name] = (deadline, min(expense.amount, max(0, balance)))
                for expense, row, old_row in zip(expenses, result.rows[2:-1], without_income.rows[2:-1]):
                    expected_income = covered[expense.name][1]
                    self.assertAlmostEqual(row.income_funded, expected_income, places=8)
                    self.assertAlmostEqual(row.amount, old_row.amount * (1 - expected_income / expense.amount),
                                           places=8)
                    self.assertEqual(row.future_amount, expense.amount)
                    self.assertAlmostEqual(row.stock + row.mak + row.cash, row.amount)
                self.assertAlmostEqual(result.total, 10_000)
                self.assertEqual(result.shortfall, 0)

    def test_future_income_releases_current_money_to_long_term_allocation(self):
        settings = self.settings(monthly_income=120, monthly_costs=(MonthlyCost('Living costs', 20),), emergency_months=3,
                                 expenses=(Expense('Cost', 300, date(2025, 6, 1)),))
        result = recommend(settings, date(2025, 1, 1), allocation(), total=1_000)
        self.assertEqual([(row.amount, row.stock, row.mak) for row in result.rows],
                         [(60, 0, 60), (40, 0, 0), (0, 0, 0), (900, 540, 360)])
        self.assertEqual(result.rows[2].income_funded, 300)
        self.assertEqual((result.total, result.stock, result.mak), (1_000, 540, 420))
        self.assertEqual(result.shortfall, 0)

    def test_income_growth_does_not_overflow_intermediate_values(self):
        today, due = date(2026, 10, 16), date(2126, 10, 16)
        settings = self.settings(monthly_income=10_000,
                                 horizon_years=1, returns=ReturnAssumptions(mak=-99.99),
                                 expenses=(Expense('Distant cost', 100_000, date(2126, 12, 16)),))
        payments = [date(year, month, 16) for year in range(today.year, due.year + 1)
                    for month in range(1, 13) if today < date(year, month, 16) <= due]
        expected = fsum(10_000 * .0001 ** ((due - payment).days / 365.2425) for payment in payments)
        result = recommend(settings, today, allocation(), total=1_000_000)
        self.assertAlmostEqual(result.rows[2].income_funded, expected, places=6)
        self.assertGreater(result.rows[2].amount, 0)
        self.assertAlmostEqual(result.total, 1_000_000)
        high_growth = recommend(replace(settings, returns=ReturnAssumptions(mak=10_000)), today, allocation(), total=1_000_000)
        self.assertEqual((high_growth.rows[2].income_funded, high_growth.rows[2].amount), (100_000, 0))

    def test_income_cannot_fund_todays_emergency_reserve_or_inflate_current_wealth(self):
        settings = self.settings(monthly_income=1_100, monthly_costs=(MonthlyCost('Living costs', 100),),
                                 expenses=(Expense('Cost', 1_000, date(2025, 4, 1)),))
        result = recommend(settings, date(2025, 1, 1), allocation(), total=100)
        self.assertEqual(result.rows[2].income_funded, 1_000)
        self.assertEqual([(row.amount, row.stock, row.mak) for row in result.rows],
                         [(0, 0, 0), (100, 0, 0), (0, 0, 0), (0, 0, 0)])
        self.assertEqual((result.total, result.shortfall), (100, 700))


if __name__ == '__main__':
    unittest.main()
