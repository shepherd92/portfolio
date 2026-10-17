"""CSV portfolio and JSON calculator input storage."""

import csv
from dataclasses import asdict, fields
from datetime import date
from decimal import Decimal
import hashlib
import io
import json
import os
from pathlib import Path

from portfolio.calculator import CalculatorSettings, CashFlow, ReturnAssumptions
from portfolio.files import json_bytes, read_optional_bytes, stage_bytes, unique_object
from portfolio.models import (AllocationTargets, AssetDetails, CASH, STOCK_MARKET, TREASURY, Holding, ManualHolding,
                    Portfolio, currency_code, parse_currency_amount, parse_manual_amount)


_ASSET_DETAIL_COLUMNS = tuple(field.name for field in fields(AssetDetails))
_CSV_COLUMNS = {
    "asset_group.csv": ("parent", "asset_group", "portfolio_allocation"),
    "asset.csv": ("asset_group", "ticker", "is_main", "shares"),
    "cash.csv": ("currency", "amount"),
    "manual_asset.csv": ("currency", "amount"),
}


def config_signature(directory):
    """Return an opaque fingerprint to pass back when saving an edited portfolio."""
    return _signature(_config_bytes(Path(directory)))


def save_portfolio(portfolio, directory, expected_signature=None, *, group_origins=None):
    """Save managed CSVs and return their new signature.

    Existing extra columns are preserved by ticker, allocation path, or currency.
    The manual CSV stays optional when empty.
    group_origins maps current group names to their names when loaded, keeping
    extra columns through renames. Use None for a newly created group's origin.
    Pass the signature captured when loading to reject intervening file edits.
    Each file is atomically replaced; a write failure rolls earlier replacements
    back to their original bytes. This is not a cross-process transaction lock.
    """
    if not isinstance(portfolio, Portfolio):
        raise ValueError("Expected a Portfolio")
    for currency, amount in portfolio.cash.items():
        parse_currency_amount(str(amount), currency)
    for holding in portfolio.manual_holdings:
        parse_manual_amount(str(holding.amount), holding.currency)
    directory = Path(directory)
    original = _config_bytes(directory)
    signature = _signature(original)
    if expected_signature is not None and signature != expected_signature:
        raise ValueError("Portfolio files changed outside this application. Reload before saving.")
    rows = {
        "asset_group.csv": (("parent", "asset_group"), _allocation_rows(portfolio.allocation)),
        "asset.csv": ("ticker", [
            {"asset_group": holding.group, "ticker": holding.ticker,
             "is_main": str(holding.is_main), "shares": str(holding.shares), **asdict(holding.details)}
            for holding in portfolio.holdings]),
        "cash.csv": ("currency", [
            {"currency": currency, "amount": str(amount)}
            for currency, amount in sorted(portfolio.cash.items())]),
    }
    if portfolio.manual_holdings or original["manual_asset.csv"] is not None:
        rows["manual_asset.csv"] = ("currency", [
            {"currency": holding.currency, "amount": str(holding.amount)}
            for holding in portfolio.manual_holdings])
    original_keys = {"asset_group.csv": _allocation_origins(group_origins)}
    serialized = {name: _csv_bytes(name, original[name], key, values,
                                  original_keys.get(name))
                  for name, (key, values) in rows.items()}
    _replace_portfolio_files(directory, original, serialized)
    return _signature({**original, **serialized})


def load_portfolio(directory):
    return load_portfolio_with_signature(directory)[0]


def load_portfolio_with_signature(directory):
    """Return validated inputs and a fingerprint of exactly the bytes parsed."""
    contents = _config_bytes(Path(directory))
    groups = _read_csv("asset_group.csv", contents["asset_group.csv"])[1]
    assets = _read_csv("asset.csv", contents["asset.csv"])[1]
    cash = _read_csv("cash.csv", contents["cash.csv"])[1]
    manual = (_read_csv("manual_asset.csv", contents["manual_asset.csv"])[1]
              if contents["manual_asset.csv"] is not None else [])
    holdings = []
    for row in assets:
        flag = row["is_main"].strip().lower()
        if flag not in ("true", "false"):
            raise ValueError(f"{row['ticker']}: is_main must be True or False")
        holdings.append(Holding(row["ticker"].strip(), row["asset_group"].strip(),
                                int(row["shares"]), flag == "true", _asset_details(row)))
    manual_holdings = tuple(ManualHolding(parse_manual_amount(row["amount"], row['currency'].strip()),
                                         row["currency"].strip()) for row in manual)
    portfolio = Portfolio(tuple(holdings), _read_allocation(groups),
                          _unique(((currency_code(row["currency"].strip()),
                                    parse_currency_amount(row["amount"], row['currency'].strip()))
                                   for row in cash), "cash currency"),
                          manual_holdings)
    return portfolio, _signature(contents)


