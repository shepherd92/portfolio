"""Evidence in a plan must reconcile to the calculation that produced it."""

from dataclasses import FrozenInstanceError, replace
from datetime import date
from math import fsum
import unittest

from support import calculator_allocation
from portfolio.calculator import CalculatorSettings, CashFlow, ReturnAssumptions, recommend
from portfolio.funding import prepare_allocation
from portfolio.models import Holding, MarketData, Portfolio, Quote


class CalculationTraceTests(unittest.TestCase):
    today = date(2025, 1, 1)

    def settings(self, **values):
        return CalculatorSettings(**{
            'emergency_months': 0,
            'cash_months': 0,
            'returns': ReturnAssumptions(equities=0, mak_huf=0),
            **values,
        })

    def calculate(self, settings, total=10_000, rates=None):
        return recommend(settings, self.today, calculator_allocation(), total=total, rates=rates)

    def test_receipts_are_assigned_once_by_deadline_with_original_expense_order_preserved(self):
        settings = self.settings(
            incomes=(CashFlow('Salary', 100, self.today, frequency='monthly'),),
            outgoings=(CashFlow('Later', 300, date(2025, 7, 1)),
                       CashFlow('Sooner', 300, date(2025, 5, 1))),
        )
        result = self.calculate(settings)
        later, sooner = result.expenses
        self.assertEqual([item.payment.name for item in result.expenses], ['Later', 'Sooner'])
        self.assertEqual(sooner.deadline, date(2025, 3, 1))
        self.assertEqual(sooner.contributions, ((date(2025, 2, 1), 100), (date(2025, 3, 1), 100)))
        self.assertEqual(later.contributions, ((date(2025, 4, 1), 100), (date(2025, 5, 1), 100)))
        self.assertEqual(result.receipts[:4], tuple((date(2025, month, 1), 100) for month in range(2, 6)))
        for day, available in result.receipts:
            assigned = fsum(amount for item in result.expenses for when, amount in item.contributions if when == day)
            self.assertLessEqual(assigned, available)
        for item, row in zip(result.expenses, result.rows[2:-1]):
            self.assertEqual(item.income_funded, 200)
            self.assertEqual(row.amount, item.full_reserve - item.released_reserve)
            self.assertAlmostEqual(row.stock, item.full_reserve * item.stock_fraction)
        self.assertEqual(fsum(amount for item in result.expenses for _, amount in item.contributions), 400)
        with self.assertRaises(FrozenInstanceError):
            sooner.income_funded = 0

    def test_one_receipt_can_be_split_without_double_counting_its_principal(self):
        settings = self.settings(
            incomes=(CashFlow('Bonus', 100, date(2025, 2, 1)),),
            outgoings=(CashFlow('First', 100, date(2025, 4, 1)),
                       CashFlow('Second', 100, date(2025, 4, 1))),
        )
        first, second = self.calculate(settings).expenses
        self.assertAlmostEqual(first.income_funded, 50)
        self.assertEqual(len(first.contributions), 1)
        self.assertEqual(len(second.contributions), 1)
        self.assertEqual(first.contributions[0][0], second.contributions[0][0])
        self.assertAlmostEqual(first.contributions[0][1] + second.contributions[0][1], 100)
        self.assertAlmostEqual(second.income_funded, 50)

    def test_compounded_funding_and_uncapped_reserve_survive_insufficient_wealth(self):
        self.today = date(2024, 1, 1)
        settings = self.settings(
            horizon_years=1,
            returns=ReturnAssumptions(equities=10, mak_huf=10),
            incomes=(CashFlow('Bonus', 20, date(2024, 5, 1)),),
            outgoings=(CashFlow('Purchase', 100, date(2024, 9, 1)),),
        )
        funded = self.calculate(settings)
        capped = self.calculate(settings, total=10)
        item, = capped.expenses
        self.assertEqual(capped.expenses, funded.expenses)
        self.assertEqual(item.deadline, date(2024, 7, 1))
        self.assertAlmostEqual(item.full_reserve, 100 / 1.1 ** (182 / 365.2425))
        self.assertAlmostEqual(item.stock_fraction, 182 / 366)
        self.assertAlmostEqual(item.income_funded, 20 * 1.1 ** (61 / 365.2425))
        self.assertAlmostEqual(item.released_reserve, 20 / 1.1 ** (121 / 365.2425))
        self.assertEqual(item.contributions, ((date(2024, 5, 1), 20),))
        self.assertGreater(item.income_funded, fsum(value for _, value in item.contributions))
        self.assertEqual((capped.rows[2].amount, capped.rows[2].mak, capped.rows[2].stock), (10, 10, 0))
        self.assertAlmostEqual(float(capped.shortfall), item.full_reserve - item.released_reserve - 10)
        self.assertEqual(capped.total, 10)

    def test_future_fixed_buffers_take_priority_over_recorded_expense_contributions(self):
        settings = self.settings(
            emergency_months=2,
            cash_months=1,
            incomes=(CashFlow('Salary', 70, self.today, frequency='monthly'),),
            outgoings=(CashFlow('New living cost', 20, date(2025, 2, 1), frequency='monthly'),
                       CashFlow('Purchase', 300, date(2025, 5, 1))),
        )
        result = self.calculate(settings)
        item, = result.expenses
        self.assertEqual(result.buffer_changes, ((date(2025, 2, 1), 60),))
        self.assertEqual(result.receipts[:2], ((date(2025, 2, 1), 50), (date(2025, 3, 1), 50)))
        self.assertEqual(item.contributions, ((date(2025, 3, 1), 40),))
        self.assertEqual(item.income_funded, 40)
        self.assertEqual(fsum(entry.buffer_reserved for entry in result.funding_schedule.entries), 60)
        self.assertEqual([row.amount for row in result.rows[:2]], [0, 0])

    def test_multicurrency_inputs_have_the_same_huf_trace_and_base_currency_does_not_change_it(self):
        settings = self.settings(
            emergency_months=1,
            cash_months=2,
            incomes=(CashFlow('Salary', 2, self.today, currency='EUR', frequency='monthly'),),
            outgoings=(CashFlow('Living costs', 1, self.today, currency='GBP', frequency='monthly'),
                       CashFlow('Purchase', 10, date(2025, 6, 1), currency='USD')),
        )
        rates = {'EUR': 400, 'GBP': 500, 'USD': 350, 'HUF': 1}
        equivalent = replace(settings,
                             incomes=(replace(settings.incomes[0], amount=800, currency='HUF'),),
                             outgoings=(replace(settings.outgoings[0], amount=500, currency='HUF'),
                                        replace(settings.outgoings[1], amount=3500, currency='HUF')))
        foreign = self.calculate(settings, rates=rates)
        native = self.calculate(equivalent)
        self.assertEqual(foreign.rows, native.rows)
        self.assertEqual(foreign.receipts, native.receipts)
        self.assertEqual(foreign.expenses[0].contributions, native.expenses[0].contributions)
        self.assertEqual(foreign.expenses[0].full_reserve, native.expenses[0].full_reserve)
        self.assertEqual(foreign.expenses[0].payment.currency, 'USD')
        self.assertEqual(foreign.expenses[0].payment.amount, 10)
        self.assertEqual(foreign.rows[2].future_amount, 3500)
        portfolio = Portfolio((Holding('ETF', 'Global', 0),), calculator_allocation(), {'HUF': 10_000})
        results = []
        for base in ('EUR', 'HUF'):
            market = MarketData({'ETF': Quote(10, 'EUR')},
                                {currency: value / rates[base] for currency, value in rates.items()}, base)
            results.append(prepare_allocation(portfolio, market, settings, self.today).recommendation)
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0].expenses, foreign.expenses)

    def test_zero_surplus_retains_gross_income_and_living_budget_evidence(self):
        settings = self.settings(
            incomes=(CashFlow('Salary', 30, self.today, frequency='monthly'),),
            outgoings=(CashFlow('Living costs', 50, self.today, frequency='monthly'),
                       CashFlow('Purchase', 100, date(2025, 5, 1))),
        )
        result = self.calculate(settings)
        self.assertEqual(result.receipts, ())
        self.assertEqual(len(result.income_budgets), 12)
        for budget, month in zip(result.income_budgets, (2, 3)):
            self.assertEqual(budget.day, date(2025, month, 1))
            self.assertEqual((budget.gross_income, budget.living_costs, budget.surplus), (30, 50, 0))
            self.assertEqual(budget.linked_income, ())
            self.assertEqual(budget.obligations, ())
        self.assertEqual(result.expenses[0].contributions, ())
        self.assertEqual(result.expenses[0].full_reserve, result.rows[2].amount)

    def test_ended_income_has_no_invented_future_receipts_or_contributions(self):
        settings = self.settings(
            incomes=(CashFlow('Ended salary', 100, self.today, frequency='monthly', end=self.today),),
            outgoings=(CashFlow('Purchase', 300, date(2025, 7, 1)),),
        )
        result = self.calculate(settings)
        item, = result.expenses
        self.assertEqual(result.receipts, ())
        self.assertEqual(item.contributions, ())
        self.assertEqual((item.income_funded, item.released_reserve), (0, 0))
        self.assertGreater(item.income_capacity, 0)
        self.assertEqual(item.full_reserve, result.rows[2].amount)


if __name__ == '__main__':
    unittest.main()
