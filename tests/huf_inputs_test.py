"""Whole-HUF rules apply to entered balances, not internal trade calculations."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from portfolio.models import (AllocationTargets, CASH, STOCK_MARKET, TREASURY,
                              ManualHolding, MarketData, Portfolio, current_values,
                              parse_currency_amount, parse_manual_amount)
from portfolio.storage import load_portfolio, save_portfolio


class HufInputTests(unittest.TestCase):
    def test_cash_accepts_whole_huf_and_fractional_foreign_currencies(self):
        for text, expected in (('0', 0), ('100', 100), ('100.00', 100), ('-20', -20)):
            with self.subTest(text=text):
                self.assertEqual(parse_currency_amount(text, 'huf'), expected)
        self.assertEqual(parse_currency_amount('100.25', 'EUR'), 100.25)
        self.assertEqual(parse_currency_amount('-100.125', 'EUR'), -100.125)

    def test_huf_rejects_fractions_before_float_conversion_erases_them(self):
        for text in ('0.01', '1.001', '-1.25', '9007199254740992.1'):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, 'HUF amounts must be whole'):
                parse_currency_amount(text, 'HUF')
        for text in ('100.25', '1.001', '90071992547400.000001'):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, 'HUF amounts must be whole'):
                parse_manual_amount(text, 'HUF')

    def test_invalid_currency_amounts_are_rejected(self):
        for text in ('', 'money', 'NaN', 'Infinity', '-Infinity', '1e1000'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_currency_amount(text, 'HUF')
        with self.assertRaises(ValueError):
            parse_currency_amount('100', 'not a currency')
        with self.assertRaisesRegex(ValueError, 'two decimal places'):
            parse_manual_amount('100.001', 'EUR')
        self.assertEqual(parse_manual_amount('100.25', 'EUR'), 100.25)
        self.assertEqual(parse_manual_amount('100.00', 'HUF'), 100)

    def test_internal_huf_amounts_keep_hundredths_for_valuation_and_trades(self):
        self.assertEqual(parse_manual_amount('100.25'), 100.25)
        holding = ManualHolding(100.25, 'HUF')
        self.assertEqual(holding.cents, 10025)
        portfolio = Portfolio((), AllocationTargets(
            {STOCK_MARKET: {}, TREASURY: {'HUF': 100}, CASH: {}}),
            {'HUF': -0.25}, (holding,))
        market = MarketData({}, {'HUF': 1}, 'HUF')
        values = current_values(portfolio.holdings, portfolio.manual_holdings, portfolio.cash, market)
        self.assertEqual(sum(values.values()), 100)


class HufStorageInputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        portfolio = Portfolio((), AllocationTargets(
            {STOCK_MARKET: {}, TREASURY: {'EUR': 50, 'HUF': 50}, CASH: {}}),
            {'EUR': 10.25, 'HUF': -20},
            (ManualHolding(100.25, 'EUR'), ManualHolding(300, 'HUF')))
        save_portfolio(portfolio, self.directory)

    def test_loading_rejects_fractional_huf_cash_and_manual_rows_without_writes(self):
        for filename, text in (('cash.csv', '0.25'), ('cash.csv', '-1.5'),
                               ('cash.csv', '9007199254740992.1'),
                               ('manual_asset.csv', '100.25'),
                               ('manual_asset.csv', '90071992547400.000001')):
            path = self.directory / filename
            original = path.read_bytes()
            path.write_text(f'currency,amount\nHUF,{text}\n', encoding='utf-8')
            before = {file.name: file.read_bytes() for file in self.directory.iterdir()}
            with self.subTest(filename=filename, text=text), self.assertRaisesRegex(
                    ValueError, 'HUF amounts must be whole'):
                load_portfolio(self.directory)
            self.assertEqual({file.name: file.read_bytes() for file in self.directory.iterdir()}, before)
            path.write_bytes(original)

    def test_loading_preserves_eur_fractions_and_accepts_integral_huf_decimal_notation(self):
        (self.directory / 'cash.csv').write_text('currency,amount\neur,-10.25\nhuf,20.00\n')
        (self.directory / 'manual_asset.csv').write_text('currency,amount\neur,100.25\nhuf,300.00\n')
        portfolio = load_portfolio(self.directory)
        self.assertEqual(portfolio.cash, {'EUR': -10.25, 'HUF': 20})
        self.assertEqual(portfolio.manual_holdings, (ManualHolding(100.25, 'EUR'), ManualHolding(300, 'HUF')))

    def test_saving_rejects_fractional_huf_without_replacing_or_creating_inputs(self):
        portfolio = load_portfolio(self.directory)
        before = {path.name: path.read_bytes() for path in self.directory.iterdir()}
        invalid = (
            replace(portfolio, cash={**portfolio.cash, 'HUF': 0.25}),
            replace(portfolio, cash={**portfolio.cash, 'HUF': -1.5}),
            replace(portfolio, manual_holdings=(ManualHolding(100.25, 'EUR'), ManualHolding(300.25, 'HUF'))),
        )
        absent = self.directory / 'new-portfolio'
        for candidate in invalid:
            for directory in (self.directory, absent):
                with self.subTest(portfolio=candidate, directory=directory), self.assertRaisesRegex(
                        ValueError, 'HUF amounts must be whole'):
                    save_portfolio(candidate, directory)
                self.assertFalse(absent.exists())
                self.assertEqual({path.name: path.read_bytes() for path in self.directory.iterdir()}, before)

    def test_saving_preserves_eur_fractions_and_whole_huf(self):
        portfolio = load_portfolio(self.directory)
        changed = replace(portfolio, cash={'EUR': -10.125, 'HUF': 200},
                          manual_holdings=(ManualHolding(123.45, 'EUR'), ManualHolding(400, 'HUF')))
        save_portfolio(changed, self.directory)
        self.assertEqual(load_portfolio(self.directory), changed)


if __name__ == '__main__':
    unittest.main()
