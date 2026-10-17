"""Unified income and outgoing schedules persist atomically without touching portfolio CSVs."""

from dataclasses import replace
from datetime import date
import json
from math import nan
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from portfolio.calculator import CalculatorSettings, CashFlow
from portfolio.linked import LinkedSettings
from portfolio.storage import load_settings, save_settings


class CalculatorStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.path = self.directory / 'calculator.json'
        self.settings = CalculatorSettings(outgoings=(
            CashFlow('Living costs', 100_000, date(2026, 1, 31), frequency='monthly'),
            CashFlow('Lakás', 500_000, date(2028, 2, 29))),
            incomes=(CashFlow('Salary', 160_000, date(2026, 2, 28), frequency='monthly'),))

    def test_missing_defaults_and_round_trip_touch_only_calculator_inputs(self):
        self.assertEqual(load_settings(self.directory), (CalculatorSettings(), None))
        untouched = {'asset.csv': b'portfolio inputs', 'cash.csv': b'cash inputs'}
        for name, contents in untouched.items():
            (self.directory / name).write_bytes(contents)
        token = save_settings(self.settings, self.directory, None)
        self.assertEqual(load_settings(self.directory), (self.settings, token))
        self.assertEqual(self.path.read_bytes(), token)
        values = json.loads(token)
        self.assertEqual(values['outgoings'][1]['start'], '2028-02')
        self.assertEqual(values['outgoings'][0], {
            'name': 'Living costs', 'amount': 100_000, 'currency': 'HUF',
            'start': '2026-01', 'frequency': 'monthly', 'count': None, 'end': None,
            'appreciation_percent': 0})
        self.assertIsNone(values['returns']['mak_huf'])
        self.assertNotIn('total', values)
        self.assertNotIn('monthly_costs', values)
        self.assertNotIn('expenses', values)
        self.assertEqual(values['incomes'][0]['amount'], 160_000)
        self.assertNotIn('monthly_income', values)
        self.assertNotIn('income_currency', values)
        self.assertEqual(values['cash_months'], 2)
        self.assertEqual({path.name for path in self.directory.iterdir()}, {*untouched, 'calculator.json'})
        for name, contents in untouched.items():
            self.assertEqual((self.directory / name).read_bytes(), contents)
        new = replace(self.settings, cash_months=4, returns=replace(self.settings.returns, equities=4))
        self.assertEqual(load_settings(self.directory)[0], self.settings)
        updated = save_settings(new, self.directory, token)
        self.assertEqual(load_settings(self.directory), (new, updated))

    def test_native_currencies_and_all_schedule_limits_round_trip(self):
        settings = replace(self.settings, incomes=(
            CashFlow('Salary', 2_000.75, date(2026, 2, 28), 'GBP', 'monthly'),
            CashFlow('Bonus', 100, date(2026, 12, 1), 'USD'),
            CashFlow('Rent income', 200, date(2026, 3, 1), 'EUR', 'monthly', count=24),
            CashFlow('Contract', 300, date(2026, 3, 1), 'HUF', 'annual', end=date(2028, 2, 29))), outgoings=(
            CashFlow('Rent', 1_000.25, date(2026, 1, 31), 'EUR', 'monthly'),
            CashFlow('Loan', 12_345, date(2026, 2, 1), frequency='monthly', count=24),
            CashFlow('Tuition', 99.95, date(2026, 2, 28), 'USD', 'monthly', end=date(2028, 2, 29)),
            CashFlow('Travel', 99.95, date(2028, 2, 29), 'USD')))
        token = save_settings(settings, self.directory, None)
        loaded, original = load_settings(self.directory)
        self.assertEqual((loaded, original), (settings, token))
        self.assertEqual(loaded.currencies, {'HUF', 'EUR', 'GBP', 'USD'})
        self.assertEqual([row['count'] for row in json.loads(token)['outgoings']], [None, 24, None, None])
        self.assertEqual(json.loads(token)['outgoings'][2]['end'], '2028-02')
        self.assertEqual(json.loads(token)['incomes'][0]['currency'], 'GBP')
        self.assertEqual([row['frequency'] for row in json.loads(token)['incomes']],
                         ['monthly', 'once', 'monthly', 'annual'])

    def test_appreciation_round_trip_keeps_base_amounts_and_derived_huf_precision(self):
        settings = CalculatorSettings(
            incomes=(CashFlow('Salary', 1001, date(2026, 1, 1), frequency='monthly', appreciation_percent=3.125),),
            outgoings=(CashFlow('Annual cost', 1200, date(2026, 1, 1), frequency='annual', count=3,
                                appreciation_percent=-2.75),
                       CashFlow('Purchase', 5000, date(2028, 1, 1), appreciation_percent=7.5)))
        token = save_settings(settings, self.directory, None)
        loaded, original = load_settings(self.directory)
        self.assertEqual((loaded, original), (settings, token))
        self.assertEqual(loaded.incomes[0].amount, 1001)
        self.assertEqual(loaded.outgoings[0].amount, 1200)
        self.assertAlmostEqual(loaded.incomes[0].amount_on(date(2027, 1, 1)), 1001 * 1.03125)
        self.assertNotEqual(loaded.incomes[0].amount_on(date(2026, 2, 1)),
                            round(loaded.incomes[0].amount_on(date(2026, 2, 1))))
        self.assertAlmostEqual(loaded.outgoings[0].amount_on(date(2028, 1, 1)), 1200 * .9725 ** 2)
        self.assertEqual(loaded.outgoings[1].amount_on(date(2030, 1, 1)), 5000)
        self.assertEqual(json.loads(token)['outgoings'][0]['appreciation_percent'], -2.75)
        self.assertEqual(self.path.read_bytes(), token)

    def test_missing_and_invalid_appreciation_are_rejected_without_rewriting_file(self):
        token = save_settings(self.settings, self.directory, None)
        for kind in ('incomes', 'outgoings'):
            for value in (None, True, -100, -101, 1000.01, 'unknown', float('nan'), float('inf')):
                data = json.loads(token)
                data[kind][0]['appreciation_percent'] = value
                contents = json.dumps(data).encode()
                self.path.write_bytes(contents)
                with self.subTest(kind=kind, value=value), self.assertRaisesRegex(ValueError, 'Invalid calculator.json'):
                    load_settings(self.directory)
                self.assertEqual(self.path.read_bytes(), contents)
            data = json.loads(token)
            del data[kind][0]['appreciation_percent']
            contents = json.dumps(data).encode()
            self.path.write_bytes(contents)
            with self.subTest(kind=kind, missing=True), self.assertRaisesRegex(ValueError, 'Invalid calculator.json'):
                load_settings(self.directory)
            self.assertEqual(self.path.read_bytes(), contents)

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
        with patch('portfolio.storage.read_optional_bytes', side_effect=[token, b'changed while staging']):
            with self.assertRaisesRegex(ValueError, 'changed outside'):
                save_settings(self.settings, self.directory, token)
        self.assertEqual(self.path.read_bytes(), token)
        self.assertEqual(list(self.directory.glob('*.tmp')), [])

    def test_json_rejects_huf_fractions_before_float_rounding(self):
        token = save_settings(self.settings, self.directory, None)
        for field, whole in (('amount', 100_000), ('amount', 160_000), ('amount', 500_000)):
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
        self.assertEqual(loaded.outgoings[0].amount, 100_000)
        self.assertEqual(loaded.returns.equities, 6.125)

    def test_failed_atomic_replacement_keeps_original_and_removes_temporary_file(self):
        token = save_settings(self.settings, self.directory, None)
        with patch('portfolio.storage.os.replace', side_effect=OSError('disk error')):
            with self.assertRaisesRegex(OSError, 'disk error'):
                save_settings(replace(self.settings, outgoings=()), self.directory, token)
        self.assertEqual(self.path.read_bytes(), token)
        self.assertEqual({path.name for path in self.directory.iterdir()}, {'calculator.json'})

    def test_runtime_linked_settings_cannot_replace_saved_inputs(self):
        token = save_settings(self.settings, self.directory, None)
        linked = LinkedSettings(as_of=date(2026, 1, 1))
        with self.assertRaisesRegex(ValueError, 'Expected validated calculator settings'):
            save_settings(linked, self.directory, token)
        self.assertEqual(self.path.read_bytes(), token)
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    @unittest.skipIf(os.name == 'nt', 'POSIX permission bits')
    def test_replacement_preserves_existing_permissions(self):
        token = save_settings(self.settings, self.directory, None)
        self.path.chmod(0o640)
        save_settings(replace(self.settings, cash_months=3), self.directory, token)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)

    def test_failed_staging_keeps_original_and_cleans_temporary(self):
        token = save_settings(self.settings, self.directory, None)
        with patch('portfolio.files.os.fsync', side_effect=OSError('disk error')):
            with self.assertRaisesRegex(OSError, 'disk error'):
                save_settings(replace(self.settings, cash_months=3), self.directory, token)
        self.assertEqual(self.path.read_bytes(), token)
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_invalid_files_remain_unchanged_and_are_not_silently_migrated(self):
        token = save_settings(self.settings, self.directory, None)
        valid = json.loads(token)
        cases = [b'not json', b'\xff', b'[]', b'{}', b'{"incomes":[],"incomes":[]}',
                 json.dumps({**valid, 'unknown': 100}).encode(),
                 json.dumps({**valid, 'returns': {'mak_huf': 6}}).encode(),
                 json.dumps({**valid, 'returns': {**valid['returns'], 'mak_huf': True}}).encode(),
                 json.dumps({**valid, 'outgoings': {}}).encode(),
                 json.dumps({**valid, 'incomes': {}}).encode()]
        row = valid['outgoings'][0]
        for change in ({'amount': 1.001}, {'amount': 0}, {'amount': True}, {'amount': nan}, {'extra': 1}, {'frequency': 'weekly'},
                       {'start': '20260131'}, {'start': '2026-01-01'}, {'start': '2026-1'}, {'start': '2026-13'}, {'start': '2026-02-30'}, {'start': None},
                       {'end': '20280229'}, {'end': '2028-02-30'}, {'end': 1},
                       {'count': True}, {'count': 0}, {'count': 2.5},
                       {'count': 12, 'end': '2028-02'},
                       {'frequency': 'once', 'count': 1}, {'frequency': 'once', 'start': '2026-01-01'}, {'end': '2025-12-31'}):
            for name in ('incomes', 'outgoings'):
                cases.append(json.dumps({**valid, name: [{**row, **change}]}).encode())
        old = {key: value for key, value in valid.items() if key not in ('outgoings', 'incomes')}
        cases.append(json.dumps({**old, 'monthly_costs': [], 'expenses': [],
                                 'monthly_income': 160_000, 'income_currency': 'HUF'}).encode())
        for contents in cases:
            self.path.write_bytes(contents)
            with self.subTest(contents=contents), self.assertRaisesRegex(ValueError, 'Invalid calculator.json'):
                load_settings(self.directory)
            self.assertEqual(self.path.read_bytes(), contents)


if __name__ == '__main__':
    unittest.main()
