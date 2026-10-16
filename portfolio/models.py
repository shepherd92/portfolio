"""Immutable inputs and results. All monetary values use explicit currencies."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from math import fsum, isclose, isfinite
from numbers import Integral, Real
from types import MappingProxyType
from typing import Mapping

MAX_SHARES = 2**53 - 1  # Consecutive integers remain exactly representable by the solver.
CASH = "Cash"
STOCK_MARKET = "Stock market"
TREASURY = "MÁK"
ALLOCATION_BRANCHES = (STOCK_MARKET, TREASURY, CASH)
MARKET_CATEGORIES = ('Equities', 'Gold', 'Bonds')


def number(value, name, *, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (Real, Decimal)):
        raise ValueError(f"{name} must be a finite number")
    try:
        value = float(value)
    except OverflowError as error:
        raise ValueError(f"{name} must be a finite number") from error
    if not isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def currency_code(value):
    if not isinstance(value, str) or len(value) != 3 or not value.isascii() or not value.isalpha():
        raise ValueError(f"Invalid currency: {value!r}")
    return value.upper()


def identifier(value, name):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be a nonempty name without surrounding whitespace")
    return value


def allocation_name(category, name):
    """Validate a stock class or normalize a currency within its category."""
    if category not in ALLOCATION_BRANCHES:
        raise ValueError('Allocation category must be Stock market, MÁK, or Cash.')
    if category == STOCK_MARKET:
        name = identifier(name, 'Asset class')
        if name in ALLOCATION_BRANCHES:
            raise ValueError('This name is reserved for an allocation category.')
    else:
        name = currency_code(name)
        if category == TREASURY and name not in ('EUR', 'HUF'):
            raise ValueError('MÁK targets must use EUR or HUF.')
    return name


def percentage(value):
    value = number(value, 'Target percentage', minimum=0)
    if value > 100:
        raise ValueError('Target percentages cannot exceed 100%.')
    return value


def current_values(holdings, manual_holdings, cash, market, *, parent=None):
    """Value holdings by allocation leaf, without requiring valid allocation targets.

    A selected parent only prices that branch; zero positions need no quote
    or exchange rate. Signed cash balances remain signed.
    """
    if parent not in (None, STOCK_MARKET, TREASURY, CASH):
        raise ValueError(f'Unknown allocation category: {parent!r}')
    parts = defaultdict(list)
    if parent in (None, STOCK_MARKET):
        for holding in holdings:
            value = holding.shares * market.price(holding.ticker) if holding.shares else 0.
            parts[STOCK_MARKET, holding.group].append(value)
    if parent in (None, TREASURY):
        for holding in manual_holdings:
            value = market.value(holding.amount, holding.currency) if holding.amount else 0.
            parts[TREASURY, holding.currency].append(value)
    if parent in (None, CASH):
        for currency, amount in cash.items():
            parts[CASH, currency].append(market.value(amount, currency) if amount else 0.)
    return {key: fsum(values) for key, values in parts.items()}


@dataclass(frozen=True)
class AllocationTargets:
    """Every asset target is a percentage of total portfolio value."""

    weights: Mapping[str, Mapping[str, float]]
    categories: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if set(self.weights) != set(ALLOCATION_BRANCHES):
            raise ValueError("Targets must describe Stock market, MÁK, and Cash")
        weights = {}
        for branch in ALLOCATION_BRANCHES:
            values = {}
            for name, value in self.weights[branch].items():
                name = allocation_name(branch, name)
                if name in values:
                    raise ValueError(f'Duplicate target: {name}')
                values[name] = percentage(value)
            weights[branch] = MappingProxyType(values)
        total = fsum(value for values in weights.values() for value in values.values())
        if not isclose(total, 100, abs_tol=1e-8, rel_tol=0):
            difference = abs(100 - total)
            if difference < .005:
                raise ValueError('Targets must sum to 100%; small differences can be hidden by display rounding.')
            shown_total = f'{total:.2f}'.rstrip('0').rstrip('.')
            shown_difference = f'{difference:.2f}'.rstrip('0').rstrip('.')
            change = f'{"add" if total < 100 else "reduce by"} {shown_difference}%'
            raise ValueError(f'Portfolio targets total {shown_total}%: {change}. Targets must sum to 100%.')
        object.__setattr__(self, "weights", MappingProxyType(weights))
        if not isinstance(self.categories, Mapping):
            raise ValueError('Investment categories must map stock classes to categories')
        if self.categories.keys() - weights[STOCK_MARKET].keys():
            raise ValueError('Only stock market asset classes can have investment categories')
        categories = {group: self.categories.get(group, '') for group in weights[STOCK_MARKET]}
        if any(category not in ('', *MARKET_CATEGORIES) for category in categories.values()):
            raise ValueError('Investment category must be Equities, Gold, or Bonds')
        object.__setattr__(self, 'categories', MappingProxyType(categories))

    @property
    def totals(self):
        return MappingProxyType({branch: fsum(values.values()) for branch, values in self.weights.items()})


def parse_currency_amount(text, currency):
    """Parse an entered balance, rejecting fractional HUF before float conversion."""
    try:
        amount = Decimal(text)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError('Enter a finite currency amount') from error
    if not amount.is_finite():
        raise ValueError('Enter a finite currency amount')
    if currency_code(currency) == 'HUF' and amount != amount.to_integral_value():
        raise ValueError('HUF amounts must be whole numbers; decimals are not allowed')
    return number(float(amount), 'Currency amount')


def parse_manual_amount(text, currency=None):
    """Validate entered hundredths before float conversion can erase digits."""
    try:
        amount = Decimal(text)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("Manual asset value must be a decimal number") from error
    if not amount.is_finite() or amount < 0:
        raise ValueError("Manual asset value must be finite and nonnegative")
    if currency is not None:
        parse_currency_amount(text, currency)
    if amount > Decimal(MAX_SHARES) / 100:
        raise ValueError("Manual asset value exceeds the supported monetary range")
    cents = amount * 100
    if cents != cents.to_integral_value():
        raise ValueError("Manual values need at most two decimal places")
    value = float(amount)
    if Decimal(str(value)) != amount:
        raise ValueError("Manual asset value cannot be represented without losing precision")
    return value


@dataclass(frozen=True)
class AssetDetails:
    """Optional instrument information; TER is a percentage, not a fraction."""

    isin: str = ""
    exchange: str = ""
    currency: str = ""
    ter: float | None = None
    name: str = ""
    ibkr_name: str = ""

    def __post_init__(self):
        for name in ("isin", "exchange", "currency", "name", "ibkr_name"):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise ValueError(f"Asset {name} must be text")
            object.__setattr__(self, name, value.strip())
        if self.isin:
            isin = self.isin.upper()
            if not self.isin.isascii() or not _valid_isin(isin):
                raise ValueError("ISIN must have 12 characters and a valid check digit")
            object.__setattr__(self, "isin", isin)
        if self.currency:
            object.__setattr__(self, "currency", currency_code(self.currency))
        if self.ter is not None:
            ter = number(self.ter, "TER percentage", minimum=0)
            if ter > 100:
                raise ValueError("TER percentage cannot exceed 100")
            object.__setattr__(self, "ter", ter)


@dataclass(frozen=True)
class Holding:
    ticker: str
    group: str
    shares: int
    is_main: bool = True
    details: AssetDetails = field(default_factory=AssetDetails)

    def __post_init__(self):
        identifier(self.ticker, "Ticker")
        allocation_name(STOCK_MARKET, self.group)
        if (isinstance(self.shares, bool) or not isinstance(self.shares, Integral)
                or not 0 <= self.shares <= MAX_SHARES):
            raise ValueError(f"Shares must be whole numbers between 0 and {MAX_SHARES}")
        object.__setattr__(self, "shares", int(self.shares))
        if not isinstance(self.is_main, bool):
            raise ValueError("is_main must be a boolean")
        if not isinstance(self.details, AssetDetails):
            raise ValueError("Holding details must be AssetDetails")


@dataclass(frozen=True)
class Quote:
    price: float
    currency: str

    def __post_init__(self):
        price = number(self.price, "Price", minimum=0)
        if price == 0:
            raise ValueError("Price must be positive")
        # Yahoo uses mixed-case minor-unit codes; normalize before uppercasing.
        units = {"GBp": "GBP", "GBX": "GBP", "ZAc": "ZAR", "ILA": "ILS"}
        currency = self.currency
        currency_code(currency)  # Validate before looking up potentially unhashable input.
        if currency in units:
            price /= 100
            currency = units[currency]
        if price == 0:
            raise ValueError("Normalized price must be positive")
        object.__setattr__(self, "price", price)
        object.__setattr__(self, "currency", currency_code(currency))


@dataclass(frozen=True)
class ManualHolding:
    """An entered asset value, traded in hundredths of its currency, without a ticker."""

    amount: float
    currency: str

    def __post_init__(self):
        amount = parse_manual_amount(str(number(self.amount, "Manual asset value", minimum=0)))
        currency = currency_code(self.currency)
        if currency not in ('EUR', 'HUF'):
            raise ValueError("Manual holdings must use EUR or HUF")
        object.__setattr__(self, "amount", amount)
        object.__setattr__(self, "currency", currency)

    @property
    def cents(self):
        return int(Decimal(str(self.amount)) * 100)


@dataclass(frozen=True)
class Portfolio:
    holdings: tuple[Holding, ...]
    allocation: AllocationTargets
    cash: Mapping[str, float]
    manual_holdings: tuple[ManualHolding, ...] = ()

    def __post_init__(self):
        if not isinstance(self.allocation, AllocationTargets):
            raise ValueError("allocation must contain validated allocation targets")
        allocation = self.allocation
        holdings = tuple(sorted(self.holdings, key=lambda item: item.ticker))
        manual = tuple(self.manual_holdings)
        currencies = {holding.currency for holding in manual}
        if len(currencies) != len(manual):
            raise ValueError("MÁK holdings must have unique currencies")
        manual += tuple(ManualHolding(0, currency)
                        for currency in allocation.weights[TREASURY]
                        if currency not in currencies and allocation.weights[TREASURY][currency] > 0)
        if len({holding.ticker for holding in holdings}) != len(holdings):
            raise ValueError("Listed tickers must be unique")
        listed_groups = {holding.group for holding in holdings}
        if listed_groups != allocation.weights[STOCK_MARKET].keys():
            raise ValueError("Stock market targets must describe every listed asset group")
        for group in listed_groups:
            if sum(holding.is_main for holding in holdings if holding.group == group) != 1:
                raise ValueError(f"Group {group!r} must have exactly one main ticker")
        cash = {}
        for key, value in self.cash.items():
            currency = currency_code(key)
            if currency in cash:
                raise ValueError(f"Duplicate cash currency: {currency}")
            cash[currency] = number(value, "Cash")
        object.__setattr__(self, "holdings", holdings)
        object.__setattr__(self, "manual_holdings", tuple(sorted(manual, key=lambda item: item.currency)))
        object.__setattr__(self, "cash", MappingProxyType(cash))

    @property
    def required_holdings(self):
        """Listed instruments that are held or can receive new purchases."""
        targets = self.allocation.weights[STOCK_MARKET]
        return tuple(holding for holding in self.holdings
                     if holding.shares or (holding.is_main and targets[holding.group] > 0))

    @property
    def required_manual_holdings(self):
        """Treasury currencies that are held or can receive new purchases."""
        targets = self.allocation.weights[TREASURY]
        return tuple(holding for holding in self.manual_holdings
                     if holding.amount or targets.get(holding.currency, 0) > 0)

    def required_currencies(self, quotes):
        """Currencies needed to value holdings, fund trades, and distribute cash."""
        currencies = set(self.cash)
        currencies.update(quotes[holding.ticker].currency for holding in self.required_holdings
                          if holding.ticker in quotes)
        currencies.update(holding.currency for holding in self.required_manual_holdings)
        currencies.update(currency for currency, weight in self.allocation.weights[CASH].items()
                          if weight > 0)
        return currencies


@dataclass(frozen=True)
class MarketData:
    quotes: Mapping[str, Quote]
    rates: Mapping[str, float]  # base-currency units per currency unit
    base_currency: str = "EUR"
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self):
        base = currency_code(self.base_currency)
        rates = {}
        for key, value in self.rates.items():
            currency = currency_code(key)
            if currency in rates:
                raise ValueError(f"Duplicate exchange rate: {currency}")
            rate = number(value, "Exchange rate", minimum=0)
            if rate == 0:
                raise ValueError("Exchange rates must be positive")
            rates[currency] = rate
        if base in rates and rates[base] != 1:
            raise ValueError("The base-currency exchange rate must be 1")
        rates[base] = 1.
        quotes = dict(self.quotes)
        for quote in quotes.values():
            if not isinstance(quote, Quote) or quote.currency not in rates:
                raise ValueError("Each quote must have a currency exchange rate")
        object.__setattr__(self, "base_currency", base)
        object.__setattr__(self, "rates", MappingProxyType(rates))
        object.__setattr__(self, "quotes", MappingProxyType(quotes))

    def validate_portfolio(self, portfolio):
        """Reject incomplete coverage before market data is accepted or used."""
        missing_quotes = sorted(holding.ticker for holding in portfolio.required_holdings
                                if holding.ticker not in self.quotes)
        missing_rates = sorted(portfolio.required_currencies(self.quotes) - self.rates.keys())
        errors = []
        if missing_quotes:
            errors.append("Missing quotes for " + ", ".join(missing_quotes))
        if missing_rates:
            errors.append("Missing exchange rate for " + ", ".join(missing_rates))
        if errors:
            raise ValueError("; ".join(errors))
        for holding in portfolio.required_holdings:
            self.price(holding.ticker)

    def value(self, amount, currency):
        currency = currency_code(currency)
        if currency not in self.rates:
            raise ValueError(f"Missing exchange rate for {currency}")
        value = amount * self.rates[currency]
        if not isfinite(value):
            raise ValueError("Converted monetary values must be finite")
        return value

    def price(self, ticker):
        if ticker not in self.quotes:
            raise ValueError(f"Missing quote for {ticker}")
        quote = self.quotes[ticker]
        price = self.value(quote.price, quote.currency)
        if price <= 0:
            raise ValueError(f"Converted price for {ticker} must be positive")
        return price


@dataclass(frozen=True)
class Trade:
    ticker: str
    shares: int  # positive buys, negative sells
    price: float
    currency: str


@dataclass(frozen=True)
class Conversion:
    from_currency: str
    to_currency: str
    from_amount: float
    to_amount: float


@dataclass(frozen=True)
class ManualTrade:
    amount: float  # positive buys, negative sells, in the entered currency
    currency: str


@dataclass(frozen=True)
class Plan:
    trades: tuple[Trade, ...]
    final_shares: Mapping[str, int]
    cash: Mapping[str, float]
    conversions: tuple[Conversion, ...]
    wealth: float
    base_currency: str
    manual_trades: tuple[ManualTrade, ...] = ()
    final_manual_holdings: tuple[ManualHolding, ...] = ()
    allocation_values: Mapping[tuple[str, str], float] = field(default_factory=dict)

    def __post_init__(self):
        for field in ("final_shares", "cash", "allocation_values"):
            object.__setattr__(self, field, MappingProxyType(dict(getattr(self, field))))

    @property
    def cash_value(self):
        return fsum(value for (parent, _), value in self.allocation_values.items() if parent == CASH)


def _valid_isin(value):
    if (len(value) != 12 or not value[:2].isalpha() or not value[:-1].isalnum()
            or not value[-1].isdigit()):
        return False
    digits = ''.join(str(ord(char) - ord('A') + 10) if char.isalpha() else char for char in value)
    total = 0
    for index, char in enumerate(reversed(digits)):
        digit = int(char) * (2 if index % 2 else 1)
        total += digit // 10 + digit % 10
    return total % 10 == 0
