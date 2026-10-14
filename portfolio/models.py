"""Immutable inputs and results. All monetary values use explicit currencies."""

from dataclasses import dataclass, field
from datetime import datetime
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


def number(value, name, *, minimum=None):
    if isinstance(value, bool) or not isinstance(value, Real):
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


@dataclass(frozen=True)
class AllocationTargets:
    """Percentages of total wealth and percentages within each allocation branch."""

    top: Mapping[str, float]
    children: Mapping[str, Mapping[str, float]]

    def __post_init__(self):
        top = _percentages(self.top, "Target")
        if set(top) != set(ALLOCATION_BRANCHES):
            raise ValueError("Top-level targets must describe Stock market, MÁK, and Cash")
        if not isclose(fsum(top.values()), 100, abs_tol=1e-8, rel_tol=0):
            raise ValueError(_total_error('Portfolio targets', fsum(top.values())))
        if set(self.children) != set(ALLOCATION_BRANCHES):
            raise ValueError("Child targets must describe every allocation branch")
        children = {}
        for branch in ALLOCATION_BRANCHES:
            normalize = currency_code if branch != STOCK_MARKET else lambda value: identifier(value, "Group")
            values = _percentages(self.children[branch], "Child target", normalize)
            if branch == TREASURY and not set(values) <= {"EUR", "HUF"}:
                raise ValueError("MÁK targets must use EUR or HUF")
            total = fsum(values.values())
            valid_total = isclose(total, 100, abs_tol=1e-8, rel_tol=0)
            if not valid_total and not (top[branch] == 0 and total == 0):
                raise ValueError(_total_error(f'{branch} targets', total))
            children[branch] = MappingProxyType(values)
        object.__setattr__(self, "top", MappingProxyType({branch: top[branch] for branch in ALLOCATION_BRANCHES}))
        object.__setattr__(self, "children", MappingProxyType(children))

    @property
    def effective(self):
        return MappingProxyType({(branch, child): self.top[branch] * share / 100
                                 for branch, children in self.children.items()
                                 for child, share in children.items()})


def parse_manual_amount(text):
    """Validate entered hundredths before float conversion can erase digits."""
    try:
        amount = Decimal(text)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("Manual asset value must be a decimal number") from error
    if not amount.is_finite() or amount < 0:
        raise ValueError("Manual asset value must be finite and nonnegative")
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
class Holding:
    ticker: str
    group: str
    shares: int
    is_main: bool = True

    def __post_init__(self):
        identifier(self.ticker, "Ticker")
        identifier(self.group, "Group")
        if self.group == CASH:
            raise ValueError("Cash is reserved for currency balances")
        if (isinstance(self.shares, bool) or not isinstance(self.shares, Integral)
                or not 0 <= self.shares <= MAX_SHARES):
            raise ValueError(f"Shares must be whole numbers between 0 and {MAX_SHARES}")
        object.__setattr__(self, "shares", int(self.shares))
        if not isinstance(self.is_main, bool):
            raise ValueError("is_main must be a boolean")


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
                        for currency in allocation.children[TREASURY]
                        if currency not in currencies and allocation.effective[TREASURY, currency] > 0)
        if len({holding.ticker for holding in holdings}) != len(holdings):
            raise ValueError("Listed tickers must be unique")
        listed_groups = {holding.group for holding in holdings}
        if listed_groups != allocation.children[STOCK_MARKET].keys():
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
        object.__setattr__(self, "allocation", allocation)
        object.__setattr__(self, "manual_holdings", tuple(sorted(manual, key=lambda item: item.currency)))
        object.__setattr__(self, "cash", MappingProxyType(cash))

    @property
    def required_holdings(self):
        """Listed instruments that are held or can receive new purchases."""
        targets = self.allocation.effective
        return tuple(holding for holding in self.holdings
                     if holding.shares or (holding.is_main and targets[STOCK_MARKET, holding.group] > 0))

    @property
    def required_manual_holdings(self):
        """Treasury currencies that are held or can receive new purchases."""
        targets = self.allocation.effective
        return tuple(holding for holding in self.manual_holdings
                     if holding.amount or targets.get((TREASURY, holding.currency), 0) > 0)

    def required_currencies(self, quotes):
        """Currencies needed to value holdings, fund trades, and distribute cash."""
        currencies = set(self.cash)
        currencies.update(quotes[holding.ticker].currency for holding in self.required_holdings
                          if holding.ticker in quotes)
        currencies.update(holding.currency for holding in self.required_manual_holdings)
        # Cash preferences still distribute residual cash when the parent target is zero.
        currencies.update(currency for currency, weight in self.allocation.children[CASH].items()
                          if weight > 0)
        return currencies


@dataclass(frozen=True)
class MarketSnapshot:
    quotes: Mapping[str, Quote]
    rates: Mapping[str, float]  # base-currency units per currency unit
    base_currency: str = "EUR"
    fetched_at: str | None = None  # Acquisition time, not the market's quote timestamp.

    def __post_init__(self):
        if self.fetched_at is not None:
            try:
                acquired = datetime.fromisoformat(self.fetched_at.replace("Z", "+00:00"))
                if acquired.utcoffset() is None:
                    raise ValueError
            except (AttributeError, TypeError, ValueError) as error:
                raise ValueError("Snapshot acquisition time must be a timezone-aware ISO timestamp") from error
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
        """Reject incomplete coverage before a snapshot is accepted or used."""
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


def _percentages(values, name, normalize=lambda value: identifier(value, "Group")):
    result = {}
    for key, value in values.items():
        key = normalize(key)
        if key in result:
            raise ValueError(f"Duplicate {name}: {key}")
        result[key] = number(value, name, minimum=0)
        if result[key] > 100:
            raise ValueError("Target percentages cannot exceed 100")
    return result


def _total_error(name, total):
    change = f'add {100 - total:g}%' if total < 100 else f'reduce by {total - 100:g}%'
    return f'{name} total {total:g}%: {change}. Target percentages must sum to 100.'
