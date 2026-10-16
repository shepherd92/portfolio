"""HUF reserve calculations using Portfolio’s long-term allocation."""

from collections.abc import Mapping
from calendar import monthrange
from dataclasses import dataclass, field, fields, replace
from datetime import date
from decimal import Decimal
from math import exp, expm1, fsum, isfinite, log, log1p
from numbers import Integral

from portfolio.models import AllocationTargets, CASH, STOCK_MARKET, TREASURY, currency_code, number


@dataclass(frozen=True)
class Expense:
    name: str
    amount: float
    due: date
    currency: str = 'HUF'

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError('Each expense needs a name.')
        currency = currency_code(self.currency)
        amount = _currency_amount(self.amount, 'Expense amount', currency)
        if amount == 0:
            raise ValueError('Expense amount must be greater than zero.')
        _validate_date(self.due)
        object.__setattr__(self, 'name', self.name.strip())
        object.__setattr__(self, 'amount', amount)
        object.__setattr__(self, 'currency', currency)


@dataclass(frozen=True)
class MonthlyCost:
    name: str
    amount: float
    currency: str = 'HUF'

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError('Each monthly cost needs a name.')
        currency = currency_code(self.currency)
        amount = _currency_amount(self.amount, 'Monthly cost amount', currency)
        if amount == 0:
            raise ValueError('Monthly cost amount must be greater than zero.')
        object.__setattr__(self, 'name', self.name.strip())
        object.__setattr__(self, 'amount', amount)
        object.__setattr__(self, 'currency', currency)


@dataclass(frozen=True)
class ReturnAssumptions:
    """Illustrative nominal annual percentages; None MÁK means use a fetched rate."""

    equities: float = 6
    gold: float = 2
    bonds: float = 3
    mak: float | None = None

    def __post_init__(self):
        for item in fields(self):
            value = getattr(self, item.name)
            if item.name == 'mak' and value is None:
                continue
            value = _annual_return(value, f'{item.name.capitalize()} expected return')
            object.__setattr__(self, item.name, value)


@dataclass(frozen=True)
class CalculatorSettings:
    monthly_costs: tuple[MonthlyCost, ...] = ()
    monthly_income: float = 0
    income_currency: str = 'HUF'
    emergency_months: int = 6
    cash_months: int = 2
    horizon_years: int = 7
    expenses: tuple[Expense, ...] = ()
    returns: ReturnAssumptions = field(default_factory=ReturnAssumptions)

    def __post_init__(self):
        currency = currency_code(self.income_currency)
        object.__setattr__(self, 'income_currency', currency)
        object.__setattr__(self, 'monthly_income', _currency_amount(self.monthly_income, 'Monthly income', currency))
        for name, minimum, maximum in (('emergency_months', 0, 120), ('cash_months', 0, 120),
                                       ('horizon_years', 1, 100)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, Integral) or not minimum <= value <= maximum:
                raise ValueError(f'{name.replace("_", " ").capitalize()} must be a whole number from {minimum} to {maximum}.')
            object.__setattr__(self, name, int(value))
        for name, kind in (('monthly_costs', MonthlyCost), ('expenses', Expense)):
            entries = getattr(self, name)
            if not isinstance(entries, (tuple, list)) or any(not isinstance(entry, kind) for entry in entries):
                raise ValueError(f'{name.replace("_", " ").capitalize()} must contain validated {kind.__name__} entries.')
            object.__setattr__(self, name, tuple(entries))
        if not isinstance(self.returns, ReturnAssumptions):
            raise ValueError('Expected validated return assumptions.')

    def monthly_spending(self, rates=None):
        try:
            return fsum(huf_value(cost.amount, cost.currency, rates) for cost in self.monthly_costs)
        except OverflowError as error:
            raise ValueError('Monthly spending exceeds the supported numeric range.') from error

    def monthly_savings(self, rates=None):
        income = huf_value(self.monthly_income, self.income_currency, rates)
        return max(0., income - self.monthly_spending(rates))

    @property
    def currencies(self):
        currencies = {'HUF', *(expense.currency for expense in self.expenses),
                      *(cost.currency for cost in self.monthly_costs)}
        if self.monthly_income:
            currencies.add(self.income_currency)
        return frozenset(currencies)


