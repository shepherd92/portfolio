"""Entered payment growth is shared by budgets, reserve targets and projections."""

from dataclasses import replace
from datetime import date
from math import fsum
import unittest

from support import calculator_allocation
from portfolio.calculator import CalculatorSettings, CashFlow, ReturnAssumptions, funding_calendar, recommend
from portfolio.projection import project
from portfolio.wealth import (Loan, Pension, PensionContribution, Property, PropertyCashFlow,
                             WealthSettings)


TODAY = date(2026, 1, 15)
ZERO_RETURNS = ReturnAssumptions(equities=0, gold=0, bonds=0, mak_huf=0, mak_eur=0)


class CashFlowAppreciationTests(unittest.TestCase):
    def test_beginning_month_is_base_and_recurring_growth_compounds_monthly(self):
        flow = CashFlow('Salary', 1000, date(2025, 1, 20), frequency='monthly', appreciation_percent=12)
        self.assertEqual(flow.amount_on(date(2025, 1, 31)), 1000)
        self.assertAlmostEqual(flow.amount_on(date(2025, 7, 31)), 1000 * 1.12 ** .5)
        self.assertAlmostEqual(flow.amount_on(date(2026, 1, 1)), 1120)
        self.assertAlmostEqual(replace(flow, appreciation_percent=-75).amount_on(date(2026, 1, 1)), 250)
        self.assertEqual(replace(flow, frequency='once').amount_on(date(2026, 1, 1)), 1000)
        self.assertEqual(flow.amount, 1000)

    def test_invalid_appreciation_and_numeric_overflow_report_validation_errors(self):
        for value in (-100, -101, 1000.01, float('nan'), float('inf'), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                CashFlow('Cost', 100, TODAY, appreciation_percent=value)
        flowing = CashFlow('Cost', 1e308, TODAY, frequency='monthly', appreciation_percent=1000)
        with self.assertRaisesRegex(ValueError, 'appreciated payment.*numeric range'):
            flowing.amount_on(date(2027, 1, 1))
        self.assertGreater(CashFlow('Cost', 100, TODAY, appreciation_percent=-99.999).appreciation_percent, -100)
        shrinking = CashFlow('Cost', 1, TODAY, frequency='monthly', appreciation_percent=-99.999)
        with self.assertRaisesRegex(ValueError, 'appreciated payment.*numeric range'):
            shrinking.amount_on(date(2126, 1, 1))

    def test_actual_annual_payments_and_smoothed_monthly_budget_share_growth(self):
        flow = CashFlow('Annual insurance', 1200, date(2025, 1, 1), frequency='annual',
                        appreciation_percent=12)
        settings = CalculatorSettings(outgoings=(flow,))
        self.assertAlmostEqual(settings.monthly_totals(2026, 1).recurring_outgoings, 1344)
        self.assertEqual(settings.monthly_totals(2026, 7).recurring_outgoings, 0)
        self.assertAlmostEqual(settings.monthly_spending(as_of=date(2026, 7, 1)), 100 * 1.12 ** 1.5)

    def test_income_and_finite_expenses_grow_from_begins_and_exclude_received_month(self):
        income = CashFlow('Salary', 1000, date(2025, 1, 1), frequency='monthly', appreciation_percent=12)
        cost = CashFlow('Loan payments', 100, date(2026, 1, 1), frequency='monthly', count=3,
                        appreciation_percent=10)
        settings = CalculatorSettings(incomes=(income,), outgoings=(cost,))
        self.assertAlmostEqual(settings.monthly_totals(2026, 1).recurring_income, 1120)
        receipts = settings.income_receipts(TODAY, date(2026, 3, 1))
        self.assertEqual([day for day, _ in receipts], [date(2026, 2, 1), date(2026, 3, 1)])
        self.assertAlmostEqual(receipts[0][1], 1000 * 1.12 ** (13 / 12))
        payments = settings.scheduled_payments(TODAY)
        self.assertEqual([payment.due for payment in payments], [date(2026, 2, 1), date(2026, 3, 1)])
        self.assertAlmostEqual(payments[0].amount, 100 * 1.10 ** (1 / 12))
        self.assertAlmostEqual(payments[1].amount, 100 * 1.10 ** (2 / 12))

    def test_growth_precedes_currency_conversion_and_generated_huf_keeps_decimals(self):
        flow = CashFlow('Living', 100, date(2025, 1, 1), 'EUR', 'monthly', appreciation_percent=10)
        settings = CalculatorSettings(outgoings=(flow,))
        expected = 100 * 1.10 ** (13 / 12) * 401.5
        self.assertAlmostEqual(settings.monthly_spending({'EUR': 401.5}, as_of=date(2026, 2, 1)), expected)
        equivalent = replace(flow, amount=40150, currency='HUF')
        self.assertAlmostEqual(CalculatorSettings(outgoings=(equivalent,)).monthly_spending(as_of=date(2026, 2, 1)), expected)
        self.assertNotEqual(equivalent.amount_on(date(2026, 2, 1)), round(expected))

    def test_growing_buffers_are_funded_before_expense_commitments(self):
        settings = CalculatorSettings(
            incomes=(CashFlow('Salary', 1000, TODAY, frequency='monthly'),),
            outgoings=(CashFlow('Living', 100, TODAY, frequency='monthly', appreciation_percent=12),
                       CashFlow('Purchase', 10000, date(2026, 6, 1))),
            emergency_months=2, cash_months=1, returns=ZERO_RETURNS)
        result = recommend(settings, TODAY, calculator_allocation(), total=20000)
        february = result.funding_schedule.entries[0]
        expected_living = 100 * 1.12 ** (1 / 12)
        expected_buffer = (expected_living - 100) * 3
        self.assertAlmostEqual(february.available, 1000 - expected_living)
        self.assertAlmostEqual(february.buffer_reserved, expected_buffer)
        self.assertAlmostEqual(fsum(value for _, value in february.expenses), february.available - expected_buffer)
        row = next(row for row in funding_calendar(settings, TODAY, result) if row.day == february.day)
        self.assertAlmostEqual(row.totals.recurring_outgoings, expected_living)
        self.assertAlmostEqual(row.budget.living_costs, expected_living)

    def test_buffer_changes_validate_increments_without_overflowing_current_reserve(self):
        for growth in (0, 10):
            settings = CalculatorSettings(
                outgoings=(CashFlow('Living', 1e308, TODAY, frequency='monthly',
                                   appreciation_percent=growth),),
                emergency_months=2, cash_months=1, returns=ZERO_RETURNS)
            changes = settings.buffer_changes(TODAY)
            self.assertAlmostEqual(fsum(value for _, value in changes) / 1e308, .3 if growth else 0)
            future = replace(settings, outgoings=(replace(settings.outgoings[0], start=date(2026, 2, 1)),))
            with self.subTest(growth=growth), self.assertRaisesRegex(ValueError, 'Fixed buffers.*numeric range'):
                future.buffer_changes(TODAY)

    def test_declining_cost_cancels_unpaid_buffer_backlog_without_inventing_income(self):
        settings = CalculatorSettings(
            incomes=(CashFlow('Bonus', 200, date(2026, 6, 1)),),
            outgoings=(CashFlow('New cost', 100, date(2026, 2, 1), frequency='monthly', appreciation_percent=-75),),
            emergency_months=1, cash_months=0, returns=ZERO_RETURNS)
        result = recommend(settings, TODAY, calculator_allocation(), total=1000)
        receipt, = result.funding_schedule.entries
        current_cost = 100 * .25 ** (4 / 12)
        self.assertAlmostEqual(receipt.available, 200 - current_cost)
        self.assertAlmostEqual(receipt.buffer_reserved, current_cost)
        self.assertAlmostEqual(receipt.uncommitted, 200 - 2 * current_cost)
        self.assertTrue(any(change < 0 for _, change in result.buffer_changes))

    def test_projection_releases_shrinking_buffers_at_month_start_without_income(self):
        settings = CalculatorSettings(
            outgoings=(CashFlow('Living', 100, date(2026, 1, 1), frequency='monthly', appreciation_percent=-10),),
            emergency_months=2, cash_months=1, returns=ZERO_RETURNS)
        projection = project(settings, TODAY, calculator_allocation(), total=1000, years=1)
        february = max(index for index, day in enumerate(projection.dates) if day == date(2026, 2, 1))
        cost = 100 * .9 ** (1 / 12)
        self.assertAlmostEqual(projection.purpose['Emergency fund'][february], 2 * cost)
        self.assertAlmostEqual(projection.purpose['Monthly living costs'][february], cost)
        self.assertAlmostEqual(projection.purpose['Long-term investment'][february], 1000 - 3 * cost)
        self.assertAlmostEqual(projection.purpose['Emergency fund'][-1], 180)
        self.assertAlmostEqual(projection.purpose['Monthly living costs'][-1], 90)
        for values in zip(*projection.portfolio.values()):
            self.assertAlmostEqual(fsum(values), 1000)

    def test_projection_total_equals_actual_growing_surplus_without_double_growth(self):
        settings = CalculatorSettings(
            incomes=(CashFlow('Salary', 1000, date(2026, 1, 1), frequency='monthly', appreciation_percent=8),),
            outgoings=(CashFlow('Living', 100, date(2026, 1, 1), frequency='monthly', appreciation_percent=12),),
            emergency_months=2, cash_months=1, returns=ZERO_RETURNS)
        projection = project(settings, TODAY, calculator_allocation(), total=1000, years=1)
        savings = fsum(1000 * 1.08 ** (month / 12) - 100 * 1.12 ** (month / 12) for month in range(1, 13))
        self.assertAlmostEqual(fsum(values[-1] for values in projection.portfolio.values()), 1000 + savings)
        self.assertAlmostEqual(projection.purpose['Emergency fund'][-1], 224)
        self.assertAlmostEqual(projection.purpose['Monthly living costs'][-1], 112)

    def test_property_growth_is_applied_once_and_linked_pension_loan_use_shared_amounts(self):
        base = CashFlow('Rent', 1000, date(2025, 1, 1), frequency='annual')
        property = Property('flat', 'Flat', 10000, vacancy_percent=25,
                            cashflows=(PropertyCashFlow(base, 'income', 'rented', 10),))
        events = WealthSettings(properties=(property,)).events(date(2025, 12, 1), date(2026, 1, 1))
        self.assertAlmostEqual(events[0].amount, 1000 * 1.1 * .75)
        with self.assertRaisesRegex(ValueError, 'nested schedule'):
            PropertyCashFlow(replace(base, appreciation_percent=10), appreciation_percent=10)
        payment = CashFlow('Payment', 100, date(2025, 1, 1), frequency='annual', appreciation_percent=10)
        pension = Pension('pension', 'Pension', access=date(2060, 1, 1),
                          contributions=(PensionContribution(payment),))
        loan = Loan('loan', 'Loan', 1000, payment)
        events = WealthSettings(pensions=(pension,), loans=(loan,)).events(date(2025, 12, 1), date(2026, 1, 1))
        self.assertEqual(len(events), 2)
        for event in events:
            self.assertAlmostEqual(event.amount, 110)


if __name__ == '__main__':
    unittest.main()
