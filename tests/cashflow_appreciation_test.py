"""Entered payment growth is shared by budgets, reserve targets and projections."""

from dataclasses import replace
from datetime import date
from math import fsum
import unittest

from support import calculator_allocation
from portfolio.calculator import (CalculatorSettings, CashFlow, ReturnAssumptions, expense_cash_date,
                                  funding_calendar, recommend)
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
        self.assertAlmostEqual(fsum(amount for _, amount in receipt.expenses), 200 - 2 * current_cost)
        self.assertEqual(receipt.uncommitted, 0)
        self.assertTrue(any(change < 0 for _, change in result.buffer_changes))

    def test_projection_releases_shrinking_buffers_at_month_start_without_income(self):
        settings = CalculatorSettings(
            outgoings=(CashFlow('Living', 100, date(2026, 1, 1), frequency='monthly', appreciation_percent=-10),),
            emergency_months=2, cash_months=1, returns=ZERO_RETURNS)
        projection = project(settings, TODAY, calculator_allocation(), total=3000, years=1)
        february = max(index for index, day in enumerate(projection.dates) if day == date(2026, 2, 1))
        cost = 100 * .9 ** (1 / 12)
        self.assertAlmostEqual(projection.purpose['Emergency fund'][february], 2 * cost)
        self.assertAlmostEqual(projection.purpose['Monthly living costs'][february], cost)
        self.assertAlmostEqual(fsum(values[february] for values in projection.portfolio.values()), 3000 - cost)
        self.assertAlmostEqual(projection.purpose['Emergency fund'][-1], 180)
        self.assertAlmostEqual(projection.purpose['Monthly living costs'][-1], 90)
        self.assertAlmostEqual(fsum(values[-1] for values in projection.portfolio.values()),
                               3000 - fsum(100 * .9 ** (month / 12) for month in range(1, 13)))

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
        base = CashFlow('Rent', 1000, date(2025, 1, 1), frequency='annual', appreciation_percent=10)
        property = Property('flat', 'Flat', 10000, vacancy_percent=25,
                            cashflows=(PropertyCashFlow(base, 'income', 'rented'),))
        events = WealthSettings(properties=(property,)).events(date(2025, 12, 1), date(2026, 1, 1))
        self.assertAlmostEqual(events[0].amount, 1000 * 1.1 * .75)
        payment = CashFlow('Payment', 100, date(2025, 1, 1), frequency='annual', appreciation_percent=10)
        pension = Pension('pension', 'Pension', access=date(2060, 1, 1),
                          contributions=(PensionContribution(payment),))
        loan = Loan('loan', 'Loan', 1000, payment)
        events = WealthSettings(pensions=(pension,), loans=(loan,)).events(date(2025, 12, 1), date(2026, 1, 1))
        self.assertEqual(len(events), 2)
        for event in events:
            self.assertAlmostEqual(event.amount, 110)

    def test_growth_modes_and_once_price_month_have_one_nominal_resolver(self):
        start, due = date(2026, 1, 1), date(2028, 1, 1)
        fixed = CashFlow('Fixed', 100, start, frequency='monthly', appreciation_percent=7, growth_mode='fixed')
        following = replace(fixed, growth_mode='inflation')
        custom = replace(fixed, growth_mode='custom')
        self.assertEqual(fixed.amount_on(due, 10), 100)
        self.assertAlmostEqual(following.amount_on(due, 10), 121)
        self.assertAlmostEqual(custom.amount_on(due, 10), 100 * 1.07 ** 2)
        nominal = CashFlow('Purchase', 100, due, appreciation_percent=7)
        priced = replace(nominal, price_month=start)
        self.assertEqual(nominal.growth_mode, 'fixed')
        self.assertEqual(nominal.amount_on(due, 10), 100)
        self.assertAlmostEqual(priced.amount_on(due, 10), 121)
        self.assertAlmostEqual(priced.amount_on(date(2030, 1, 1), 10), 121)
        self.assertEqual(CashFlow('Salary', 100, start, frequency='monthly', appreciation_percent=7).growth_mode, 'custom')

    def test_inflation_reaches_totals_buffers_income_and_commitments(self):
        start = date(2026, 1, 1)
        income = CashFlow('Salary', 200, start, frequency='monthly', growth_mode='inflation')
        living = CashFlow('Living', 100, start, frequency='monthly', growth_mode='inflation')
        purchase = CashFlow('Purchase', 1000, date(2028, 1, 1), price_month=start)
        settings = CalculatorSettings(incomes=(income,), outgoings=(living, purchase), inflation_percent=10,
                                      returns=ZERO_RETURNS)
        self.assertAlmostEqual(settings.monthly_spending(as_of=date(2027, 1, 1)), 110)
        self.assertAlmostEqual(settings.monthly_totals(2027, 1).income, 220)
        self.assertAlmostEqual(settings.income_budgets(start, date(2027, 1, 1))[-1].surplus, 110)
        self.assertAlmostEqual(settings.scheduled_payments(start)[0].amount, 1210)
        self.assertAlmostEqual(fsum(value for _, value in settings.buffer_changes(start)), 80)

    def test_invalid_inflation_growth_mode_and_price_reference_are_rejected(self):
        for value in (-100, 1000.01, float('nan'), float('inf'), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                CalculatorSettings(inflation_percent=value)
        for values in ({'growth_mode': 'real'}, {'price_month': '2025-01'},
                       {'price_month': date(2027, 1, 1)},
                       {'frequency': 'monthly', 'price_month': date(2025, 1, 1)}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                CashFlow('Payment', 100, date(2026, 1, 1), **values)

    def test_saved_price_month_inflates_once_before_nominal_reserve_discounting(self):
        due = date(2028, 1, 1)
        flow = CashFlow('Purchase', 1000, due, price_month=date(2026, 1, 1))
        returns = ReturnAssumptions(equities=12, bonds=12, gold=12, mak_huf=12, mak_eur=12)
        settings = CalculatorSettings(outgoings=(flow,), inflation_percent=10, returns=returns)
        result = recommend(settings, TODAY, calculator_allocation(), total=10000)
        payment = result.expenses[0]
        nominal = 1000 * 1.1 ** 2
        days = (expense_cash_date(due, settings.cash_months) - TODAY).days
        self.assertAlmostEqual(payment.payment.amount, nominal)
        self.assertAlmostEqual(payment.full_reserve, nominal / 1.12 ** (days / 365.2425))
        projected = project(settings, TODAY, calculator_allocation(), total=10000, years=2)
        indices = [index for index, day in enumerate(projected.dates) if day == due]
        before, after = (fsum(values[index] for values in projected.portfolio.values()) for index in indices)
        self.assertAlmostEqual(before - after, nominal)
        # Reopening later preserves the entered price anchor, not the opening date.
        later = recommend(settings, date(2027, 1, 15), calculator_allocation(), total=10000)
        self.assertAlmostEqual(later.expenses[0].payment.amount, nominal)


if __name__ == '__main__':
    unittest.main()
