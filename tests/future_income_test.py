from dataclasses import replace
from datetime import date
from math import fsum
import unittest

from support import calculator_allocation as allocation

from portfolio.calculator import CalculatorSettings, CashFlow, ReturnAssumptions, expense_cash_date, recommend


class FutureIncomeTests(unittest.TestCase):
    def income(self, amount, as_of):
        return (CashFlow('Salary', amount, as_of, frequency='monthly'),) if amount else ()

    def settings(self, as_of=date(2025, 1, 1), income=100, **values):
        defaults = dict(incomes=self.income(income, as_of),
                        returns=ReturnAssumptions(equities=0, mak=0))
        return CalculatorSettings(**(defaults | values))

    def test_no_surplus_matches_zero_income_and_does_not_model_withdrawals(self):
        today = date(2025, 1, 1)
        settings = self.settings(
            income=0,
            outgoings=(
                CashFlow('Living costs', 50, date.min, frequency='monthly'),
                CashFlow('Soon', 200, date(2025, 3, 1)),
                CashFlow('Later', 300, date(2026, 1, 1)),
            ),
        )
        expected = recommend(settings, today, allocation(), total=10_000)
        for income in (0, 25, 50):
            with self.subTest(income=income):
                result = recommend(replace(settings, incomes=self.income(income, today)), today, allocation(), total=10000)
                self.assertEqual(result.rows, expected.rows)
                self.assertEqual((result.market_return, result.mak_return, result.shortfall),
                                 (expected.market_return, expected.mak_return, expected.shortfall))
                self.assertEqual(result.receipts, ())
        self.assertEqual([row.income_funded for row in expected.rows], [None, None, 0, 0, None])
        without_expenses = replace(settings, outgoings=())
        self.assertEqual(recommend(replace(without_expenses, incomes=self.income(1000, today)), today, allocation(), total=10000),
                         recommend(without_expenses, today, allocation(), total=10_000))

    def test_monthly_income_starts_next_month_and_includes_the_cash_deadline(self):
        cases = ((date(2025, 1, 31), date(2025, 3, 31), 0),
                 (date(2025, 1, 31), date(2025, 4, 1), 100),
                 (date(2025, 1, 31), date(2025, 5, 1), 200),
                 (date(2025, 1, 31), date(2025, 5, 31), 200),
                 (date(2024, 1, 31), date(2024, 4, 1), 100),
                 (date(2024, 1, 31), date(2024, 5, 1), 200))
        for today, due, covered in cases:
            with self.subTest(today=today, due=due):
                result = recommend(self.settings(today, outgoings=(CashFlow('Cost', 500, due),)), today, allocation(), total=10000)
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
                result = recommend(
                    self.settings(today, cash_months=months, outgoings=(CashFlow('Cost', 500, due),)),
                    today,
                    allocation(),
                    total=10000,
                )
                row = result.rows[2]
                self.assertEqual((row.income_funded, row.amount), (covered, 500 - covered))
                if today >= expense_cash_date(due, months):
                    self.assertEqual(row.cash, 500)
                    self.assertEqual((row.mak, row.stock), (0, 0))

    def test_earlier_expenses_use_income_once_even_when_rows_are_out_of_order(self):
        today = date(2025, 1, 1)
        expenses = (CashFlow('Later', 300, date(2025, 7, 1)),
                    CashFlow('Sooner', 300, date(2025, 5, 1)))
        result = recommend(self.settings(outgoings=expenses), today, allocation(), total=10000)
        self.assertEqual([row.name for row in result.rows[2:-1]], ['Later', 'Sooner'])
        for row in result.rows[2:-1]:
            self.assertAlmostEqual(row.income_funded, 200)
            self.assertAlmostEqual(row.amount, 100)
        self.assertEqual(result.total, 10_000)

    def test_same_month_expenses_share_income_once_in_input_order(self):
        today = date(2025, 1, 28)
        expenses = (CashFlow('First', 100, date(2025, 4, 30)),
                    CashFlow('Second', 100, date(2025, 4, 28)))
        self.assertEqual([expense_cash_date(expense.start) for expense in expenses],
                         [date(2025, 2, 1), date(2025, 2, 1)])
        settings = self.settings(today, outgoings=expenses)
        baseline = recommend(replace(settings, incomes=()), today, allocation(), total=10000)
        result = recommend(settings, today, allocation(), total=10000)
        first, second = result.rows[2:-1]
        first_defensive = baseline.rows[2].mak
        self.assertAlmostEqual(first.income_funded, first_defensive)
        self.assertAlmostEqual(first.mak, 0)
        self.assertAlmostEqual(second.income_funded, 100 - first_defensive)
        for old, new in zip(baseline.rows[2:-1], result.rows[2:-1]):
            self.assertEqual(new.stock, old.stock)
        self.assertAlmostEqual(first.income_funded + second.income_funded, 100)

    def test_mak_growth_matches_independent_cashflow_sum_and_offsets_only_defensive_value(self):
        today = date(2025, 1, 15)
        payments = [date(2025, month, 1) for month in range(2, 8)]
        expenses = (CashFlow('Last', 100000, date(2025, 9, 14)),
                    CashFlow('First', 100000, date(2025, 4, 20)),
                    CashFlow('Middle', 100000, date(2025, 6, 16)))
        for rate in (-20, 0, 12):
            with self.subTest(mak_return=rate):
                settings = self.settings(today, returns=ReturnAssumptions(equities=7, mak=rate), outgoings=expenses)
                result = recommend(settings, today, allocation(), total=1000000)
                baseline = recommend(replace(settings, incomes=()), today, allocation(), total=1000000)
                covered = {}
                for expense in sorted(expenses, key=lambda item: item.start):
                    deadline = expense_cash_date(expense.start)
                    growth = lambda start: (1 + rate / 100) ** ((deadline - start).days / 365.2425)
                    balance = fsum(100 * growth(payment) for payment in payments if payment <= deadline)
                    balance -= fsum(amount * growth(due) for due, amount in covered.values())
                    covered[expense.name] = (deadline, max(0, balance))
                for expense, row, old_row in zip(expenses, result.rows[2:-1], baseline.rows[2:-1]):
                    deadline, income = covered[expense.name]
                    saving_today = income / (1 + rate / 100) ** ((deadline - today).days / 365.2425)
                    self.assertAlmostEqual(row.income_funded, income, places=8)
                    self.assertAlmostEqual(row.amount, old_row.amount - saving_today, places=8)
                    self.assertAlmostEqual(row.mak, old_row.mak - saving_today, places=8)
                    self.assertEqual(row.stock, old_row.stock)
                    self.assertEqual(row.future_amount, expense.amount)
                    self.assertAlmostEqual(row.stock + row.mak + row.cash, row.amount)
                self.assertAlmostEqual(result.total, 1000000)
                self.assertEqual(result.shortfall, 0)

    def test_income_preserves_full_expense_stock_leg_and_releases_only_mak(self):
        today = date(2024, 1, 1)
        settings = self.settings(today, income=0, horizon_years=1,
                                 incomes=(CashFlow('Bonus', 20, date(2024, 5, 1)),),
                                 outgoings=(CashFlow('Purchase', 100, date(2024, 9, 2)),))
        result = recommend(settings, today, allocation(), total=1000)
        row = result.rows[2]
        # 182 of the 366 horizon days remain until the July cash deadline.
        stock_fraction = 182 / 366
        self.assertAlmostEqual(row.stock, 100 * stock_fraction)
        self.assertAlmostEqual(row.mak, 100 * (1 - stock_fraction) - 20)
        self.assertEqual((row.amount, row.income_funded), (80, 20))
        with_interest = replace(settings, returns=ReturnAssumptions(equities=10, mak=10))
        row = recommend(with_interest, today, allocation(), total=1000).rows[2]
        reserve = 100 / 1.1 ** (182 / 365.2425)
        income_today = 20 / 1.1 ** (121 / 365.2425)
        self.assertAlmostEqual(row.stock, reserve * stock_fraction)
        self.assertAlmostEqual(row.mak, reserve * (1 - stock_fraction) - income_today)
        self.assertAlmostEqual(row.income_funded, 20 * 1.1 ** (61 / 365.2425))

    def test_future_income_caps_at_defensive_need_and_preserves_fixed_buffers(self):
        today = date(2025, 1, 1)
        settings = self.settings(
            income=120, emergency_months=3,
            outgoings=(CashFlow('Living costs', 20, date.min, frequency='monthly'),
                       CashFlow('Cost', 300, date(2025, 6, 1))),
        )
        baseline = recommend(replace(settings, incomes=()), today, allocation(), total=1000)
        result = recommend(settings, today, allocation(), total=1000)
        row = result.rows[2]
        self.assertEqual(result.rows[:2], baseline.rows[:2])
        self.assertEqual(row.stock, baseline.rows[2].stock)
        self.assertAlmostEqual(row.mak, 0)
        self.assertAlmostEqual(row.amount, row.stock)
        self.assertAlmostEqual(row.income_funded, baseline.rows[2].mak)
        self.assertAlmostEqual(result.rows[-1].amount, 900 - row.stock)
        self.assertAlmostEqual(result.total, 1000)
        self.assertEqual(result.shortfall, 0)

    def test_fully_stock_funded_distant_expenses_do_not_earmark_income_even_at_extreme_rates(self):
        today = date(2026, 10, 16)
        for rate in (-99.99, 0, 10000):
            with self.subTest(rate=rate):
                settings = self.settings(
                    today, income=10000, horizon_years=1,
                    returns=ReturnAssumptions(mak=rate),
                    outgoings=(CashFlow('Distant cost', 100000, date(2126, 12, 16)),),
                )
                result = recommend(settings, today, allocation(), total=1000000)
                baseline = recommend(replace(settings, incomes=()), today, allocation(), total=1000000)
                self.assertEqual(result.rows, baseline.rows)
                self.assertEqual((result.market_return, result.mak_return, result.shortfall),
                                 (baseline.market_return, baseline.mak_return, baseline.shortfall))
                self.assertTrue(result.receipts)
                self.assertEqual(baseline.receipts, ())
                self.assertEqual(result.expenses[0].contributions, ())
                self.assertEqual(result.rows[2].income_funded, 0)
                self.assertGreater(result.rows[2].stock, 0)
                self.assertEqual(result.rows[2].mak, 0)
                self.assertAlmostEqual(result.total, 1000000)

    def test_income_cannot_fund_todays_emergency_reserve_or_inflate_current_wealth(self):
        today = date(2025, 1, 1)
        settings = self.settings(
            income=1100,
            outgoings=(CashFlow('Living costs', 100, date.min, frequency='monthly'),
                       CashFlow('Cost', 1000, date(2025, 4, 1))),
        )
        baseline = recommend(replace(settings, incomes=()), today, allocation(), total=10000)
        result = recommend(settings, today, allocation(), total=100)
        self.assertAlmostEqual(result.rows[2].income_funded, baseline.rows[2].mak)
        self.assertEqual([(row.amount, row.stock, row.mak) for row in result.rows],
                         [(0, 0, 0), (100, 0, 0), (0, 0, 0), (0, 0, 0)])
        self.assertAlmostEqual(float(result.shortfall), 700 + baseline.rows[2].stock)
        self.assertEqual(result.total, 100)


if __name__ == '__main__':
    unittest.main()
