"""Live market quotes, exchange rates, and the official MNB base rate."""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from html.parser import HTMLParser
import re
from threading import Lock
from urllib.request import Request, urlopen

from portfolio.models import MarketData, Quote, currency_code, number

BASE_RATE_URL = 'https://www.mnb.hu/Jegybanki_alapkamat_alakulasa'
_MONTHS = ('január', 'február', 'március', 'április', 'május', 'június',
           'július', 'augusztus', 'szeptember', 'október', 'november', 'december')


@dataclass(frozen=True)
class BaseRate:
    percent: float
    effective_date: date
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc), compare=False)


class MarketCache:
    """Session-local successful requests, shared safely by overlapping workers.

    Native quotes do not depend on the display currency. FX is cached against
    EUR so changing that currency only needs a missing or expired EUR rate.
    A canceled UI worker may finish a request; its reusable result stays here,
    while the UI remains responsible for discarding the canceled callback.
    """

    def __init__(self, max_age_seconds=15 * 60):
        self.max_age_seconds = number(max_age_seconds, 'Cache lifetime', minimum=0)
        self._entries = {}
        self._locks = {}
        self._guard = Lock()

    def _get(self, key, fetch):
        with self._guard:
            lock = self._locks.setdefault(key, Lock())
        # Checking again after taking the per-key lock coalesces requests made
        # while a canceled or timed-out worker still has a request in flight.
        with lock:
            entry = self._entries.get(key)
            if entry is not None:
                age = (datetime.now(timezone.utc) - entry[1]).total_seconds()
                if 0 <= age < self.max_age_seconds:
                    return entry
            value = fetch()  # Errors are never stored, and may be retried.
            entry = value, datetime.now(timezone.utc)
            self._entries[key] = entry
            return entry

    def _exchange_rate(self, source, destination):
        if source == destination:
            return 1., None
        values, fetched = {}, []
        for currency in (source, destination):
            if currency == 'EUR':
                values[currency] = 1.
            else:
                values[currency], stamp = self._get(
                    ('fx', currency), lambda currency=currency: fetch_exchange_rate(currency, 'EUR'))
                fetched.append(stamp)
        rate = number(values[source] / values[destination], 'Exchange rate', minimum=0)
        if not rate:
            raise ValueError('Exchange rate must be positive')
        return rate, min(fetched)


def fetch_base_rate(*, cache=None):
    """Read the latest effective base rate from the central bank's own table."""
    if cache is not None:
        return cache._get(('base_rate',), fetch_base_rate)[0]
    try:
        request = Request(BASE_RATE_URL, headers={'User-Agent': 'PortfolioPlanner/1.0'})
        with urlopen(request, timeout=20) as response:
            html = response.read(2_000_000).decode('utf-8')
        return _parse_base_rate(html, date.today())
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError(f'Could not load the MNB base rate: {error}') from error


def fetch_exchange_rate(source, destination, *, cache=None):
    """Return destination-currency units per source-currency unit."""
    import yfinance as yf

    source, destination = currency_code(source), currency_code(destination)
    if source == destination:
        return 1.
    if cache is not None:
        return cache._exchange_rate(source, destination)[0]
    try:
        value = yf.Ticker(f'{source}{destination}=X').fast_info['last_price']
        rate = number(value, 'Exchange rate', minimum=0)
        if not rate:
            raise ValueError('Exchange rate must be positive')
        return rate
    except Exception as error:
        raise ValueError(f'Could not price {source}/{destination}: {error}') from error


def fetch_market_data(portfolio, base_currency="EUR", *, extra_currencies=(), cache=None):
    """Fetch missing/expired inputs once, preserving the oldest input's age."""
    base_currency = currency_code(base_currency)
    currencies = {currency_code(currency) for currency in extra_currencies}
    quotes, fetched = {}, []
    for holding in portfolio.required_holdings:
        if cache is None:
            quotes[holding.ticker] = _fetch_quote(holding.ticker)
        else:
            quotes[holding.ticker], stamp = cache._get(
                ('quote', holding.ticker), lambda ticker=holding.ticker: _fetch_quote(ticker))
            fetched.append(stamp)
    currencies.update(portfolio.required_currencies(quotes))
    rates = {base_currency: 1.}
    for currency in sorted(currencies - {base_currency}):
        if cache is None:
            rates[currency] = fetch_exchange_rate(currency, base_currency)
        else:
            rates[currency], stamp = cache._exchange_rate(currency, base_currency)
            fetched.append(stamp)
    return MarketData(quotes, rates, base_currency,
                      fetched_at=min(fetched) if fetched else datetime.now(timezone.utc))


def _fetch_quote(ticker):
    import yfinance as yf

    try:
        info = yf.Ticker(ticker).fast_info
        return Quote(info['last_price'], info['currency'])
    except Exception as error:
        raise ValueError(f'Could not price {ticker}: {error}') from error


def _parse_base_rate(html, today):
    table = _RateTable()
    table.feed(html)
    rates = []
    for row in table.rows:
        if len(row) != 2:
            continue
        match = re.fullmatch(r'(\d{4})\.\s+(\w+)\s+(\d{1,2})\.,.*', row[0])
        if not match or not re.fullmatch(r'\d{1,2}(?:,\d+)?\s*%', row[1]):
            continue
        year, month, day = match.groups()
        effective = date(int(year), _MONTHS.index(month) + 1, int(day))
        percent = float(row[1].rstrip('%').replace(',', '.'))
        if effective <= today:
            rates.append(BaseRate(percent, effective))
    if not rates:
        raise ValueError('No effective rate found in the official rate table')
    return max(rates, key=lambda rate: rate.effective_date)


class _RateTable(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self._table = self._cell = False
        self._row, self._text = [], []

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self._table = 'datatable' in dict(attrs).get('class', '').split()
        if self._table and tag == 'tr':
            self._row = []
        if self._table and tag == 'td':
            self._cell, self._text = True, []

    def handle_data(self, data):
        if self._cell:
            self._text.append(data)

    def handle_endtag(self, tag):
        if self._table and tag == 'td':
            self._row.append(' '.join(''.join(self._text).split()))
            self._cell = False
        if self._table and tag == 'tr':
            self.rows.append(self._row)
        if tag == 'table':
            self._table = False
