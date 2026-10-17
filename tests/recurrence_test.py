"""Month-based recurrence and annual cash-flow budgeting."""

from datetime import date
from math import fsum
import unittest

from portfolio.calculator import CalculatorSettings, CashFlow, MonthlyTotals, ReturnAssumptions, recommend
from portfolio.models import AllocationTargets, CASH, STOCK_MARKET, TREASURY
from portfolio.projection import project


TODAY = date(2026, 1, 1)
ALLOCATION = AllocationTargets({STOCK_MARKET: {'World': 60}, TREASURY: {'HUF': 40}, CASH: {}},
                               {'World': 'Equities'})
NO_GROWTH = ReturnAssumptions(equities=0, bonds=0, gold=0, mak=0)


class RecurrenceTests(unittest.TestCase):
    def test_recurring_months_are_canonical_and_end_month_is_inclusive(self):
        monthly = CashFlow('Loan', 100, date(2026, 1, 31), frequency='monthly',
                           end=date(2026, 3, 2))
        self.assertEqual((monthly.start, monthly.end, monthly.payment_count),
                         (TODAY, date(2026, 3, 1), 3))
        self.assertEqual(tuple(monthly.dates(TODAY, date(2026, 12, 31))),
                         (TODAY, date(2026, 2, 1), date(2026, 3, 1)))
        same_month = CashFlow('Contract', 100, date(2026, 10, 31), frequency='monthly',
                              end=date(2026, 10, 1))
        self.assertEqual(same_month.payment_count, 1)

    def test_single_payment_uses_the_beginning_of_its_month(self):
        due = date(2028, 2, 29)
        single = CashFlow('Purchase', 100, due)
        self.assertEqual(single.start, date(2028, 2, 1))
        self.assertEqual(tuple(single.dates(date(2028, 2, 1), due)), (date(2028, 2, 1),))
        self.assertFalse(single.ongoing)

    def test_annual_payments_repeat_only_in_the_start_month(self):
        annual = CashFlow('Subscription', 1200, date(2026, 10, 16), frequency='annual', count=3)
        self.assertEqual(annual.interval_months, 12)
        self.assertEqual(tuple(annual.dates(TODAY, date(2030, 1, 1))),
                         (date(2026, 10, 1), date(2027, 10, 1), date(2028, 10, 1)))
        self.assertEqual(tuple(annual.dates(date(2026, 11, 1), date(2028, 9, 30))),
                         (date(2027, 10, 1),))
        self.assertEqual(tuple(annual.dates(date(2027, 10, 2), date(2028, 10, 1))),
                         (date(2028, 10, 1),))

    def test_annual_end_month_and_count_limit_occurrences(self):
        for end, count in ((date(2026, 10, 1), 1), (date(2027, 9, 30), 1),
                           (date(2027, 10, 1), 2), (date(2028, 11, 30), 3)):
            with self.subTest(end=end):
                annual = CashFlow('Contract', 100, date(2026, 10, 16), frequency='annual', end=end)
                self.assertEqual(annual.payment_count, count)
                self.assertEqual(len(tuple(annual.dates(TODAY, date.max))), count)
        ongoing = CashFlow('Bonus', 100, TODAY, frequency='annual')
        self.assertTrue(ongoing.ongoing)
        self.assertIsNone(ongoing.payment_count)
        self.assertEqual(len(tuple(ongoing.dates(TODAY, date(2030, 12, 31)))), 5)

    def test_calendar_limits_apply_to_the_full_annual_schedule(self):
        with self.assertRaisesRegex(ValueError, 'calendar'):
            CashFlow('Contract', 100, date(9999, 1, 1), frequency='annual', count=2)
        self.assertEqual(CashFlow('Contract', 100, date(9998, 12, 31),
                                  frequency='annual', count=2).payment_count, 2)
        for values in ({'count': 0}, {'count': True}, {'count': 1201},
                       {'count': 2, 'end': date(2027, 1, 1)}, {'end': date(2025, 12, 1)}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                CashFlow('Contract', 100, TODAY, frequency='annual', **values)

    def test_saving_ratio_excludes_single_flows_and_preserves_deficits(self):
        for single_income, single_outgoings in ((0, 0), (10000, 50000)):
            with self.subTest(single_income=single_income):
                totals = MonthlyTotals(1000, single_income, 750, single_outgoings)
                self.assertEqual(totals.saving_percent, 25)
                self.assertEqual(MonthlyTotals(1000, single_income, 1200, single_outgoings).saving_percent, -20)
        self.assertEqual(MonthlyTotals(1000, 0, 0, 0).saving_percent, 100)
        self.assertEqual(MonthlyTotals(1000, 0, 1000, 0).saving_percent, 0)
        self.assertIsNone(MonthlyTotals(0, 1000, 50, 0).saving_percent)
        self.assertIsNone(MonthlyTotals(0, 0, 0, 0).saving_percent)

    def test_current_month_recurring_income_is_not_received_twice(self):
        contract = CashFlow('Salary', 500, date(2026, 10, 16), frequency='monthly',
                            end=date(2026, 10, 16))
        inputs = CalculatorSettings(incomes=(contract,))
        self.assertEqual(inputs.monthly_totals(2026, 10).recurring_income, 500)
        self.assertEqual(inputs.income_receipts(date(2026, 10, 16), date(2027, 12, 31)), ())
        annual = CashFlow('Bonus', 1000, date(2026, 10, 16), frequency='annual', count=2)
        self.assertEqual(CalculatorSettings(incomes=(annual,)).income_receipts(
            date(2026, 10, 16), date(2028, 10, 31)), ((date(2027, 10, 1), 1000),))

    def test_ongoing_annual_costs_are_budgeted_monthly_but_summary_uses_due_month(self):
        annual = CashFlow('Insurance', 1200, TODAY, frequency='annual')
        salary = CashFlow('Salary', 500, TODAY, frequency='monthly')
        inputs = CalculatorSettings(incomes=(salary,), outgoings=(annual,), returns=NO_GROWTH)
        self.assertEqual(inputs.monthly_spending(as_of=TODAY), 100)
        self.assertEqual(inputs.monthly_totals(2026, 1).recurring_outgoings, 1200)
        self.assertEqual(inputs.monthly_totals(2026, 2).recurring_outgoings, 0)
        self.assertEqual(inputs.scheduled_payments(TODAY), ())
        receipts = inputs.income_receipts(TODAY, date(2027, 1, 1))
        self.assertEqual(len(receipts), 12)
        self.assertTrue(all(amount == 400 for _, amount in receipts))
        recommendation = recommend(inputs, TODAY, ALLOCATION, total=10000)
        self.assertEqual([row.amount for row in recommendation.rows[:2]], [600, 200])
        projection = project(inputs, TODAY, ALLOCATION, total=10000, years=1)
        self.assertEqual(fsum(values[-1] for values in projection.portfolio.values()),
                         10000 + 12 * 500 - 1200)

    def test_current_month_income_is_recorded_and_not_counted_twice(self):
        inputs = CalculatorSettings(
            incomes=(CashFlow('Salary', 500, TODAY, frequency='monthly'),
                     CashFlow('Bonus', 200, date(2026, 1, 15))),
            outgoings=(CashFlow('Living costs', 100, TODAY, frequency='monthly'),))
        for as_of in (TODAY, date(2026, 1, 10)):
            with self.subTest(as_of=as_of):
                self.assertEqual(inputs.income_receipts(as_of, date(2026, 2, 1)),
                                 ((date(2026, 2, 1), 400),))

    def test_current_month_single_income_is_recorded_but_single_expenses_remain_unpaid(self):
        inputs = CalculatorSettings(
            incomes=(CashFlow('Current bonus', 200, date(2026, 1, 31)),
                     CashFlow('Future bonus', 300, date(2026, 2, 28))),
            outgoings=(CashFlow('Overdue', 100, date(2025, 12, 31)),
                       CashFlow('Current purchase', 200, date(2026, 1, 31))),
            emergency_months=0, cash_months=0, returns=NO_GROWTH)
        for as_of in (TODAY, date(2026, 1, 16)):
            with self.subTest(as_of=as_of):
                self.assertEqual(inputs.income_receipts(as_of, date(2026, 2, 28)),
                                 ((date(2026, 2, 1), 300),))
                self.assertEqual([payment.due for payment in inputs.scheduled_payments(as_of)],
                                 [date(2025, 12, 1), date(2026, 1, 1)])
                result = recommend(inputs, as_of, ALLOCATION, total=1000)
                self.assertEqual([row.cash for row in result.rows[2:-1]], [100, 200])
                projection = project(inputs, as_of, ALLOCATION, total=1000, years=1)
                self.assertEqual(fsum(values[1] for values in projection.portfolio.values()), 700)
                self.assertEqual(fsum(values[-1] for values in projection.portfolio.values()), 1000)

    def test_finite_annual_outgoings_are_dated_expenses_without_budget_deduction(self):
        annual = CashFlow('Annual instalment', 1200, date(2026, 4, 20), frequency='annual', count=2)
        salary = CashFlow('Salary', 500, TODAY, frequency='monthly')
        inputs = CalculatorSettings(incomes=(salary,), outgoings=(annual,),
                                    emergency_months=0, cash_months=0, returns=NO_GROWTH)
        self.assertEqual(inputs.monthly_spending(as_of=TODAY), 0)
        self.assertEqual([payment.due for payment in inputs.scheduled_payments(TODAY)],
                         [date(2026, 4, 1), date(2027, 4, 1)])
        self.assertEqual([payment.due for payment in inputs.scheduled_payments(date(2026, 4, 2))],
                         [date(2027, 4, 1)])
        projection = project(inputs, TODAY, ALLOCATION, total=10000, years=2)
        self.assertEqual(fsum(values[-1] for values in projection.portfolio.values()),
                         10000 + 24 * 500 - 2 * 1200)
        self.assertFalse(projection.shortfalls)
        self.assertFalse(projection.deadline_shortfalls)

    def test_future_annual_budget_start_raises_fixed_buffers_once(self):
        annual = CashFlow('Insurance', 12, date(2026, 7, 31), 'EUR', 'annual')
        salary = CashFlow('Salary', 1000, TODAY, frequency='monthly')
        inputs = CalculatorSettings(incomes=(salary,), outgoings=(annual,), returns=NO_GROWTH)
        rates = {'EUR': 400}
        self.assertEqual(inputs.monthly_spending(rates, as_of=TODAY), 0)
        self.assertEqual(inputs.monthly_spending(rates, as_of=date(2026, 7, 1)), 400)
        self.assertEqual(inputs.buffer_increases(TODAY, rates), ((date(2026, 7, 1), 3200),))
        projection = project(inputs, TODAY, ALLOCATION, total=10000, rates=rates, years=1)
        self.assertEqual(fsum(values[-1] for values in projection.portfolio.values()),
                         10000 + 5 * 1000 + 7 * 600)
        self.assertEqual(projection.purpose['Emergency fund'][-1], 2400)
        self.assertEqual(projection.purpose['Monthly living costs'][-1], 800)


if __name__ == '__main__':
    unittest.main()
