"""Savings are assigned once; calendar, targets and projections use that evidence."""

from dataclasses import replace
from datetime import date
from math import fsum
import unittest

from support import calculator_allocation
from portfolio.calculator import (CalculatorSettings, CashFlow, Payment, ReturnAssumptions,
                                  expense_income, funding_calendar, recommend)
from portfolio.models import AllocationTargets, CASH, STOCK_MARKET, TREASURY
from portfolio.projection import project


TODAY = date(2025, 1, 1)


class FundingCalendarTests(unittest.TestCase):
    def test_equal_deadlines_share_remaining_needs_and_ignore_input_order(self):
        april, june = date(2025, 4, 1), date(2025, 6, 1)
        expenses = (Payment('A', 100, june, 'HUF', 0), Payment('B', 300, june, 'HUF', 1))
        receipts = ((date(2025, 2, 1), 80), (april, 80), (date(2025, 5, 1), 100))
        for rate in (-25, 0, 10):
            schedule = expense_income(expenses, (100, 300), receipts, rate, 2)
            reversed_schedule = expense_income(expenses[::-1], (300, 100), receipts, rate, 2)
            for forward, backward in zip(schedule.entries, reversed_schedule.entries):
                self.assertEqual(dict(forward.expenses), {1 - i: value for i, value in backward.expenses})
                if forward.expenses:
                    self.assertAlmostEqual(dict(forward.expenses)[1], 3 * dict(forward.expenses)[0])
                self.assertAlmostEqual(forward.available,
                                       forward.buffer_reserved + fsum(value for _, value in forward.expenses)
                                       + forward.uncommitted)
            self.assertEqual(schedule.entries[-1].expenses, ())
            expected = 80 * (1 + rate / 100) ** (59 / 365.2425) + 80
            self.assertAlmostEqual(fsum(schedule.gaps), 400 - expected)

    def test_earliest_group_is_filled_then_leftovers_reach_next_group(self):
        expenses = (Payment('Later', 200, date(2025, 7, 1), 'HUF', 0),
                    Payment('Early A', 30, date(2025, 5, 1), 'HUF', 1),
                    Payment('Early B', 60, date(2025, 5, 1), 'HUF', 2))
        schedule = expense_income(expenses, (200, 30, 60), ((date(2025, 2, 1), 150),), 0, 2)
        self.assertEqual(schedule.entries[0].expenses, ((1, 30), (2, 60), (0, 60)))
        self.assertEqual(schedule.gaps, (140, 0, 0))

    def test_fixed_buffer_backlog_and_uncommitted_savings_remain_in_ledger(self):
        receipts = tuple((date(2025, month, 1), 50) for month in range(2, 6))
        schedule = expense_income((), (), receipts, 5, 2,
                                  buffer_changes=((date(2025, 2, 1), 120),))
        self.assertEqual([entry.buffer_reserved for entry in schedule.entries], [50, 50, 20, 0])
        self.assertEqual([entry.uncommitted for entry in schedule.entries], [0, 0, 30, 50])

    def test_calendar_includes_no_income_months_and_actual_annual_cost_without_double_counting(self):
        settings = CalculatorSettings(
            incomes=(CashFlow('Salary', 2000, TODAY, frequency='monthly', end=date(2025, 2, 1)),),
            outgoings=(CashFlow('Annual insurance', 1200, TODAY, frequency='annual'),
                       CashFlow('Purchase', 500, date(2025, 4, 1))),
            returns=ReturnAssumptions(equities=0, mak_huf=0),
            emergency_months=0, cash_months=0)
        result = recommend(settings, TODAY, calculator_allocation(), total=10000)
        rows = funding_calendar(settings, TODAY, result)
        january, february, march, april = rows[:4]
        self.assertEqual(january.totals.recurring_outgoings, 1200)
        self.assertEqual(january.budget.living_costs, 100)
        self.assertIsNone(january.assignment)
        self.assertEqual(february.budget.surplus, 1900)
        self.assertEqual(february.assignment.available, 1900)
        self.assertEqual(march.budget.living_costs, 100)
        self.assertEqual(march.budget.surplus, 0)
        self.assertIsNone(march.assignment)
        self.assertEqual(april.totals.single_outgoings, 500)
        self.assertEqual(february.deadlines, (0,))
        self.assertEqual(april.due_expenses, (0,))
        self.assertEqual(february.assignment.expenses, result.funding_schedule.entries[0].expenses)

    def test_overdue_once_payment_and_passed_deadline_remain_in_opening_month(self):
        settings = CalculatorSettings(outgoings=(CashFlow('Overdue', 100, date(2024, 11, 1)),
                                                 CashFlow('Soon', 200, date(2025, 2, 1))),
                                      returns=ReturnAssumptions(mak_huf=0))
        result = recommend(settings, TODAY, calculator_allocation(), total=1000)
        months = funding_calendar(settings, TODAY, result)
        self.assertEqual(months[0].deadlines, (0, 1))
        self.assertEqual(months[0].due_expenses, (0,))
        self.assertEqual(months[1].due_expenses, (1,))
        self.assertEqual(result.expenses[0].payment.due, date(2024, 11, 1))
        self.assertEqual(result.expenses[0].deadline, date(2024, 9, 1))
        self.assertEqual(result.expenses[1].deadline, date(2024, 12, 1))
        self.assertEqual(months[0].totals.single_outgoings, 0)

    def test_extending_calendar_never_changes_expense_commitments_or_today_targets(self):
        settings = CalculatorSettings(incomes=(CashFlow('Income', 100, TODAY, frequency='monthly'),),
                                      outgoings=(CashFlow('Expense', 1000, date(2025, 6, 1)),),
                                      returns=ReturnAssumptions(mak_huf=5))
        short = recommend(settings, TODAY, calculator_allocation(), total=2000)
        long = recommend(settings, TODAY, calculator_allocation(), total=2000,
                         funding_end=date(2030, 1, 1))
        self.assertEqual(short.rows, long.rows)
        self.assertEqual(short.expenses, long.expenses)
        self.assertEqual(short.funding_schedule.entries, long.funding_schedule.entries[:12])


