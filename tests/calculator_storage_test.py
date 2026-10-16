"""Independent calculator JSON persists atomically without touching portfolio CSVs."""

from dataclasses import replace
from datetime import date
import json
from math import nan
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from portfolio.calculator import CalculatorSettings, Expense, MonthlyCost
from portfolio.storage import load_settings, save_settings


class CalculatorStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.path = self.directory / 'calculator.json'
        self.settings = CalculatorSettings(monthly_costs=(MonthlyCost('Living costs', 100_000),), monthly_income=160_000,
                                           expenses=(Expense('Lakás', 500_000, date(2028, 2, 29)),))

    def test_missing_defaults_and_round_trip_touch_only_calculator_inputs(self):
        self.assertEqual(load_settings(self.directory), (CalculatorSettings(), None))
        untouched = {'asset.csv': b'portfolio inputs', 'cash.csv': b'cash inputs'}
        for name, contents in untouched.items():
            (self.directory / name).write_bytes(contents)
        token = save_settings(self.settings, self.directory, None)
        self.assertEqual(load_settings(self.directory), (self.settings, token))
        self.assertEqual(self.path.read_bytes(), token)
        self.assertEqual(json.loads(token)['expenses'][0]['due'], '2028-02-29')
        self.assertIsNone(json.loads(token)['returns']['mak'])
        self.assertNotIn('total', json.loads(token))
        self.assertEqual(json.loads(token)['monthly_costs'], [{'name': 'Living costs', 'amount': 100_000, 'currency': 'HUF'}])
        self.assertEqual(json.loads(token)['monthly_income'], 160_000)
        self.assertEqual(json.loads(token)['cash_months'], 2)
        self.assertEqual({path.name for path in self.directory.iterdir()}, {*untouched, 'calculator.json'})
        for name, contents in untouched.items():
            self.assertEqual((self.directory / name).read_bytes(), contents)
        new = replace(self.settings, cash_months=4, returns=replace(self.settings.returns, equities=4))
        self.assertEqual(load_settings(self.directory)[0], self.settings)
        updated = save_settings(new, self.directory, token)
        self.assertEqual(load_settings(self.directory), (new, updated))

    def test_native_currencies_and_fractional_foreign_amounts_round_trip(self):
        settings = replace(self.settings, monthly_costs=(MonthlyCost('Rent', 1_000.25, 'EUR'), MonthlyCost('Groceries', 12_345)),
                           monthly_income=2_000.75, income_currency='GBP',
                           expenses=(Expense('Travel', 99.95, date(2028, 2, 29), 'USD'),))
        token = save_settings(settings, self.directory, None)
        loaded, original = load_settings(self.directory)
        self.assertEqual((loaded, original), (settings, token))
        self.assertEqual(loaded.currencies, {'HUF', 'EUR', 'GBP', 'USD'})
        self.assertEqual(loaded.expenses[0].currency, 'USD')
        self.assertEqual(json.loads(token)['monthly_costs'][0]['currency'], 'EUR')
        self.assertEqual(json.loads(token)['income_currency'], 'GBP')
        self.assertEqual(json.loads(token)['expenses'][0]['currency'], 'USD')

    def test_external_edit_creation_deletion_and_edit_during_save_are_guarded(self):
        token = save_settings(self.settings, self.directory, None)
        for original, contents in ((None, token), (token, b'externally changed'), (token, None)):
            if contents is None:
                self.path.unlink()
            else:
                self.path.write_bytes(contents)
            with self.subTest(contents=contents), self.assertRaisesRegex(ValueError, 'changed outside'):
                save_settings(self.settings, self.directory, original)
            self.assertEqual(self.path.read_bytes() if self.path.exists() else None, contents)
        token = save_settings(self.settings, self.directory, None)
        with patch('portfolio.storage._read', side_effect=[token, b'changed while staging']):
            with self.assertRaisesRegex(ValueError, 'changed outside'):
                save_settings(self.settings, self.directory, token)
        self.assertEqual(self.path.read_bytes(), token)
        self.assertEqual(list(self.directory.glob('*.tmp')), [])

    def test_json_rejects_huf_fractions_before_float_rounding(self):
        token = save_settings(self.settings, self.directory, None)
        for field, whole in (('amount', 100_000),
                              ('monthly_income', 160_000), ('amount', 500_000)):
            for literal in (f'{whole}.000000000000001', f'{whole - 1}.999999999999999'):
                contents = token.replace(f'"{field}": {whole}.0'.encode(),
                                         f'"{field}": {literal}'.encode())
                self.assertNotEqual(contents, token)
                self.path.write_bytes(contents)
                with self.subTest(field=field, literal=literal), self.assertRaisesRegex(ValueError, 'whole number of HUF'):
                    load_settings(self.directory)
                self.assertEqual(self.path.read_bytes(), contents)
        integral = token.replace(b'100000.0', b'100000.00').replace(b'"equities": 6.0', b'"equities": 6.125')
        self.path.write_bytes(integral)
        loaded, _ = load_settings(self.directory)
        self.assertEqual(loaded.monthly_spending(), 100_000)
        self.assertEqual(loaded.returns.equities, 6.125)

    def test_failed_atomic_replacement_keeps_original_and_removes_temporary_file(self):
        token = save_settings(self.settings, self.directory, None)
        with patch('portfolio.storage.os.replace', side_effect=OSError('disk error')):
            with self.assertRaisesRegex(OSError, 'disk error'):
                save_settings(replace(self.settings, monthly_costs=(MonthlyCost('Living costs', 200_000),)), self.directory, token)
        self.assertEqual(self.path.read_bytes(), token)
        self.assertEqual({path.name for path in self.directory.iterdir()}, {'calculator.json'})

    def test_invalid_files_remain_unchanged_and_are_not_silently_migrated(self):
        token = save_settings(self.settings, self.directory, None)
        valid = json.loads(token)
        cases = [b'not json', b'\xff', b'[]', b'{}', b'{"monthly_income":1,"monthly_income":2}',
                 json.dumps({**valid, 'monthly_income': True}).encode(),
                 json.dumps({**valid, 'monthly_income': nan}).encode(),
                 json.dumps({**valid, 'unknown': 100}).encode(),
                 json.dumps({**valid, 'monthly_costs': [{'name': 'Rent', 'amount': 1.001, 'currency': 'HUF'}]}).encode(),
                 json.dumps({**valid, 'monthly_costs': {}}).encode(),
                 json.dumps({**valid, 'monthly_costs': [{'name': 'Rent', 'amount': 0, 'currency': 'HUF'}]}).encode(),
                 json.dumps({**valid, 'monthly_costs': [{'name': 'Rent', 'amount': 1, 'currency': 'HUF', 'extra': 1}]}).encode(),
                 json.dumps({**valid, 'monthly_income': 1.001}).encode(),
                 json.dumps({**valid, 'monthly_income': True}).encode(),
                 json.dumps({**valid, 'expenses': [{'name': 'A', 'amount': 1.001, 'due': '2028-02-29', 'currency': 'HUF'}]}).encode(),
                 json.dumps({**valid, 'legacy': 1}).encode(),
                 json.dumps({**valid, 'returns': {'mak': 6}}).encode(),
                 json.dumps({**valid, 'returns': {**valid['returns'], 'mak': True}}).encode(),
                 json.dumps({**valid, 'expenses': {}}).encode(),
                 json.dumps({**valid, 'expenses': [{'name': 'A', 'amount': 1, 'due': '20280229', 'currency': 'HUF'}]}).encode(),
                 json.dumps({**valid, 'expenses': [{'name': 'A', 'amount': 1, 'due': '2028-02-30', 'currency': 'HUF'}]}).encode()]
        for contents in cases:
            self.path.write_bytes(contents)
            with self.subTest(contents=contents), self.assertRaisesRegex(ValueError, 'Invalid calculator.json'):
                load_settings(self.directory)
            self.assertEqual(self.path.read_bytes(), contents)


if __name__ == '__main__':
    unittest.main()
