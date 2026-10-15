"""CSV portfolio storage and live market data fetching."""

import csv
from dataclasses import asdict, fields
import hashlib
import io
import os
from pathlib import Path
import sys
import tempfile

from portfolio.models import (AllocationTargets, AssetDetails, CASH, STOCK_MARKET, TREASURY, Holding, ManualHolding,
                    MarketData, Portfolio, Quote, currency_code, parse_manual_amount)


RESOURCE_DIRECTORY = Path(__file__).resolve().parents[1]
SCRIPT_DIRECTORY = (Path(os.environ.get("APPIMAGE") or sys.executable).resolve().parent
                    if getattr(sys, "frozen", False) else RESOURCE_DIRECTORY)


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
    manual_holdings = tuple(ManualHolding(parse_manual_amount(row["amount"]),
                                         row["currency"].strip()) for row in manual)
    portfolio = Portfolio(tuple(holdings), _read_allocation(groups),
                          _unique(((currency_code(row["currency"].strip()), float(row["amount"]))
                                   for row in cash), "cash currency"),
                          manual_holdings)
    return portfolio, _signature(contents)


def fetch_market_data(portfolio, base_currency="EUR"):
    """Fetch each quote and each required currency-to-base rate exactly once."""
    import yfinance as yf

    base_currency = currency_code(base_currency)
    quotes = {}
    for holding in portfolio.required_holdings:
        try:
            info = yf.Ticker(holding.ticker).fast_info
            quotes[holding.ticker] = Quote(info["last_price"], info["currency"])
        except Exception as error:
            raise ValueError(f"Could not price {holding.ticker}: {error}") from error
    currencies = portfolio.required_currencies(quotes)
    rates = {base_currency: 1.}
    for currency in sorted(currencies - {base_currency}):
        try:
            rates[currency] = yf.Ticker(f"{currency}{base_currency}=X").fast_info["last_price"]
        except Exception as error:
            raise ValueError(f"Could not price {currency}/{base_currency}: {error}") from error
    return MarketData(quotes, rates, base_currency)


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
    result = {}
    for name in _CSV_COLUMNS:
        try:
            result[name] = (directory / name).read_bytes()
        except FileNotFoundError:
            result[name] = None
    return result


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
    ter = values["ter"].strip()
    values["ter"] = float(ter) if ter else None
    return AssetDetails(**values)


def _allocation_rows(allocation):
    return [{"parent": parent, "asset_group": name, "portfolio_allocation": str(target)}
            for parent, weights in allocation.weights.items() for name, target in weights.items()]


def _allocation_origins(group_origins):
    """Preserve stock-class metadata through renames without reusing deleted names."""
    return {(STOCK_MARKET, group): (STOCK_MARKET, origin) if origin is not None else None
            for group, origin in (group_origins or {}).items()}


def _read_allocation(rows):
    values = _unique((((row["parent"].strip(), row["asset_group"].strip()),
                        float(row["portfolio_allocation"])) for row in rows), "allocation target")
    weights = {parent: {} for parent in (STOCK_MARKET, TREASURY, CASH)}
    for (parent, name), value in values.items():
        if parent not in weights:
            raise ValueError(f"Unknown allocation parent: {parent}")
        weights[parent][name] = value
    return AllocationTargets(weights)


def _stage(path, contents):
    """Flush a sibling temporary file before its atomic replacement."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{path.name}.", suffix=".tmp",
                                         dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            temporary.chmod(path.stat().st_mode & 0o777)
        return temporary
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def _replace_portfolio_files(directory, original, serialized):
    """Stage all replacements and keep originals recoverable until every write succeeds."""
    directory.mkdir(parents=True, exist_ok=True)
    staged, backups, replaced = {}, {}, []
    try:
        for name, contents in serialized.items():
            staged[name] = _stage(directory / name, contents)
            if original[name] is not None:
                backups[name] = _stage(directory / name, original[name])
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
