"""Strict, conflict-safe persistence without touching trading or calculator inputs."""

from dataclasses import replace
from datetime import date
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from portfolio.calculator import CashFlow
from portfolio.wealth import Loan, Pension, PensionContribution, Property, PropertyCashFlow, WealthSettings
from portfolio.wealth_storage import load_wealth, save_wealth


class WealthStorageTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.path = self.directory / 'wealth.json'
        flow = CashFlow('Annual tax', 1000, date(2027, 1, 1), frequency='annual', count=3)
        self.settings = WealthSettings(
            properties=(Property('home', 'Budapest apartment', 50000000,
                                 appreciation_percent=3, depreciation_percent=1,
                                 vacancy_percent=12.5,
                                 cashflows=(PropertyCashFlow(
                                     CashFlow('Rent', 100.25, date(2026, 11, 1), currency='EUR', frequency='monthly'),
                                     'income', 'rented', appreciation_percent=2.5),
                                     PropertyCashFlow(flow),
                                     PropertyCashFlow(CashFlow('Vacant upkeep', 200, date(2027, 1, 1)),
                                                      'outgoing', 'vacant', appreciation_percent=-1))),),
            pensions=(Pension('pension', 'Pension', balance=10000, annual_return=4,
                              access=date(2030, 1, 1),
                              contributions=(PensionContribution(flow, 'payroll'),),
                              withdrawals=(CashFlow('Retirement', 100, date(2030, 1, 1), frequency='monthly'),)),),
            loans=(Loan('debt', 'Mortgage', 1000000, flow, annual_interest=5, property_id='home'),))

    def test_missing_file_uses_empty_settings_and_no_save_token(self):
        self.assertEqual(load_wealth(self.directory), (WealthSettings(), None))
        self.assertFalse(self.path.exists())

    def test_round_trip_preserves_links_schedules_and_months(self):
        token = save_wealth(self.settings, self.directory, None)
        loaded, loaded_token = load_wealth(self.directory)
        self.assertEqual(loaded, self.settings)
        self.assertEqual(token, loaded_token)
        data = json.loads(token)
        self.assertEqual(data['version'], 2)
        self.assertEqual(data['pensions'][0]['access'], '2030-01')
        self.assertEqual(data['loans'][0]['payment']['start'], '2027-01')
        property_data = data['properties'][0]
        self.assertEqual(property_data['vacancy_percent'], 12.5)
        self.assertEqual(property_data['cashflows'][0]['direction'], 'income')
        self.assertEqual(property_data['cashflows'][0]['occupancy'], 'rented')
        self.assertEqual(property_data['cashflows'][0]['appreciation_percent'], 2.5)
        self.assertEqual(property_data['cashflows'][0]['flow']['appreciation_percent'], 0)
        self.assertEqual(property_data['cashflows'][0]['flow']['amount'], 100.25)
        self.assertEqual(property_data['cashflows'][1]['flow']['count'], 3)
        self.assertEqual(property_data['cashflows'][1]['occupancy'], 'always')
        self.assertEqual(property_data['cashflows'][2]['occupancy'], 'vacant')
        self.assertEqual(property_data['cashflows'][2]['appreciation_percent'], -1)
        self.assertEqual(data['loans'][0]['property_id'], 'home')

    def test_linked_appreciation_round_trip_preserves_each_single_growth_source(self):
        pension = self.settings.pensions[0]
        contribution = replace(pension.contributions[0], flow=replace(pension.contributions[0].flow,
                                                                     appreciation_percent=3.125))
        pension = replace(pension, contributions=(contribution,),
                          withdrawals=(replace(pension.withdrawals[0], appreciation_percent=-1.5),))
        loan = self.settings.loans[0]
        loan = replace(loan, payment=replace(loan.payment, appreciation_percent=-2.75))
        settings = replace(self.settings, pensions=(pension,), loans=(loan,))
        token = save_wealth(settings, self.directory, None)
        loaded, original = load_wealth(self.directory)
        self.assertEqual((loaded, original), (settings, token))
        self.assertEqual(loaded.properties, self.settings.properties)
        self.assertEqual(loaded.properties[0].cashflows[0].flow.appreciation_percent, 0)
        self.assertEqual(loaded.properties[0].cashflows[0].appreciation_percent, 2.5)
        self.assertAlmostEqual(loaded.pensions[0].contributions[0].flow.amount_on(date(2028, 1, 1)),
                               1000 * 1.03125)
        self.assertAlmostEqual(loaded.pensions[0].withdrawals[0].amount_on(date(2031, 1, 1)), 100 * .985)
        self.assertAlmostEqual(loaded.loans[0].payment.amount_on(date(2028, 1, 1)), 1000 * .9725)
        self.assertEqual(self.path.read_bytes(), token)

    def test_linked_flow_appreciation_is_required_and_invalid_values_remain_untouched(self):
        token = save_wealth(self.settings, self.directory, None)
        selectors = (
            lambda data: data['properties'][0]['cashflows'][0]['flow'],
            lambda data: data['pensions'][0]['contributions'][0]['flow'],
            lambda data: data['pensions'][0]['withdrawals'][0],
            lambda data: data['loans'][0]['payment'])
        for index, select in enumerate(selectors):
            for value in (None, True, -100, -101, 1000.01, 'unknown', float('nan'), float('inf')):
                data = json.loads(token)
                select(data)['appreciation_percent'] = value
                payload = json.dumps(data).encode()
                self.path.write_bytes(payload)
                with self.subTest(source=index, value=value), self.assertRaisesRegex(ValueError, 'Invalid wealth.json'):
                    load_wealth(self.directory)
                self.assertEqual(self.path.read_bytes(), payload)
            data = json.loads(token)
            del select(data)['appreciation_percent']
            payload = json.dumps(data).encode()
            self.path.write_bytes(payload)
            with self.subTest(source=index, missing=True), self.assertRaisesRegex(ValueError, 'Invalid wealth.json'):
                load_wealth(self.directory)
            self.assertEqual(self.path.read_bytes(), payload)

    def test_property_cannot_save_growth_twice_in_nested_and_wrapper_fields(self):
        token = save_wealth(self.settings, self.directory, None)
        data = json.loads(token)
        data['properties'][0]['cashflows'][0]['flow']['appreciation_percent'] = 2.5
        payload = json.dumps(data).encode()
        self.path.write_bytes(payload)
        with self.assertRaisesRegex(ValueError, 'Invalid wealth.json'):
            load_wealth(self.directory)
        self.assertEqual(self.path.read_bytes(), payload)

    def test_saving_other_assets_does_not_change_other_input_files(self):
        calculator = self.directory / 'calculator.json'
        calculator.write_bytes(b'untouched calculator')
        assets = self.directory / 'asset.csv'
        assets.write_bytes(b'untouched portfolio')
        save_wealth(self.settings, self.directory, None)
        self.assertEqual(calculator.read_bytes(), b'untouched calculator')
        self.assertEqual(assets.read_bytes(), b'untouched portfolio')

    def test_external_edit_prevents_overwrite(self):
        token = save_wealth(self.settings, self.directory, None)
        self.path.write_bytes(b'externally changed')
        with self.assertRaisesRegex(ValueError, 'changed outside'):
            save_wealth(self.settings, self.directory, token)
        self.assertEqual(self.path.read_bytes(), b'externally changed')

    def test_external_creation_prevents_overwrite(self):
        self.path.write_bytes(b'new file')
        with self.assertRaisesRegex(ValueError, 'changed outside'):
            save_wealth(self.settings, self.directory, None)

    def test_failed_atomic_replace_keeps_original_and_cleans_temporary(self):
        original = save_wealth(self.settings, self.directory, None)
        with patch('portfolio.wealth_storage.os.replace', side_effect=OSError('disk failure')):
            with self.assertRaisesRegex(OSError, 'disk failure'):
                save_wealth(WealthSettings(), self.directory, original)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    @unittest.skipIf(os.name == 'nt', 'POSIX permission bits')
    def test_replacement_keeps_wealth_private(self):
        token = save_wealth(self.settings, self.directory, None)
        self.path.chmod(0o644)
        save_wealth(WealthSettings(), self.directory, token)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_failed_staging_keeps_original_and_cleans_temporary(self):
        token = save_wealth(self.settings, self.directory, None)
        with patch('portfolio.files.os.fsync', side_effect=OSError('disk error')):
            with self.assertRaisesRegex(OSError, 'disk error'):
                save_wealth(WealthSettings(), self.directory, token)
        self.assertEqual(self.path.read_bytes(), token)
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_second_conflict_check_detects_edit_during_staging(self):
        original = save_wealth(self.settings, self.directory, None)
        with patch('portfolio.files.os.fsync', side_effect=lambda fd: self.path.write_bytes(b'changed')):
            with self.assertRaisesRegex(ValueError, 'changed outside'):
                save_wealth(WealthSettings(), self.directory, original)
        self.assertEqual(self.path.read_bytes(), b'changed')
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_malformed_duplicate_unknown_and_missing_fields_are_rejected(self):
        original = save_wealth(self.settings, self.directory, None)
        payloads = [b'invalid JSON', b'{"version":2,"version":2}', b'null', b'[]',
                    b'{"version":2,"properties":[],"pensions":[],"loans":[],"extra":1}']
        for mutate in (
            lambda data: data.update(version=True),
            lambda data: data.update(version=3),
            lambda data: data.update(properties={}),
            lambda data: data['properties'][0].update(unknown=1),
            lambda data: data['properties'][0].pop('value'),
            lambda data: data['properties'][0].update(value=float('nan')),
            lambda data: data['properties'][0].pop('vacancy_percent'),
            lambda data: data['properties'][0].pop('cashflows'),
            lambda data: data['properties'][0].update(cashflows={}),
            lambda data: data['properties'][0].update(rent=None),
            lambda data: data['properties'][0].update(costs=[]),
            lambda data: data['properties'][0].update(occupancy='rented'),
            lambda data: data['pensions'][0].update(access='2030-01-01'),
            lambda data: data['loans'][0]['payment'].update(start='2027-1'),
            lambda data: data['loans'][0].update(property_id='missing'),
        ):
            data = json.loads(original)
            mutate(data)
            payloads.append(json.dumps(data).encode())
        for payload in payloads:
            with self.subTest(payload=payload):
                self.path.write_bytes(payload)
                with self.assertRaisesRegex(ValueError, 'Invalid wealth.json'):
                    load_wealth(self.directory)
                self.assertEqual(self.path.read_bytes(), payload)

    def test_version_one_is_rejected_without_modifying_file(self):
        payload = b'{"version":1,"properties":[],"pensions":[],"loans":[]}'
        self.path.write_bytes(payload)
        with self.assertRaisesRegex(ValueError, 'Unsupported wealth file version'):
            load_wealth(self.directory)
        self.assertEqual(self.path.read_bytes(), payload)

    def test_property_cashflow_fields_are_strictly_validated(self):
        original = save_wealth(self.settings, self.directory, None)
        for mutate in (
            lambda entry: entry.update(unknown=1),
            lambda entry: entry.update(payer='tenant'),
            lambda entry: entry.pop('flow'),
            lambda entry: entry.pop('direction'),
            lambda entry: entry.pop('occupancy'),
            lambda entry: entry.pop('appreciation_percent'),
            lambda entry: entry.update(flow=None),
            lambda entry: entry['flow'].update(unknown=1),
            lambda entry: entry['flow'].pop('amount'),
            lambda entry: entry['flow'].update(start='2026-11-01'),
            lambda entry: entry.update(direction='incoming'),
            lambda entry: entry.update(direction=True),
            lambda entry: entry.update(occupancy='owner-occupied'),
            lambda entry: entry.update(occupancy=None),
            lambda entry: entry.update(appreciation_percent=-100),
            lambda entry: entry.update(appreciation_percent=1001),
            lambda entry: entry.update(appreciation_percent=True),
            lambda entry: entry.update(appreciation_percent=float('inf')),
        ):
            data = json.loads(original)
            mutate(data['properties'][0]['cashflows'][0])
            payload = json.dumps(data).encode()
            with self.subTest(payload=payload):
                self.path.write_bytes(payload)
                with self.assertRaisesRegex(ValueError, 'Invalid wealth.json'):
                    load_wealth(self.directory)
                self.assertEqual(self.path.read_bytes(), payload)

    def test_vacancy_percentage_is_validated_when_loading(self):
        original = save_wealth(self.settings, self.directory, None)
        for value in (-0.01, 100.01, True, None, 'unknown', float('nan'), float('inf')):
            data = json.loads(original)
            data['properties'][0]['vacancy_percent'] = value
            payload = json.dumps(data).encode()
            with self.subTest(value=value):
                self.path.write_bytes(payload)
                with self.assertRaisesRegex(ValueError, 'Invalid wealth.json'):
                    load_wealth(self.directory)
                self.assertEqual(self.path.read_bytes(), payload)

    def test_exact_decimal_validation_rejects_fractional_huf_before_float_rounding(self):
        token = save_wealth(self.settings, self.directory, None)
        token = token.replace(b'50000000.0', b'50000000.00000000000001')
        self.path.write_bytes(token)
        with self.assertRaisesRegex(ValueError, 'HUF amounts must be whole'):
            load_wealth(self.directory)

    def test_exact_decimal_validation_covers_nested_property_cashflow_amounts(self):
        token = save_wealth(self.settings, self.directory, None)
        modified = token.replace(b'1000.0', b'1000.00000000000001', 1)
        self.assertNotEqual(modified, token)
        self.path.write_bytes(modified)
        with self.assertRaisesRegex(ValueError, 'must be a whole number of HUF'):
            load_wealth(self.directory)

    def test_minimal_records_round_trip_without_payment_schedules(self):
        settings = WealthSettings(properties=(Property('home', 'Home', 100),),
                                  pensions=(Pension('income', 'State pension', kind='income'),),
                                  loans=(Loan('loan', 'Debt', 10),))
        save_wealth(settings, self.directory, None)
        self.assertEqual(load_wealth(self.directory)[0], settings)


if __name__ == '__main__':
    unittest.main()
