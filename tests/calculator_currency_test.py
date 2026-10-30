"""Currency amounts are converted once to HUF before reserve and income modelling."""

from datetime import date
from decimal import Decimal
from math import inf, nan
import unittest

from portfolio.calculator import CalculatorSettings, CashFlow, ReturnAssumptions, huf_value, recommend
from portfolio.funding import prepare_allocation
from portfolio.models import AllocationTargets, CASH, STOCK_MARKET, TREASURY, MarketData, Portfolio


def ongoing(name, amount, currency='HUF'):
    return CashFlow(name, amount, date.min, currency, 'monthly')


class CalculatorCurrencyTests(unittest.TestCase):
    today = date(2025, 1, 1)
    rates = {'EUR': 400, 'USD': 360, 'GBP': 480}
    allocation = AllocationTargets({STOCK_MARKET: {}, TREASURY: {'EUR': 20, 'HUF': 80}, CASH: {}})

    def settings(self, *, extra_outgoings=(), **values):
        defaults = dict(outgoings=(ongoing('Rent', 1_200, 'EUR'), ongoing('Other', 800, 'EUR')),
                        incomes=(ongoing('Salary', 3_000, 'USD'),),
                        returns=ReturnAssumptions(equities=0, mak_huf=0))
        values = defaults | values
        values['outgoings'] = tuple(values['outgoings']) + tuple(extra_outgoings)
        return CalculatorSettings(**values)

    def test_non_huf_inputs_accept_fractions_and_huf_inputs_keep_exact_integer_validation(self):
        settings = self.settings(outgoings=(ongoing(' Rent ', Decimal('2000.125'), 'eur'),),
                                 incomes=(ongoing('Salary', Decimal('3000.75'), 'usd'),))
        self.assertEqual((settings.outgoings[0].name, settings.outgoings[0].amount, settings.outgoings[0].currency),
                         ('Rent', 2000.125, 'EUR'))
        self.assertEqual((settings.incomes[0].amount, settings.incomes[0].currency), (3000.75, 'USD'))
        expense = CashFlow('GBP expense', 10.25, self.today, 'gbp')
        self.assertEqual((expense.amount, expense.currency), (10.25, 'GBP'))
        for currency in ('HUF', 'huf'):
            with self.subTest(currency=currency), self.assertRaisesRegex(ValueError, 'whole number of HUF'):
                CashFlow('Fraction', Decimal('1.000000000000001'), self.today, currency)
        with self.assertRaisesRegex(ValueError, 'whole number of HUF'):
            ongoing('Rent', Decimal('1.000000000000001'), 'HUF')
        with self.assertRaisesRegex(ValueError, 'whole number of HUF'):
            self.settings(incomes=(ongoing('Salary', Decimal('1.000000000000001')),))
        for currency in ('EU', 'EURO', '€UR', ' EUR', None):
            with self.subTest(currency=currency), self.assertRaises(ValueError):
                ongoing('Rent', 1, currency)

    def test_itemized_costs_sum_each_currency_before_funding_both_buffers_and_income(self):
        settings = self.settings(outgoings=(ongoing('Rent', 1_000, 'EUR'),
                                                ongoing('Food', 100_000),
                                                ongoing('Utilities', 100, 'USD')),
                                 incomes=(ongoing('Salary', 2_000, 'GBP'),))
        self.assertEqual(settings.monthly_spending(self.rates), 536_000)
        self.assertEqual(settings.monthly_totals(2025, 1, self.rates).net, 424_000)
        self.assertEqual(settings.currencies, {'HUF', 'EUR', 'USD', 'GBP'})
        result = recommend(settings, self.today, self.allocation, rates=self.rates, total=10_000_000)
        self.assertEqual(result.rows[0].mak, 6 * 536_000)
        self.assertEqual(result.rows[1].cash, 2 * 536_000)
        self.assertEqual(result.rows[-1].amount, 10_000_000 - 8 * 536_000)
        self.assertEqual(self.settings(outgoings=[]).outgoings, ())
        overflow = self.settings(outgoings=(ongoing('First', 1e308), ongoing('Second', 1e308)))
        with self.assertRaisesRegex(ValueError, 'numeric range'):
            overflow.monthly_spending()

    def test_income_subtracts_spending_after_conversion_and_payments_are_shared_once(self):
        settings = self.settings(extra_outgoings=(CashFlow('First', 1_000, date(2025, 4, 1), 'GBP'),
                                           CashFlow('Second', 500, date(2025, 5, 1), 'GBP'),
                                           CashFlow('Third', 100, date(2025, 6, 1), 'GBP')))
        self.assertEqual(settings.monthly_totals(2025, 1, self.rates).net, 280_000)
        targets = AllocationTargets({STOCK_MARKET: {'World': 60}, TREASURY: {'HUF': 40}, CASH: {}},
                                     {'World': 'Equities'})
        result = recommend(settings, self.today, targets, rates=self.rates, total=10_000_000)
        self.assertEqual((result.rows[1].cash, result.rows[0].mak), (1_600_000, 4_800_000))
        self.assertEqual([row.future_amount for row in result.rows[2:-1]], [480_000, 240_000, 48_000])
        first, second, third = result.rows[2:-1]
        self.assertEqual((first.income_funded, first.amount), (280_000, 200_000))
        for row in (second, third):
            self.assertAlmostEqual(row.income_funded, row.future_amount - row.stock)
            self.assertAlmostEqual(row.amount, row.stock)
            self.assertGreater(row.stock, 0)
        self.assertEqual(result.total, 10_000_000)
        # A larger numeric salary can still leave no savings when its currency is worth less.
        self.assertEqual(self.settings(outgoings=(ongoing('Rent', 3_000, 'EUR'),), incomes=(ongoing('Salary', 3_100, 'USD'),)).monthly_totals(2025, 1, self.rates).net, -84_000)

    def test_converted_fractional_huf_values_stay_precise_without_becoming_huf_inputs(self):
        rates = {'EUR': 399.5, 'GBP': 471.25}
        settings = self.settings(incomes=(), outgoings=(ongoing('Rent', 100.01, 'EUR'),),
                                 extra_outgoings=(CashFlow('Cost today', 10.25, self.today, 'GBP'),))
        result = recommend(settings, self.today, self.allocation, rates=rates, total=10_000_000)
        spending = 100.01 * 399.5
        expense = 10.25 * 471.25
        self.assertAlmostEqual(result.rows[1].cash, 2 * spending)
        self.assertAlmostEqual(result.rows[0].mak, 6 * spending)
        self.assertAlmostEqual(result.rows[2].future_amount, expense)
        self.assertAlmostEqual(result.rows[2].amount, expense)
        living = [item for item in result.expenses if item.payment.origin == 'living']
        self.assertEqual(len(living), 12)
        self.assertTrue(all(item.payment.currency == 'HUF' for item in living))
        self.assertAlmostEqual(sum(item.payment.amount for item in living), 12 * spending)
        self.assertAlmostEqual(result.rows[-1].amount, 10_000_000 - spending * 20 - expense)
        self.assertAlmostEqual(result.total, 10_000_000)

    def test_fx_changes_recalculate_inputs_without_changing_nominal_native_amounts(self):
        settings = self.settings(incomes=(), extra_outgoings=(CashFlow('Cost', 100, self.today, 'GBP'),))
        original = recommend(settings, self.today, self.allocation, rates=self.rates, total=10_000_000)
        changed = recommend(settings, self.today, self.allocation, rates={**self.rates, 'EUR': 450, 'GBP': 500}, total=10_000_000)
        self.assertEqual((original.rows[0].amount, changed.rows[0].amount), (4_800_000, 5_400_000))
        self.assertEqual((original.rows[2].amount, changed.rows[2].amount), (48_000, 50_000))
        self.assertEqual(settings.outgoings[:2], (ongoing('Rent', 1_200, 'EUR'), ongoing('Other', 800, 'EUR')))
        self.assertEqual(settings.scheduled_payments(self.today, self.rates)[0].amount, 100)
        self.assertEqual(changed.total, original.total)

    def test_funding_uses_cross_rates_in_any_base_and_keeps_all_protected_amounts_huf(self):
        settings = self.settings(incomes=(),
                                 extra_outgoings=(CashFlow('Now', 100, self.today, 'GBP'),))
        portfolio = Portfolio((), self.allocation, {'HUF': 10_000_000})
        huf = prepare_allocation(portfolio, MarketData({}, self.rates, 'HUF'), settings, self.today)
        eur_rates = {currency: rate / 400 for currency, rate in {'HUF': 1, **self.rates}.items()}
        eur = prepare_allocation(portfolio, MarketData({}, eur_rates, 'EUR'), settings, self.today)
        self.assertEqual(huf.recommendation, eur.recommendation)
        self.assertEqual(huf.portfolio.allocation, eur.portfolio.allocation)
        # Living deficits add 9.6m HUF of required reserves. Only owned assets
        # can back the protected allocation after funding cash and emergency.
        self.assertEqual(huf.recommendation.shortfall, Decimal('6048000'))
        self.assertEqual(dict(huf.minimum_values), {(CASH, 'HUF'): 1_648_000, (TREASURY, 'HUF'): 8_352_000})
        self.assertEqual(dict(eur.minimum_values), {(CASH, 'HUF'): 4_120, (TREASURY, 'HUF'): 20_880})

    def test_inflation_linked_foreign_living_deficits_include_every_income_free_month(self):
        flow = CashFlow('Rent', 100.01, self.today, 'EUR', 'monthly', growth_mode='inflation')
        settings = self.settings(incomes=(), outgoings=(flow,), inflation_percent=12)
        rates = {'EUR': 399.5}
        budgets = settings.income_budgets(self.today, date(2026, 1, 1), rates)
        self.assertEqual(len(budgets), 12)
        for month, budget in enumerate(budgets, 1):
            expected = 100.01 * 1.12 ** (month / 12) * 399.5
            self.assertAlmostEqual(budget.living_deficit, expected)
            self.assertAlmostEqual(budget.net, -expected)
            self.assertEqual(budget.surplus, 0)
        reserved = settings.scheduled_payments(self.today, rates)
        self.assertEqual([payment.due for payment in reserved], [budget.day for budget in budgets])
        for payment, budget in zip(reserved, budgets):
            self.assertEqual(payment.amount, budget.living_deficit)
            self.assertEqual(payment.currency, 'HUF')

    def test_only_nonzero_inputs_need_rates_and_invalid_missing_rates_are_explicit(self):
        settings = self.settings(outgoings=(), incomes=(),
                                 extra_outgoings=(CashFlow('Cost', 10, self.today, 'GBP'),))
        self.assertEqual(settings.currencies, {'HUF', 'GBP'})
        self.assertEqual(settings.monthly_spending(), 0)
        self.assertEqual(huf_value(0, 'EUR'), 0)
        self.assertEqual(huf_value(12.5, 'HUF'), 12.5)
        with self.assertRaisesRegex(ValueError, 'GBP/HUF'):
            recommend(settings, self.today, self.allocation, total=10_000_000)
        for rates in ({}, {'EUR': 0}, {'EUR': -1}, {'EUR': True}, {'EUR': inf}, {'EUR': nan}, {'EUR': 400, 'HUF': 400}, []):
            with self.subTest(rates=rates), self.assertRaises(ValueError):
                huf_value(1, 'EUR', rates)
        with self.assertRaisesRegex(ValueError, 'must be 1'):
            huf_value(1, 'HUF', {'HUF': 400})
        with self.assertRaisesRegex(ValueError, 'finite HUF'):
            huf_value(1e308, 'EUR', {'EUR': 400})
        self.assertEqual(huf_value(10, 'EUR', {'EUR': Decimal('400.25')}), 4002.5)


if __name__ == '__main__':
    unittest.main()