def load_settings(directory):
    """Load calculator inputs and their exact save token; a missing file uses defaults."""
    original = read_optional_bytes(Path(directory) / 'calculator.json')
    if original is None:
        return CalculatorSettings(), None
    try:
        values = json.loads(original, parse_float=Decimal, object_pairs_hook=unique_object)
        _keys(values, CalculatorSettings)
        _keys(values['returns'], ReturnAssumptions)
        values['returns'] = ReturnAssumptions(**values['returns'])
        for name in ('incomes', 'outgoings'):
            if not isinstance(values[name], list):
                raise ValueError(f'{name.capitalize()} must be a JSON list.')
            flows = []
            for row in values[name]:
                _keys(row, CashFlow)
                start = _setting_month(row['start'])
                end = _setting_month(row['end']) if row['end'] is not None else None
                flows.append(CashFlow(**{**row, 'start': start, 'end': end}))
            values[name] = tuple(flows)
        return CalculatorSettings(**values), original
    except (ValueError, TypeError, UnicodeError) as error:
        raise ValueError(f'Invalid calculator.json: {error}') from error


def save_settings(settings, directory, original):
    """Atomically save calculator inputs without modifying any portfolio files."""
    if type(settings) is not CalculatorSettings:
        raise ValueError('Expected validated calculator settings.')
    path = Path(directory) / 'calculator.json'
    contents = json_bytes(asdict(settings))
    _check_original(path, original)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = stage_bytes(path, contents, preserve_mode=True)
    try:
        _check_original(path, original)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return contents


def _setting_month(value):
    if not isinstance(value, str) or len(value) != 7:
        raise ValueError('Payment months use YYYY-MM.')
    parsed = date.fromisoformat(f'{value}-01')
    if parsed.isoformat()[:7] != value:
        raise ValueError('Payment months use YYYY-MM.')
    return parsed


def _read_csv(name, contents):
    if contents is None:
        raise FileNotFoundError(f"Missing portfolio file: {name}")
    required = set(_CSV_COLUMNS[name])
    with io.StringIO(contents.decode("utf-8-sig"), newline="") as stream:
        reader = csv.DictReader(stream)
        try:
            headers = reader.fieldnames or []
            if len(set(headers)) != len(headers) or not required <= set(headers):
                raise ValueError(f"{name}: required columns are {', '.join(sorted(required))}")
            if name == "manual_asset.csv" and "asset_group" in headers:
                raise ValueError("manual_asset.csv: asset_group is not a supported column")
            rows = list(reader)
        except csv.Error as error:
            raise ValueError(f"{name}: {error}") from error
        if any(None in row or any(value is None for value in row.values()) for row in rows):
            raise ValueError(f"{name}: malformed CSV row")
        return headers, rows


def _config_bytes(directory):
    """Read only managed CSVs; a missing file has a distinct signature."""
    return {name: read_optional_bytes(directory / name) for name in _CSV_COLUMNS}


def _signature(contents):
    return tuple((name, None if data is None else hashlib.sha256(data).hexdigest())
                 for name, data in contents.items())