@dataclass(frozen=True)
class AllocationRow:
    name: str
    amount: float
    stock: float
    mak: float
    cash: float = 0
    future_amount: float | None = None
    income_funded: float | None = None


@dataclass(frozen=True)
class Recommendation:
    rows: tuple[AllocationRow, ...]
    market_return: float
    mak_return: float
    shortfall: Decimal = Decimal(0)

    @property
    def total(self):
        return fsum(row.amount for row in self.rows)

    @property
    def stock(self):
        return fsum(row.stock for row in self.rows)

    @property
    def mak(self):
        return fsum(row.mak for row in self.rows)

    @property
    def cash(self):
        return fsum(row.cash for row in self.rows)

    @property
    def stock_percent(self):
        return self.stock / self.total * 100 if self.total else 0.

    @property
    def mak_percent(self):
        return self.mak / self.total * 100 if self.total else 0.

    @property
    def cash_percent(self):
        return self.cash / self.total * 100 if self.total else 0.

    @property
    def expected_return(self):
        return (self.stock_percent / 100 * self.market_return
                + self.mak_percent / 100 * self.mak_return)


def huf_value(amount, currency, rates=None):
    """Convert using current HUF-per-currency rates; zero amounts need no FX quote."""
    amount = number(amount, 'Currency amount', minimum=0)
    currency = currency_code(currency)
    if rates is not None and not isinstance(rates, Mapping):
        raise ValueError('Exchange rates must map currencies to their HUF value.')
    if rates is not None and 'HUF' in rates and number(rates['HUF'], 'HUF exchange rate') != 1:
        raise ValueError('The HUF exchange rate must be 1.')
    if currency == 'HUF':
        return amount
    if not amount:
        return 0.
    if rates is None or currency not in rates:
        raise ValueError(f'Missing exchange rate for {currency}/HUF.')
    rate = number(rates[currency], f'{currency}/HUF exchange rate', minimum=0)
    value = amount * rate
    if not isfinite(value) or value <= 0:
        raise ValueError(f'Converted {currency} amount must have a positive finite HUF value.')
    return value


def return_weights(allocation):
    """Return absolute portfolio target percentages for each return assumption."""
    if not isinstance(allocation, AllocationTargets):
        raise ValueError('Expected validated long-term allocation targets from Portfolio.')
    weights = dict.fromkeys(('Equities', 'Bonds', 'Gold'), 0.)
    for group, weight in allocation.weights[STOCK_MARKET].items():
        if not weight:
            continue
        category = allocation.categories.get(group)
        if category not in weights:
            raise ValueError(f'Categorize {group!r} as Equities, Gold, or Bonds on the Asset classes tab before estimating its return.')
        weights[category] += weight
    return {**weights, TREASURY: allocation.totals[TREASURY]}


def estimate_market_return(returns, market_weights):
    """Weight the stock-market assumptions by their target category amounts."""
    if not isinstance(returns, ReturnAssumptions) or not isinstance(market_weights, Mapping):
        raise ValueError('Provide return assumptions and Stock market category weights.')
    categories = {'Equities': returns.equities, 'Gold': returns.gold, 'Bonds': returns.bonds}
    weights = {name: number(weight, f'{name} weight', minimum=0) for name, weight in market_weights.items()}
    for name, weight in weights.items():
        if weight and name not in categories:
            raise ValueError(f'Categorize {name!r} as Equities, Gold, or Bonds on the Asset classes tab before estimating its return.')
    weights = {name: weight for name, weight in weights.items() if weight}
    scale = max(weights.values(), default=0)
    if not scale:
        return 0.
    total = fsum(weight / scale for weight in weights.values())
    return fsum(weight / scale / total * categories[name] for name, weight in weights.items())


def expense_cash_date(due, cash_months=2):
    """Hold an expense in cash by this month-end-clamped date before it is due."""
    return month_after(due, -max(2, cash_months)) or date.min


