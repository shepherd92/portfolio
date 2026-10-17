"""Unified payment schedules and their cash-flow accounting."""

from datetime import date
from math import fsum
import unittest

from portfolio.calculator import (CalculatorSettings, CashFlow, ReturnAssumptions,
                                  expense_income, recommend)
from portfolio.models import AllocationTargets, CASH, STOCK_MARKET, TREASURY
from portfolio.projection import project


TODAY = date(2026, 1, 1)
ALLOCATION = AllocationTargets({STOCK_MARKET: {'World': 60}, TREASURY: {'HUF': 40}, CASH: {}},
                               {'World': 'Equities'})


def settings(*outgoings, income=0, currency='HUF', **values):
    incomes = (CashFlow('Salary', income, TODAY, currency, 'monthly'),) if income else ()
    return CalculatorSettings(incomes=incomes, outgoings=outgoings,
                              returns=ReturnAssumptions(equities=0, bonds=0, gold=0, mak=0), **values)


def total_at(projection, index):
    return fsum(values[index] for values in projection.portfolio.values())


class CashFlowTests(unittest.TestCase):
    def test_count_includes_the_first_payment(self):
        loan = CashFlow('Loan', 100, TODAY, frequency='monthly', count=1)
        self.assertEqual([payment.due for payment in settings(loan).scheduled_payments(TODAY)], [TODAY])

    def test_past_installments_are_paid_but_single_overdue_payments_remain(self):
        loan = CashFlow('Loan', 100, date(2025, 11, 1), frequency='monthly', count=3)
        overdue = CashFlow('Invoice', 50, date(2025, 12, 1))
        payments = settings(loan, overdue).scheduled_payments(TODAY)
        self.assertEqual([(payment.name, payment.due, payment.outgoing_index) for payment in payments],
                         [('Loan', TODAY, 0), ('Invoice', date(2025, 12, 1), 1)])
        self.assertEqual(settings(loan).scheduled_payments(date(2026, 1, 2)), ())

    def test_schedules_have_strict_endings_and_calendar_limits(self):
        invalid = ({'frequency': 'weekly'}, {'count': 2}, {'end': TODAY},
                   {'frequency': 'monthly', 'count': 2, 'end': TODAY},
                   {'frequency': 'monthly', 'count': 0}, {'frequency': 'monthly', 'count': True},
                   {'frequency': 'monthly', 'count': 1.5}, {'frequency': 'monthly', 'count': 1201},
                   {'frequency': 'monthly', 'end': date(2025, 12, 31)},
                   {'frequency': 'monthly', 'end': date(2126, 1, 1)})
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                CashFlow('Loan', 100, TODAY, **values)
        with self.assertRaisesRegex(ValueError, 'calendar'):
            CashFlow('Loan', 100, date(9999, 12, 1), frequency='monthly', count=2)
        self.assertEqual(CashFlow('Loan', 100, TODAY, frequency='monthly', count=1200).payment_count, 1200)

    def test_huf_rejects_fractions_but_foreign_currency_accepts_them(self):
        with self.assertRaisesRegex(ValueError, 'whole number of HUF'):
            CashFlow('Loan', 1.5, TODAY)
        self.assertEqual(CashFlow('Loan', 1.5, TODAY, 'EUR').amount, 1.5)

    def test_ongoing_costs_are_date_aware_and_finite_payments_are_not_baseline(self):
        rent = CashFlow('Rent', 100, date(2026, 4, 15), frequency='monthly')
        loan = CashFlow('Loan', 50, TODAY, frequency='monthly', count=12)
        inputs = settings(rent, loan, income=300)
        self.assertEqual(inputs.monthly_spending(as_of=TODAY), 0)
        self.assertEqual(inputs.income_receipts(TODAY, date(2026, 2, 1)), ((date(2026, 2, 1), 300),))
        self.assertEqual(inputs.monthly_spending(as_of=rent.start), 100)
        self.assertEqual(inputs.income_receipts(TODAY, date(2026, 4, 1))[-1], (date(2026, 4, 1), 200))
        self.assertEqual(len(inputs.scheduled_payments(TODAY)), 12)

    def test_finite_installments_are_not_counted_in_buffers_or_deducted_twice(self):
        rent = CashFlow('Rent', 100, TODAY, frequency='monthly')
        loan = CashFlow('Loan', 50, date(2026, 2, 1), frequency='monthly', count=12)
        inputs = settings(rent, loan, income=500)
        result = project(inputs, TODAY, ALLOCATION, total=10000, years=1)
        self.assertEqual(total_at(result, -1), 10000 + 12 * 400 - 12 * 50)
        self.assertEqual(set(result.purpose['Emergency fund']), {600})
        self.assertEqual(set(result.purpose['Monthly living costs']), {200})
        self.assertEqual(result.purpose['Planned expenses'][-1], 0)
        self.assertFalse(result.shortfalls)
        self.assertFalse(result.deadline_shortfalls)

    def test_income_is_released_after_the_last_installment(self):
        loan = CashFlow('Loan', 100, date(2026, 2, 1), frequency='monthly', count=3)
        result = project(settings(loan, income=200), TODAY, ALLOCATION, total=1000, years=1)
        self.assertEqual(total_at(result, -1), 1000 + 12 * 200 - 300)
        june = [index for index, day in enumerate(result.dates) if day == date(2026, 6, 1)]
        self.assertEqual(result.purpose['Long-term investment'][june[-1]]
                         - result.purpose['Long-term investment'][june[0]], 200)

    def test_future_start_changes_buffers_and_income_without_retroactive_charges(self):
        rent = CashFlow('Rent', 100, date(2026, 7, 15), frequency='monthly')
        inputs = settings(rent, income=300)
        recommendation = recommend(inputs, TODAY, ALLOCATION, total=10000)
        self.assertEqual([row.amount for row in recommendation.rows[:2]], [0, 0])
        result = project(inputs, TODAY, ALLOCATION, total=10000, years=1)
        self.assertEqual(total_at(result, -1), 10000 + 5 * 300 + 7 * 200)
        start = result.dates.index(rent.start)
        self.assertEqual(result.purpose['Emergency fund'][start], 600)
        self.assertEqual(result.purpose['Monthly living costs'][start], 200)
        self.assertTrue(all(value == 0 for value in result.purpose['Emergency fund'][:start]))
        for index in range(len(result.dates)):
            self.assertAlmostEqual(total_at(result, index),
                                   fsum(values[index] for values in result.purpose.values()))

    def test_future_start_reduces_income_available_for_later_commitments(self):
        rent = CashFlow('Rent', 100, date(2026, 3, 1), frequency='monthly')
        goal = CashFlow('Goal', 1000, date(2026, 7, 1))
        inputs = settings(rent, goal, income=200, emergency_months=0, cash_months=0)
        row = recommend(inputs, TODAY, ALLOCATION, total=2000).rows[2]
        self.assertEqual(row.income_funded, 500)  # February 200; March–May 100 each.
        self.assertEqual(row.amount, 500)

    def test_future_fixed_buffers_cannot_spend_income_already_promised_to_an_expense(self):
        rent = CashFlow('Rent', 100, date(2026, 3, 1), frequency='monthly')
        goal = CashFlow('Goal', 1000, date(2026, 7, 1))
        inputs = settings(rent, goal, income=200)
        recommendation = recommend(inputs, TODAY, ALLOCATION, total=1000)
        row = recommendation.rows[2]
        self.assertEqual(row.income_funded, 200)  # February only; later savings build new buffers.
        self.assertEqual(row.amount, 800)
        result = project(inputs, TODAY, ALLOCATION, total=row.amount, years=1)
        self.assertFalse(result.shortfalls)
        self.assertFalse(result.deadline_shortfalls)

    def test_income_commitments_are_shared_once_across_installments(self):
        loan = CashFlow('Loan', 250, date(2026, 4, 1), frequency='monthly', count=3)
        inputs = settings(loan, income=100, emergency_months=0, cash_months=0)
        rows = recommend(inputs, TODAY, ALLOCATION, total=1000).rows[2:-1]
        self.assertEqual([row.income_funded for row in rows], [100, 100, 100])
        self.assertEqual(sum(row.amount for row in rows), 450)
        self.assertTrue(all(row.outgoing_index == 0 for row in rows))
        self.assertEqual([row.due for row in rows], [date(2026, 4, 1), date(2026, 5, 1), date(2026, 6, 1)])

    def test_installments_obey_the_same_two_month_cash_deadline(self):
        loan = CashFlow('Loan', 100, date(2026, 7, 1), frequency='monthly', count=2)
        inputs = settings(loan, emergency_months=0, cash_months=0)
        result = project(inputs, TODAY, ALLOCATION, total=200, years=1)
        may = max(index for index, day in enumerate(result.dates) if day == date(2026, 5, 1))
        june = max(index for index, day in enumerate(result.dates) if day == date(2026, 6, 1))
        self.assertEqual(result.portfolio[CASH][may], 100)
        self.assertEqual(result.portfolio[CASH][june], 200)
        self.assertEqual(total_at(result, -1), 0)

    def test_recommendation_includes_installments_beyond_the_plot_horizon(self):
        loan = CashFlow('Loan', 100, date(2026, 2, 1), frequency='monthly', count=24)
        inputs = settings(loan, emergency_months=0, cash_months=0)
        result = recommend(inputs, TODAY, ALLOCATION, total=10000)
        self.assertEqual(len(result.rows[2:-1]), 24)
        for years in (1, 3):
            projection = project(inputs, TODAY, ALLOCATION, total=10000, years=years)
            self.assertEqual(projection.purpose['Planned expenses'][0], 2400)

    def test_foreign_currency_installments_are_converted_once(self):
        rent = CashFlow('Rent', 1.5, TODAY, 'EUR', 'monthly')
        loan = CashFlow('Loan', 2, date(2026, 2, 1), 'GBP', 'monthly', count=2)
        inputs = settings(rent, loan, income=10, currency='EUR')
        result = project(inputs, TODAY, ALLOCATION, total=10000, rates={'EUR': 400, 'GBP': 500},
                         years=1, currency='EUR')
        self.assertEqual(total_at(result, -1), (10000 + 12 * 3400 - 2000) / 400)

    def test_zero_income_months_are_skipped_without_stalling(self):
        payment = settings(CashFlow('Goal', 200, date(2026, 7, 1))).scheduled_payments(TODAY)
        receipts = ((date(2026, 2, 1), 0), (date(2026, 3, 1), 100),
                    (date(2026, 4, 1), 0), (date(2026, 5, 1), 100))
        gaps, commitments = expense_income(payment, [200], receipts, 0, 0)
        self.assertEqual(gaps, [0])
        self.assertEqual(set(commitments), {date(2026, 3, 1), date(2026, 5, 1)})

    def test_month_summary_separates_finite_recurring_and_single_cash_flows(self):
        inputs = CalculatorSettings(
            incomes=(CashFlow('Contract', 1000, date(2026, 1, 31), frequency='monthly', count=2),
                     CashFlow('Bonus', 200, date(2026, 2, 15))),
            outgoings=(CashFlow('Rent', 400, TODAY, frequency='monthly'),
                       CashFlow('Loan', 100, date(2026, 2, 28), frequency='monthly', end=date(2026, 3, 31)),
                       CashFlow('Trip', 2000, date(2026, 2, 20))))
        february = inputs.monthly_totals(2026, 2)
        self.assertEqual((february.recurring_income, february.single_income,
                          february.recurring_outgoings, february.single_outgoings), (1000, 200, 500, 2000))
        self.assertEqual((february.income, february.outgoings, february.net), (1200, 2500, -1300))
        march = inputs.monthly_totals(2026, 3)
        self.assertEqual((march.income, march.outgoings), (0, 500))
        self.assertEqual(inputs.monthly_totals(2026, 4).outgoings, 400)

    def test_income_in_past_and_current_months_is_already_in_the_recorded_portfolio(self):
        inputs = CalculatorSettings(incomes=(CashFlow('Past', 100, date(2025, 12, 31)),
                                             CashFlow('Today', 200, TODAY),
                                             CashFlow('Next month', 300, date(2026, 2, 2))))
        self.assertEqual(inputs.income_receipts(TODAY, date(2026, 2, 1)), ((date(2026, 2, 1), 300),))

    def test_current_recurring_month_is_recorded_and_next_month_uses_whole_budget(self):
        as_of = date(2026, 1, 15)
        inputs = CalculatorSettings(incomes=(CashFlow('Salary', 1000, date(2026, 1, 31), frequency='monthly'),),
                                    outgoings=(CashFlow('Already paid', 300, TODAY, frequency='monthly'),
                                               CashFlow('Today', 100, as_of, frequency='monthly'),
                                               CashFlow('Later', 200, date(2026, 1, 20), frequency='monthly')))
        self.assertEqual(inputs.income_receipts(as_of, date(2026, 2, 28)),
                         ((date(2026, 2, 1), 400),))

    def test_early_income_cannot_promise_later_ongoing_costs_to_a_goal(self):
        inputs = CalculatorSettings(incomes=(CashFlow('Salary', 1000, date(2026, 1, 5)),),
                                    outgoings=(CashFlow('Rent', 300, date(2026, 1, 31), frequency='monthly'),))
        self.assertEqual(inputs.income_receipts(date(2025, 12, 31), date(2026, 1, 15)), ((date(2026, 1, 1), 700),))

    def test_same_month_receipts_cover_monthly_costs_once(self):
        inputs = CalculatorSettings(incomes=(CashFlow('First', 100, date(2026, 1, 5)),
                                             CashFlow('Second', 300, date(2026, 1, 20)),
                                             CashFlow('Third', 50, date(2026, 1, 20))),
                                    outgoings=(CashFlow('Living', 200, date(2026, 1, 31), frequency='monthly'),))
        self.assertEqual(inputs.income_receipts(date(2025, 12, 31), date(2026, 1, 31)), ((date(2026, 1, 1), 250),))

    def test_monthly_income_deficit_is_zero_without_carrying_phantom_debt(self):
        inputs = CalculatorSettings(incomes=(CashFlow('January', 100, date(2026, 1, 5)),
                                             CashFlow('February', 500, date(2026, 2, 5))),
                                    outgoings=(CashFlow('Living', 200, TODAY, frequency='monthly'),))
        self.assertEqual(inputs.income_receipts(date(2025, 12, 31), date(2026, 2, 28)), ((date(2026, 2, 1), 300),))

    def test_single_income_can_fund_only_expenses_ready_on_or_after_its_receipt(self):
        bonus = CashFlow('Bonus', 100, date(2026, 2, 15))
        early = CashFlow('Early', 100, date(2026, 3, 1))
        on_time = CashFlow('On time', 100, date(2026, 4, 15))
        inputs = CalculatorSettings(incomes=(bonus,), outgoings=(early, on_time), emergency_months=0,
                                    cash_months=0, returns=ReturnAssumptions(equities=0, mak=0))
        rows = recommend(inputs, TODAY, ALLOCATION, total=1000).rows[2:-1]
        self.assertEqual(rows[0].income_funded, 0)
        self.assertAlmostEqual(rows[1].income_funded, 100 - rows[1].stock)
        self.assertGreater(rows[1].stock, 0)
        result = project(inputs, TODAY, ALLOCATION, total=1000, years=1)
        self.assertIn(bonus.start, result.dates)
        indices = [index for index, day in enumerate(result.dates) if day == bonus.start]
        self.assertEqual(total_at(result, indices[-1]) - total_at(result, indices[0]), 100)
        self.assertEqual(total_at(result, -1), 900)

    def test_finite_income_stops_and_foreign_currency_is_converted_once(self):
        inputs = CalculatorSettings(incomes=(CashFlow('Contract', 2.5, date(2026, 1, 31), 'EUR', 'monthly', count=2),
                                             CashFlow('Bonus', 1, date(2026, 3, 15), 'GBP')),
                                    outgoings=(CashFlow('Living', 200, TODAY, frequency='monthly'),),
                                    returns=ReturnAssumptions(equities=0, mak=0))
        rates = {'EUR': 400, 'GBP': 500}
        self.assertEqual(inputs.income_receipts(TODAY, date(2026, 12, 31), rates),
                         ((date(2026, 2, 1), 800), (date(2026, 3, 1), 300)))
        result = project(inputs, TODAY, ALLOCATION, total=10000, rates=rates, years=1)
        self.assertEqual(total_at(result, -1), 11100)


if __name__ == '__main__':
    unittest.main()
