"""Strict, atomic storage of separately valued assets and their payment schedules."""

from dataclasses import asdict, fields
from datetime import date
from decimal import Decimal
import json
import os
from pathlib import Path

from portfolio.calculator import CashFlow
from portfolio.files import json_bytes, read_optional_bytes, stage_bytes, unique_object
from portfolio.wealth import Loan, Pension, PensionContribution, Property, PropertyCashFlow, WealthSettings


def load_wealth(directory):
    """Return validated records and exact original bytes for conflict-safe saving."""
    original = read_optional_bytes(Path(directory) / 'wealth.json')
    if original is None:
        return WealthSettings(), None
    try:
        values = json.loads(original, parse_float=Decimal, object_pairs_hook=unique_object,
                            parse_constant=_invalid_constant)
        _keys(values, {'version', 'properties', 'pensions', 'loans'})
        if type(values['version']) is not int or values['version'] != 2:
            raise ValueError('Unsupported wealth file version.')
        properties, pensions, loans = [], [], []
        for row in _rows(values['properties']):
            _keys(row, Property)
            cashflows = []
            for entry in _rows(row['cashflows']):
                _keys(entry, PropertyCashFlow)
                cashflows.append(PropertyCashFlow(**{**entry, 'flow': _flow(entry['flow'])}))
            row['cashflows'] = tuple(cashflows)
            properties.append(Property(**row))
        for row in _rows(values['pensions']):
            _keys(row, Pension)
            row['access'] = _month(row['access']) if row['access'] is not None else None
            contributions = []
            for entry in _rows(row['contributions']):
                _keys(entry, PensionContribution)
                contributions.append(PensionContribution(_flow(entry['flow']), entry['source']))
            row['contributions'] = tuple(contributions)
            row['withdrawals'] = tuple(_flow(flow) for flow in _rows(row['withdrawals']))
            pensions.append(Pension(**row))
        for row in _rows(values['loans']):
            _keys(row, Loan)
            row['payment'] = _flow(row['payment']) if row['payment'] is not None else None
            loans.append(Loan(**row))
        return WealthSettings(tuple(properties), tuple(pensions), tuple(loans)), original
    except (ValueError, TypeError, UnicodeError, KeyError) as error:
        raise ValueError(f'Invalid wealth.json: {error}') from error


def save_wealth(settings, directory, original):
    """Atomically save only wealth.json, rejecting intervening external edits."""
    if not isinstance(settings, WealthSettings):
        raise ValueError('Expected validated other assets and loans.')
    if original is not None and not isinstance(original, bytes):
        raise ValueError('Expected the original wealth file bytes from loading.')
    path = Path(directory) / 'wealth.json'
    contents = json_bytes({'version': 2, **asdict(settings)})
    _check_original(path, original)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = stage_bytes(path, contents, preserve_mode=False)
    try:
        _check_original(path, original)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return contents


def _check_original(path, original):
    if read_optional_bytes(path) != original:
        raise ValueError('Other assets and loans changed outside this application. Reload before saving.')


def _rows(value):
    if not isinstance(value, list):
        raise ValueError('Asset and payment records must be JSON lists.')
    return value


def _keys(value, model):
    expected = model if isinstance(model, set) else {field.name for field in fields(model)}
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError('A record has missing or unsupported fields.')


def _invalid_constant(value):
    raise ValueError(f'Invalid numeric value: {value}.')


def _month(value):
    if not isinstance(value, str) or len(value) != 7:
        raise ValueError('Payment and access months use YYYY-MM.')
    parsed = date.fromisoformat(f'{value}-01')
    if parsed.isoformat()[:7] != value:
        raise ValueError('Payment and access months use YYYY-MM.')
    return parsed


def _flow(row):
    _keys(row, CashFlow)
    return CashFlow(**{**row, 'start': _month(row['start']),
                       'end': _month(row['end']) if row['end'] is not None else None})