class TreasuryReturnTests(unittest.TestCase):
    targets = AllocationTargets({STOCK_MARKET: {'Global': 60}, TREASURY: {'HUF': 10, 'EUR': 30}, CASH: {}},
                                {'Global': 'Equities'})

    def settings(self, **values):
        return CalculatorSettings(returns=ReturnAssumptions(equities=8, mak_huf=6, mak_eur=2),
                                  **values)

    def test_recommendation_weights_protected_huf_and_long_term_currency_mix(self):
        settings = self.settings(emergency_months=2, cash_months=1,
                                 outgoings=(CashFlow('Living', 100, TODAY, frequency='monthly'),))
        result = recommend(settings, TODAY, self.targets, total=1000)
        self.assertEqual((result.stock, result.mak_huf, result.mak_eur, result.cash), (420, 270, 210, 100))
        self.assertEqual(result.long_term_mak_return, 3)
        self.assertAlmostEqual(result.expected_return, .42 * 8 + .27 * 6 + .21 * 2)
        changed = recommend(replace(settings, returns=replace(settings.returns, mak_eur=10)),
                            TODAY, self.targets, total=1000)
        self.assertEqual(changed.rows, result.rows)
        self.assertAlmostEqual(changed.expected_return - result.expected_return, .21 * 8)

    def test_eur_return_does_not_change_emergency_or_expense_reserves(self):
        settings = self.settings(outgoings=(CashFlow('Purchase', 500, date(2026, 7, 1)),))
        original = recommend(settings, TODAY, self.targets, total=1000)
        changed = recommend(replace(settings, returns=replace(settings.returns, mak_eur=50)),
                            TODAY, self.targets, total=1000)
        self.assertEqual(original.rows, changed.rows)
        self.assertEqual(original.expenses, changed.expenses)

    def test_projection_blends_long_term_currency_returns_and_keeps_huf_reserve_rate(self):
        settings = self.settings(emergency_months=0, cash_months=0)
        result = project(settings, TODAY, self.targets, total=1000, years=1)
        expected = 1000 * (1 + (.6 * 8 + .1 * 6 + .3 * 2) / 100) ** (365 / 365.2425)
        self.assertAlmostEqual(fsum(values[-1] for values in result.portfolio.values()), expected)
        huf_only = replace(settings, outgoings=(CashFlow('Living', 100, TODAY, frequency='monthly'),),
                           emergency_months=10)
        first = project(huf_only, TODAY, self.targets, total=1000, years=1)
        second = project(replace(huf_only, returns=replace(huf_only.returns, mak_eur=90)),
                         TODAY, self.targets, total=1000, years=1)
        # Emergency earnings move to long-term investment; its EUR rate then matters.
        self.assertEqual(first.purpose['Emergency fund'], second.purpose['Emergency fund'])
        self.assertGreater(second.purpose['Long-term investment'][-1], first.purpose['Long-term investment'][-1])

    def test_rates_are_validated_separately_and_eur_has_no_invented_forecast(self):
        self.assertEqual(ReturnAssumptions().mak_eur, 0)
        self.assertIsNone(ReturnAssumptions().mak_huf)
        for name in ('mak_huf', 'mak_eur'):
            for invalid in (-100, float('nan'), float('inf'), True):
                with self.subTest(name=name, invalid=invalid), self.assertRaises(ValueError):
                    ReturnAssumptions(**{name: invalid})


if __name__ == '__main__':
    unittest.main()
