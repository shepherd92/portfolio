"""Best-effort Yahoo listing metadata; unknown instrument details stay blank."""

from math import isfinite
from numbers import Real

from portfolio.models import AssetDetails, currency_code, identifier


def fetch_asset_details(ticker):
    """Look up an exact Yahoo ticker, without guessing another listing or broker name.

    Yahoo's annual-report expense ratio is a fraction, converted here to percent.
    It may be unavailable or older than the issuer's current published charge.
    yfinance's experimental, name-matched ISIN search is deliberately not used.
    """
    import yfinance as yf

    ticker = identifier(ticker, 'Ticker').upper()
    try:
        asset = yf.Ticker(ticker)
        info = asset.get_info()
    except Exception as error:
        raise ValueError(f'Could not look up {ticker}: {error}') from error
    if not isinstance(info, dict):
        raise ValueError(f'Yahoo returned no listing information for {ticker}.')
    symbol = _text(info.get('symbol'))
    if symbol and symbol.upper() != ticker:
        raise ValueError(f'Yahoo returned {symbol} instead of {ticker}; details were not applied.')

    details = AssetDetails(
        isin=_isin(info.get('isin')),
        exchange=_text(info.get('fullExchangeName')) or _text(info.get('exchange')),
        currency=_currency(info.get('currency')),
        ter=_expense_ratio(asset, ticker) if info.get('quoteType') in ('ETF', 'MUTUALFUND') else None,
        name=_text(info.get('longName')),
    )
    if details == AssetDetails():
        raise ValueError(f'No usable details found for {ticker}. Check its exchange suffix or enter details manually.')
    return details


def _text(value):
    return value.strip() if isinstance(value, str) else ''


def _currency(value):
    value = _text(value)
    value = {'GBp': 'GBP', 'GBX': 'GBP', 'ZAc': 'ZAR', 'ILA': 'ILS'}.get(value, value)
    try:
        return currency_code(value)
    except ValueError:
        return ''


def _isin(value):
    # Accept only an explicit listing field, validated by the shared model.
    try:
        return AssetDetails(isin=_text(value)).isin
    except ValueError:
        return ''


def _expense_ratio(asset, ticker):
    try:
        value = asset.funds_data.fund_operations.loc['Annual Report Expense Ratio', ticker]
    except Exception:
        return None  # Optional fund data must not discard usable listing details.
    if isinstance(value, Real) and not isinstance(value, bool) and isfinite(value) and 0 <= value <= 1:
        return float(value) * 100
    return None