def recommend(settings, as_of, allocation, *, total, rates=None):
    """Discount expenses under a declining-stock glidepath, without forecasting FX.

    Analytic integration compounds the arithmetic blended annual return
    as stock weight declines to zero at the expense's cash deadline. Future
    income can fund an expense only by that deadline. Emergency MÁK and the
    separate bank cash buffer are funded today. Years use 365.2425 days.
    """
    if not isinstance(settings, CalculatorSettings):
        raise ValueError('Expected validated calculator settings.')
    if not isinstance(allocation, AllocationTargets):
        raise ValueError('Expected validated long-term allocation targets from Portfolio.')
    _validate_date(as_of)
    total = number(total, 'Portfolio value', minimum=0)
    mak_return = settings.returns.mak
    if mak_return is None:
        raise ValueError('Fetch the MNB base rate or enter a MÁK expected annual return.')
    weights = return_weights(allocation)
    market_weights = {name: weight for name, weight in weights.items() if name != TREASURY}
    market_return = estimate_market_return(settings.returns, market_weights)
    horizon = month_after(as_of, 12 * settings.horizon_years)
    if horizon is None:
        raise ValueError('The horizon extends beyond the supported calendar.')
    days = (horizon - as_of).days
    spending = Decimal(str(settings.monthly_spending(rates)))
    emergency = spending * settings.emergency_months
    cash = spending * settings.cash_months
    remaining = Decimal(str(total)) - emergency - cash
    rows = [AllocationRow('Emergency reserve', float(emergency), 0., float(emergency)),
            AllocationRow('Bank cash buffer', float(cash), 0., 0., float(cash))]
    amounts = [huf_value(expense.amount, expense.currency, rates) for expense in settings.expenses]
    gaps, _ = expense_income(settings.expenses, amounts, settings.monthly_savings(rates), as_of,
                         mak_return, settings.cash_months)
    for expense, future, gap in zip(settings.expenses, amounts, gaps):
        deadline = expense_cash_date(expense.due, settings.cash_months)
        remaining_days = max(0, (deadline - as_of).days)
        fraction = min(1., remaining_days / days)
        amount = expense_reserve(gap, remaining_days, days, market_return, mak_return)
        cash = amount if not remaining_days else 0.
        stock = (amount - cash) * fraction
        rows.append(AllocationRow(expense.name, amount, stock, amount - cash - stock, cash,
                                  future_amount=future, income_funded=future - gap))
        remaining -= Decimal(str(amount))
    shortfall = max(Decimal(0), -remaining)
    if shortfall:
        rows = _fund_rows(rows, settings.expenses, total)
    amount = float(max(Decimal(0), remaining))
    mix = allocation.totals
    stock = amount * (mix[STOCK_MARKET] / 100)
    mak = amount * (mix[TREASURY] / 100)
    cash = amount * (mix[CASH] / 100)
    rows.append(AllocationRow('Long-term allocation', amount, stock, mak, cash))
    result = Recommendation(tuple(rows), market_return, mak_return, shortfall)
    if result.stock and not fsum(market_weights.values()):
        raise ValueError('Set a Stock market mix on Portfolio to invest the stock portion of planned expenses.')
    return result


def expense_reserve(amount, days, horizon_days, market_return, mak_return):
    """Present value along the linear stock glidepath, for validated inputs."""
    if not days or not amount:
        return amount
    years, horizon = days / 365.2425, horizon_days / 365.2425
    duration = min(years, horizon)
    start = _log_gross_blend(market_return, mak_return, 0.)
    end = _log_gross_blend(market_return, mak_return, duration / horizon)
    growth = duration * _mean_log_growth(start, end)
    growth += max(0, years - horizon) * _log_gross_blend(market_return, mak_return, 1.)
    try:
        reserve = amount * exp(-growth) if abs(growth) < 700 else exp(log(amount) - growth)
    except OverflowError as error:
        raise ValueError('Expense projection is outside the supported numeric range; review its date and returns.') from error
    if not isfinite(reserve) or reserve <= 0:
        raise ValueError('Expense projection is outside the supported numeric range; review its date and returns.')
    return reserve


