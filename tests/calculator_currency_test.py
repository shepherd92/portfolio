"""Currency amounts are converted once to HUF before reserve and income modelling."""

from datetime import date
from decimal import Decimal
from math import inf, nan
import unittest

from portfolio.calculator import CalculatorSettings, Expense, MonthlyCost, ReturnAssumptions, huf_value, recommend
from portfolio.funding import prepare_allocation
from portfolio.models import AllocationTargets, CASH, STOCK_MARKET, TREASURY, MarketData, Portfolio


class CalculatorCurrencyTests(unittest.TestCase):
    today = date(2025, 1, 1)
    rates = {'EUR': 400, 'USD': 360, 'GBP': 480}
    allocation = AllocationTargets({STOCK_MARKET: {}, TREASURY: {'EUR': 20, 'HUF': 80}, CASH: {}})

    def settings(self, **values):
        defaults = dict(monthly_costs=(MonthlyCost('Rent', 1_200, 'EUR'), MonthlyCost('Other', 800, 'EUR')),
                        monthly_income=3_000, income_currency='USD',
                        returns=ReturnAssumptions(equities=0, mak=0))
        return CalculatorSettings(**(defaults | values))

    def test_non_huf_inputs_accept_fractions_and_huf_inputs_keep_exact_integer_validation(self):
        settings = self.settings(monthly_costs=(MonthlyCost(' Rent ', Decimal('2000.125'), 'eur'),),
                                 monthly_income=Decimal('3000.75'), income_currency='usd')
        self.assertEqual((settings.monthly_costs[0].name, settings.monthly_costs[0].amount, settings.monthly_costs[0].currency),
                         ('Rent', 2000.125, 'EUR'))
        self.assertEqual((settings.monthly_income, settings.income_currency), (3000.75, 'USD'))
        expense = Expense('GBP expense', 10.25, self.today, 'gbp')
        self.assertEqual((expense.amount, expense.currency), (10.25, 'GBP'))
        for currency in ('HUF', 'huf'):
            with self.subTest(currency=currency), self.assertRaisesRegex(ValueError, 'whole number of HUF'):
                Expense('Fraction', Decimal('1.000000000000001'), self.today, currency)
        with self.assertRaisesRegex(ValueError, 'whole number of HUF'):
            MonthlyCost('Rent', Decimal('1.000000000000001'), 'HUF')
        with self.assertRaisesRegex(ValueError, 'whole number of HUF'):
            self.settings(monthly_income=Decimal('1.000000000000001'), income_currency='HUF')
        for currency in ('EU', 'EURO', '€UR', ' EUR', None):
            with self.subTest(currency=currency), self.assertRaises(ValueError):
                MonthlyCost('Rent', 1, currency)

    def test_itemized_costs_sum_each_currency_before_funding_both_buffers_and_income(self):
        settings = self.settings(monthly_costs=(MonthlyCost('Rent', 1_000, 'EUR'),
                                                MonthlyCost('Food', 100_000),
                                                MonthlyCost('Utilities', 100, 'USD')),
                                 monthly_income=2_000, income_currency='GBP')
        self.assertEqual(settings.monthly_spending(self.rates), 536_000)
        self.assertEqual(settings.monthly_savings(self.rates), 424_000)
        self.assertEqual(settings.currencies, {'HUF', 'EUR', 'USD', 'GBP'})
        result = recommend(settings, self.today, self.allocation, rates=self.rates, total=10_000_000)
        self.assertEqual(result.rows[0].mak, 6 * 536_000)
        self.assertEqual(result.rows[1].cash, 2 * 536_000)
        self.assertEqual(result.rows[-1].amount, 10_000_000 - 8 * 536_000)
        self.assertEqual(self.settings(monthly_costs=[]).monthly_costs, ())
        overflow = self.settings(monthly_costs=(MonthlyCost('First', 1e308), MonthlyCost('Second', 1e308)))
        with self.assertRaisesRegex(ValueError, 'numeric range'):
            overflow.monthly_spending()

    def test_income_subtracts_spending_after_conversion_and_payments_are_shared_once(self):
        settings = self.settings(expenses=(Expense('First', 1_000, date(2025, 4, 1), 'GBP'),
                                           Expense('Second', 500, date(2025, 5, 1), 'GBP'),
                                           Expense('Third', 100, date(2025, 6, 1), 'GBP')))
        self.assertEqual(settings.monthly_savings(self.rates), 280_000)
        targets = AllocationTargets({STOCK_MARKET: {'World': 60}, TREASURY: {'HUF': 40}, CASH: {}},
                                     {'World': 'Equities'})
        result = recommend(settings, self.today, targets, rates=self.rates, total=10_000_000)
        self.assertEqual((result.rows[1].cash, result.rows[0].mak), (1_600_000, 4_800_000))
        self.assertEqual([row.future_amount for row in result.rows[2:-1]], [480_000, 240_000, 48_000])
        self.assertEqual([row.income_funded for row in result.rows[2:-1]], [280_000, 240_000, 48_000])
        self.assertEqual([row.amount for row in result.rows[2:-1]], [200_000, 0, 0])
        self.assertEqual(result.total, 10_000_000)
        # A larger numeric salary can still leave no savings when its currency is worth less.
        self.assertEqual(self.settings(monthly_costs=(MonthlyCost('Rent', 3_000, 'EUR'),), monthly_income=3_100).monthly_savings(self.rates), 0)

    def test_converted_fractional_huf_values_stay_precise_without_becoming_huf_inputs(self):
        rates = {'EUR': 399.5, 'GBP': 471.25}
        settings = self.settings(monthly_income=0, monthly_costs=(MonthlyCost('Rent', 100.01, 'EUR'),),
                                 expenses=(Expense('Cost today', 10.25, self.today, 'GBP'),))
        result = recommend(settings, self.today, self.allocation, rates=rates, total=10_000_000)
        spending = 100.01 * 399.5
        expense = 10.25 * 471.25
        self.assertAlmostEqual(result.rows[1].cash, 2 * spending)
        self.assertAlmostEqual(result.rows[0].mak, 6 * spending)
        self.assertAlmostEqual(result.rows[2].future_amount, expense)
        self.assertAlmostEqual(result.rows[2].amount, expense)
        self.assertAlmostEqual(result.rows[-1].amount, 10_000_000 - spending * 8 - expense)
        self.assertAlmostEqual(result.total, 10_000_000)

    def test_fx_changes_recalculate_inputs_without_changing_nominal_native_amounts(self):
        settings = self.settings(monthly_income=0, expenses=(Expense('Cost', 100, self.today, 'GBP'),))
        original = recommend(settings, self.today, self.allocation, rates=self.rates, total=10_000_000)
        changed = recommend(settings, self.today, self.allocation, rates={**self.rates, 'EUR': 450, 'GBP': 500}, total=10_000_000)
        self.assertEqual((original.rows[0].amount, changed.rows[0].amount), (4_800_000, 5_400_000))
        self.assertEqual((original.rows[2].amount, changed.rows[2].amount), (48_000, 50_000))
        self.assertEqual(settings.monthly_costs, (MonthlyCost('Rent', 1_200, 'EUR'), MonthlyCost('Other', 800, 'EUR')))
        self.assertEqual(settings.expenses[0].amount, 100)
        self.assertEqual(changed.total, original.total)

    def test_funding_uses_cross_rates_in_any_base_and_keeps_all_protected_amounts_huf(self):
        settings = self.settings(monthly_income=0,
                                 expenses=(Expense('Now', 100, self.today, 'GBP'),))
        portfolio = Portfolio((), self.allocation, {'HUF': 10_000_000})
        huf = prepare_allocation(portfolio, MarketData({}, self.rates, 'HUF'), settings, self.today)
        eur_rates = {currency: rate / 400 for currency, rate in {'HUF': 1, **self.rates}.items()}
        eur = prepare_allocation(portfolio, MarketData({}, eur_rates, 'EUR'), settings, self.today)
        self.assertEqual(huf.recommendation, eur.recommendation)
        self.assertEqual(huf.portfolio.allocation, eur.portfolio.allocation)
        self.assertEqual(dict(huf.minimum_values), {(CASH, 'HUF'): 1_648_000, (TREASURY, 'HUF'): 4_800_000})
        self.assertEqual(dict(eur.minimum_values), {(CASH, 'HUF'): 4_120, (TREASURY, 'HUF'): 12_000})

    def test_only_nonzero_inputs_need_rates_and_invalid_missing_rates_are_explicit(self):
        settings = self.settings(monthly_costs=(), monthly_income=0,
                                 expenses=(Expense('Cost', 10, self.today, 'GBP'),))
        self.assertEqual(settings.currencies, {'HUF', 'GBP'})
        self.assertEqual(settings.monthly_savings(), 0)
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