def _csv_bytes(name, contents, key, rows, original_keys=None):
    """Keep existing column order and extra cell values for unchanged row keys."""
    columns = list(_CSV_COLUMNS[name])
    existing = {}
    keys = (key,) if isinstance(key, str) else key

    def row_key(row):
        values = tuple(currency_code(row[column].strip()) if column == "currency"
                       else row[column].strip() for column in keys)
        if keys == ("parent", "asset_group") and values[0] in (TREASURY, CASH):
            values = (values[0], currency_code(values[1]))
        return values[0] if isinstance(key, str) else values

    if contents is not None:
        columns, existing_rows = _read_csv(name, contents)
        for row in existing_rows:
            value = row_key(row)
            if value in existing:
                raise ValueError(f"{name}: duplicate {key}: {value}")
            existing[value] = row
    if name == "asset.csv":
        columns.extend(column for column in _ASSET_DETAIL_COLUMNS if column not in columns)
    if name == "asset_group.csv" and 'category' not in columns:
        columns.append('category')
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        value = row_key(row)
        original_key = (original_keys or {}).get(value, value)
        writer.writerow({**existing.get(original_key, {}), **row})
    return stream.getvalue().encode("utf-8")


def _asset_details(row):
    values = {name: row.get(name, "") for name in _ASSET_DETAIL_COLUMNS}
    for name in ("ter", "color_hue"):
        value = values[name].strip()
        values[name] = float(value) if value else None
    return AssetDetails(**values)


def _allocation_rows(allocation):
    return [{"parent": parent, "asset_group": name, "portfolio_allocation": str(target),
             'category': allocation.categories.get(name, '') if parent == STOCK_MARKET else ''}
            for parent, weights in allocation.weights.items() for name, target in weights.items()]


def _allocation_origins(group_origins):
    """Preserve stock-class metadata through renames without reusing deleted names."""
    return {(STOCK_MARKET, group): (STOCK_MARKET, origin) if origin is not None else None
            for group, origin in (group_origins or {}).items()}


def _read_allocation(rows):
    if any(row['parent'].strip() != STOCK_MARKET and row.get('category', '').strip() for row in rows):
        raise ValueError('Only stock market asset classes can have investment categories')
    values = _unique((((row["parent"].strip(), row["asset_group"].strip()),
                        float(row["portfolio_allocation"])) for row in rows), "allocation target")
    weights = {parent: {} for parent in (STOCK_MARKET, TREASURY, CASH)}
    for (parent, name), value in values.items():
        if parent not in weights:
            raise ValueError(f"Unknown allocation parent: {parent}")
        weights[parent][name] = value
    categories = {row['asset_group'].strip(): row.get('category', '').strip()
                  for row in rows if row['parent'].strip() == STOCK_MARKET}
    return AllocationTargets(weights, categories)


def _replace_portfolio_files(directory, original, serialized):
    """Stage all replacements and keep originals recoverable until every write succeeds."""
    directory.mkdir(parents=True, exist_ok=True)
    staged, backups, replaced = {}, {}, []
    try:
        for name, contents in serialized.items():
            staged[name] = stage_bytes(directory / name, contents, preserve_mode=True)
            if original[name] is not None:
                backups[name] = stage_bytes(directory / name, original[name], preserve_mode=True)
        if config_signature(directory) != _signature(original):
            raise ValueError("Portfolio files changed while saving. Reload before saving.")
        try:
            for name, temporary in staged.items():
                os.replace(temporary, directory / name)
                replaced.append(name)
        except BaseException as error:
            rollback_errors = []
            for name in reversed(replaced):
                try:
                    if original[name] is None:
                        (directory / name).unlink()
                    else:
                        os.replace(backups[name], directory / name)
                except OSError as rollback_error:
                    rollback_errors.append(f"{name}: {rollback_error}")
            if rollback_errors:
                # Keep recovery copies if the filesystem cannot restore them.
                recovery = [str(path) for path in backups.values() if path.exists()]
                backups.clear()
                raise OSError("Save failed and some originals could not be restored: "
                              + "; ".join(rollback_errors)
                              + ". Recovery copies: " + ", ".join(recovery)) from error
            raise
    finally:
        for temporary in (*staged.values(), *backups.values()):
            temporary.unlink(missing_ok=True)


def _unique(pairs, name):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate {name}: {key}")
        result[key] = value
    return result


def _check_original(path, original):
    if read_optional_bytes(path) != original:
        raise ValueError('Calculator inputs changed outside this application. Reload before saving.')


def _keys(value, model):
    if not isinstance(value, dict) or set(value) != {field.name for field in fields(model)}:
        raise ValueError(f'{model.__name__} has missing or unsupported fields.')
