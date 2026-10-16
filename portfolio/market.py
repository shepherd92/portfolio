"""Live market quotes, exchange rates, and the official MNB base rate."""

from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
import re
from urllib.request import Request, urlopen

from portfolio.models import MarketData, Quote, currency_code, number

BASE_RATE_URL = 'https://www.mnb.hu/Jegybanki_alapkamat_alakulasa'
_MONTHS = ('január', 'február', 'március', 'április', 'május', 'június',
           'július', 'augusztus', 'szeptember', 'október', 'november', 'december')


@dataclass(frozen=True)
class BaseRate:
    percent: float
    effective_date: date


def fetch_base_rate():
    """Read the latest effective base rate from the central bank's own table."""
    try:
        request = Request(BASE_RATE_URL, headers={'User-Agent': 'PortfolioPlanner/1.0'})
        with urlopen(request, timeout=20) as response:
            html = response.read(2_000_000).decode('utf-8')
        return _parse_base_rate(html, date.today())
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError(f'Could not load the MNB base rate: {error}') from error


def fetch_exchange_rate(source, destination):
    """Return destination-currency units per source-currency unit."""
    import yfinance as yf

    source, destination = currency_code(source), currency_code(destination)
    if source == destination:
        return 1.
    try:
        value = yf.Ticker(f'{source}{destination}=X').fast_info['last_price']
        rate = number(value, 'Exchange rate', minimum=0)
        if not rate:
            raise ValueError('Exchange rate must be positive')
        return rate
    except Exception as error:
        raise ValueError(f'Could not price {source}/{destination}: {error}') from error


def fetch_market_data(portfolio, base_currency="EUR", *, extra_currencies=()):
    """Fetch each quote and each required currency-to-base rate exactly once."""
    import yfinance as yf

    base_currency = currency_code(base_currency)
    currencies = {currency_code(currency) for currency in extra_currencies}
    quotes = {}
    for holding in portfolio.required_holdings:
        try:
            info = yf.Ticker(holding.ticker).fast_info
            quotes[holding.ticker] = Quote(info["last_price"], info["currency"])
        except Exception as error:
            raise ValueError(f"Could not price {holding.ticker}: {error}") from error
    currencies.update(portfolio.required_currencies(quotes))
    rates = {base_currency: 1.}
    for currency in sorted(currencies - {base_currency}):
        rates[currency] = fetch_exchange_rate(currency, base_currency)
    return MarketData(quotes, rates, base_currency)


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
