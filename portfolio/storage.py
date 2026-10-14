"""CSV portfolio storage, fixed JSON snapshots, and explicit Yahoo fetching."""

import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile

from portfolio.models import (AllocationTargets, CASH, STOCK_MARKET, TREASURY, Holding, ManualHolding,
                    MarketSnapshot, Portfolio, Quote, currency_code, parse_manual_amount)


SCRIPT_DIRECTORY = Path(__file__).resolve().parents[1]
SNAPSHOT_DIRECTORY = SCRIPT_DIRECTORY / "snapshots"


_CSV_COLUMNS = {
    "asset_group.csv": ("parent", "asset_group", "target_allocation"),
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
             "is_main": str(holding.is_main), "shares": str(holding.shares)}
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
                                int(row["shares"]), flag == "true"))
    manual_holdings = tuple(ManualHolding(parse_manual_amount(row["amount"]),
                                         row["currency"].strip()) for row in manual)
    portfolio = Portfolio(tuple(holdings), _read_allocation(groups),
                          _unique(((currency_code(row["currency"].strip()), float(row["amount"]))
                                   for row in cash), "cash currency"),
                          manual_holdings)
    return portfolio, _signature(contents)


def load_snapshot(path):
    with Path(path).open(encoding="utf-8") as stream:
        data = json.load(stream, object_pairs_hook=lambda pairs: _unique(pairs, "JSON key"))
    if (not isinstance(data, dict) or not {"quotes", "rates", "base_currency"} <= data.keys()
            or not isinstance(data["quotes"], dict) or not isinstance(data["rates"], dict)
            or any(not isinstance(quote, dict) or set(quote) != {"price", "currency"}
                   for quote in data["quotes"].values())):
        raise ValueError("Snapshot requires base_currency, a quotes object with price/currency, and a rates object")
    return MarketSnapshot({ticker: Quote(**quote) for ticker, quote in data["quotes"].items()},
                          data["rates"], data["base_currency"], data.get("fetched_at"))


def save_snapshot(snapshot, path):
    """Atomically save prices in snapshots/ or outside the script's code tree."""
    if not isinstance(snapshot, MarketSnapshot):
        raise ValueError("Expected a MarketSnapshot")
    if any(not isinstance(ticker, str) for ticker in snapshot.quotes):
        raise ValueError("Snapshot quote tickers must be strings")
    snapshot = MarketSnapshot(
        {ticker: Quote(quote.price, quote.currency) for ticker, quote in snapshot.quotes.items()},
        snapshot.rates, snapshot.base_currency, snapshot.fetched_at)
    data = {"base_currency": snapshot.base_currency,
            "quotes": {ticker: {"price": quote.price, "currency": quote.currency}
                       for ticker, quote in snapshot.quotes.items()},
            "rates": dict(snapshot.rates)}
    if snapshot.fetched_at is not None:
        data["fetched_at"] = snapshot.fetched_at
    serialized = (json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    try:
        path = Path(path).expanduser().resolve()
    except RuntimeError as error:
        raise ValueError(f"Could not resolve snapshot path: {error}") from error
    code_directory = SCRIPT_DIRECTORY.resolve()
    # Keep the allowlist at the literal snapshots directory. Resolving that
    # directory could turn a symlink into permission to overwrite source files.
    snapshot_directory = code_directory / "snapshots"
    if path.is_relative_to(code_directory) and not path.is_relative_to(snapshot_directory):
        raise ValueError(f"Save snapshots in {SNAPSHOT_DIRECTORY} or outside the script directory")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _stage(path, serialized)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def fetch_snapshot(portfolio, base_currency="EUR"):
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
    return MarketSnapshot(quotes, rates, base_currency, datetime.now(timezone.utc).isoformat())


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
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        value = row_key(row)
        original_key = (original_keys or {}).get(value, value)
        writer.writerow({**existing.get(original_key, {}), **row})
    return stream.getvalue().encode("utf-8")


def _allocation_rows(allocation):
    rows = [{"parent": "", "asset_group": root, "target_allocation": str(target)}
            for root, target in allocation.top.items()]
    rows.extend({"parent": root, "asset_group": child, "target_allocation": str(target)}
                for root, children in allocation.children.items()
                for child, target in children.items())
    return rows


def _allocation_origins(group_origins):
    """Preserve stock-class metadata through renames without reusing deleted names."""
    return {(STOCK_MARKET, group): (STOCK_MARKET, origin) if origin is not None else None
            for group, origin in (group_origins or {}).items()}


def _read_allocation(rows):
    values = _unique((((row["parent"].strip(), row["asset_group"].strip()),
                        float(row["target_allocation"])) for row in rows), "allocation target")
    top = {child: value for (parent, child), value in values.items() if not parent}
    children = {root: {} for root in (STOCK_MARKET, TREASURY, CASH)}
    for (parent, child), value in values.items():
        if parent:
            if parent not in children:
                raise ValueError(f"Unknown allocation parent: {parent}")
            children[parent][child] = value
    return AllocationTargets(top, children)


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