def expense_income(expenses, amounts, savings, as_of, mak_return, cash_months):
    """Return expense gaps and dated income commitments from validated inputs.

    Payments map receipt dates to (expense index, principal) pairs. Each future
    payment is used once, only by expenses whose cash deadline permits it.
    """
    gaps, payments = list(amounts), {}
    if not savings:
        return gaps, payments
    annual_growth = _log_gross_blend(mak_return, mak_return, 0.)
    month, available = 1, savings
    payment_date = month_after(as_of, month)
    for index in sorted(range(len(gaps)), key=lambda index: expenses[index].due):
        deadline = expense_cash_date(expenses[index].due, cash_months)
        while gaps[index] and payment_date is not None and payment_date <= deadline:
            growth = (deadline - payment_date).days / 365.2425 * annual_growth
            gaps[index], principal = _use_payment(gaps[index], available, growth)
            available = max(0., available - principal)
            if principal:
                payments.setdefault(payment_date, []).append((index, principal))
            if not available:
                month += 1
                payment_date = month_after(as_of, month)
                available = savings
    return gaps, payments


def month_after(value, months):
    """Shift a calendar date by whole months, clamping its day; None outside dates."""
    year, month = divmod(value.year * 12 + value.month - 1 + months, 12)
    if not 1 <= year <= 9999:
        return None
    month += 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def _validate_date(value):
    if type(value) is not date:
        raise ValueError('Use a calendar date without a time.')


def _currency_amount(value, name, currency):
    amount = number(value, name, minimum=0)
    exact = Decimal(str(value))
    if currency == 'HUF' and exact != exact.to_integral_value():
        raise ValueError(f'{name} must be a whole number of HUF.')
    return amount


def _annual_return(value, name):
    value = number(value, name)
    if value <= -100:
        raise ValueError('Expected annual returns must be greater than -100%.')
    return value


def _use_payment(gap, available, growth):
    """Return unpaid expense and committed principal without unbounded intermediates."""
    factor = exp(-abs(growth))
    if growth >= 0:
        needed = gap * factor
        if not needed:
            raise ValueError('Income projection is outside the supported numeric range; review its date and returns.')
        if available >= needed:
            return 0., needed
        return max(0., gap - available / factor), available
    funded = available * factor
    if funded >= gap:
        return 0., gap / factor
    return gap - funded, available


def _log_gross_blend(market_return, mak_return, fraction):
    """Log of one plus the arithmetic annual return, without large products."""
    blended = (1 - fraction) * (mak_return / 100) + fraction * (market_return / 100)
    if blended > -.5:
        return log1p(blended)
    # Add 100 before division to retain precision for rates close to -100%.
    gross = (1 - fraction) * ((100 + mak_return) / 100) + fraction * ((100 + market_return) / 100)
    return log(gross)


def _mean_log_growth(start, end):
    """Exact average log growth along a linear path between gross returns.

    Given endpoint logarithms, the integral is high - 1 + d / expm1(d).
    Its symmetric series avoids cancellation when the endpoints are close.
    """
    high, low = max(start, end), min(start, end)
    difference = high - low
    if difference < .01:
        square = difference * difference
        return (high + low) / 2 + square * (1 / 12 + square * (-1 / 720 + square / 30240))
    correction = difference / expm1(difference) if difference < 700 else 0.
    return high - 1 + correction


def _fund_rows(rows, expenses, total):
    """Protect bank/expense cash, then emergency/expense MÁK, then stocks."""
    order = [0, 1, *sorted(range(2, len(rows)), key=lambda index: expenses[index - 2].due)]
    funded = [{'cash': Decimal(0), 'mak': Decimal(0), 'stock': Decimal(0)} for row in rows]
    available = Decimal(str(total))
    for component in ('cash', 'mak', 'stock'):
        for index in order:
            needed = Decimal(str(getattr(rows[index], component)))
            value = min(needed, available)
            funded[index][component] = value
            available -= value
    return [replace(row, amount=float(sum(values.values())),
                    **{name: float(value) for name, value in values.items()})
            for row, values in zip(rows, funded)]
