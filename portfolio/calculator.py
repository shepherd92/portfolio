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
class CashFlow:
    """A single payment or a recurring schedule, both specified by calendar month."""
    name: str
    amount: float
    start: date
    currency: str = 'HUF'
    frequency: str = 'once'
    count: int | None = None
    end: date | None = None

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError('Each cash flow needs a name.')
        currency = currency_code(self.currency)
        amount = _currency_amount(self.amount, 'Payment amount', currency)
        if amount == 0:
            raise ValueError('Payment amount must be greater than zero.')
        _validate_date(self.start)
        if self.end is not None:
            _validate_date(self.end)
        if self.frequency not in ('once', 'monthly', 'annual'):
            raise ValueError('Payment frequency must be once, monthly, or annual.')
        if self.frequency == 'once' and (self.count is not None or self.end is not None):
            raise ValueError('A single payment cannot have an end month or payment count.')
        object.__setattr__(self, 'start', self.start.replace(day=1))
        if self.end is not None:
            object.__setattr__(self, 'end', self.end.replace(day=1))
        if self.count is not None and self.end is not None:
            raise ValueError('Choose either an end month or a payment count.')
        if self.count is not None:
            if isinstance(self.count, bool) or not isinstance(self.count, Integral) or not 1 <= self.count <= 1200:
                raise ValueError('Payment count must be a whole number from 1 to 1,200.')
            object.__setattr__(self, 'count', int(self.count))
            if month_after(self.start, self.interval_months * (self.count - 1)) is None:
                raise ValueError('The payment schedule extends beyond the supported calendar.')
        if self.end is not None:
            if self.end < self.start:
                raise ValueError('The end month cannot precede the first payment month.')
            if self.payment_count > 1200:
                raise ValueError('A finite payment schedule can contain at most 1,200 payments.')
        object.__setattr__(self, 'name', self.name.strip())
        object.__setattr__(self, 'amount', amount)
        object.__setattr__(self, 'currency', currency)

    @property
    def interval_months(self):
        return 12 if self.frequency == 'annual' else 1

    @property
    def ongoing(self):
        return self.frequency != 'once' and self.count is None and self.end is None

    @property
    def payment_count(self):
        if self.frequency == 'once':
            return 1
        if self.count is not None or self.end is None:
            return self.count
        months = (self.end.year - self.start.year) * 12 + self.end.month - self.start.month
        return months // self.interval_months + 1

    def dates(self, start, end):
        """Yield payment dates in an inclusive interval; all payments use day one."""
        _validate_date(start)
        _validate_date(end)
        interval = self.interval_months
        months = (start.year - self.start.year) * 12 + start.month - self.start.month
        first = max(0, (months + interval - 1) // interval)
        last = ((end.year - self.start.year) * 12 + end.month - self.start.month) // interval
        if self.payment_count is not None:
            last = min(last, self.payment_count - 1)
        for occurrence in range(first, last + 1):
            due = month_after(self.start, occurrence * interval)
            if start <= due <= end:
                yield due


@dataclass(frozen=True)
class Payment:
    name: str
    amount: float
    due: date
    currency: str
    outgoing_index: int
    cash_due: date | None = None
    defensive: bool = False


@dataclass(frozen=True)
class IncomeBudget:
    """One future receipt month's HUF budget before expense commitments."""
    day: date
    gross_income: float
    living_costs: float
    surplus: float
    linked_income: tuple[tuple[str, float], ...] = ()
    obligations: tuple[tuple[str, float, float], ...] = ()


@dataclass(frozen=True)
class MonthlyTotals:
    recurring_income: float
    single_income: float
    recurring_outgoings: float
    single_outgoings: float

    @property
    def income(self):
        return self.recurring_income + self.single_income

    @property
    def outgoings(self):
        return self.recurring_outgoings + self.single_outgoings

    @property
    def net(self):
        return self.income - self.outgoings

    @property
    def saving_percent(self):
        """Selected-month recurring surplus as a percentage of recurring income."""
        if not self.recurring_income:
            return None
        return (self.recurring_income - self.recurring_outgoings) / self.recurring_income * 100


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
    incomes: tuple[CashFlow, ...] = ()
    outgoings: tuple[CashFlow, ...] = ()
    emergency_months: int = 6
    cash_months: int = 2
    horizon_years: int = 7
    returns: ReturnAssumptions = field(default_factory=ReturnAssumptions)

    def __post_init__(self):
        for name, minimum, maximum in (('emergency_months', 0, 120), ('cash_months', 0, 120),
                                       ('horizon_years', 1, 100)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, Integral) or not minimum <= value <= maximum:
                raise ValueError(f'{name.replace("_", " ").capitalize()} must be a whole number from {minimum} to {maximum}.')
            object.__setattr__(self, name, int(value))
        for name in ('incomes', 'outgoings'):
            entries = getattr(self, name)
            if not isinstance(entries, (tuple, list)) or any(not isinstance(entry, CashFlow) for entry in entries):
                raise ValueError(f'{name.capitalize()} must contain validated CashFlow entries.')
            object.__setattr__(self, name, tuple(entries))
        if not isinstance(self.returns, ReturnAssumptions):
            raise ValueError('Expected validated return assumptions.')

    def monthly_spending(self, rates=None, *, as_of=None):
        as_of = date.today() if as_of is None else as_of
        _validate_date(as_of)
        try:
            return fsum(huf_value(outgoing.amount, outgoing.currency, rates) / outgoing.interval_months
                        for outgoing in self.outgoings if outgoing.ongoing and outgoing.start <= as_of)
        except OverflowError as error:
            raise ValueError('Monthly spending exceeds the supported numeric range.') from error

    def monthly_totals(self, year, month, rates=None):
        """Actual scheduled income and outgoings in a selected calendar month."""
        start, end = date(year, month, 1), date(year, month, monthrange(year, month)[1])
        values = []
        try:
            for entries in (self.incomes, self.outgoings):
                for recurring in (True, False):
                    values.append(fsum(huf_value(entry.amount, entry.currency, rates) for entry in entries
                                       if (entry.frequency != 'once') == recurring and any(entry.dates(start, end))))
        except OverflowError as error:
            raise ValueError('Monthly cash flows exceed the supported numeric range.') from error
        return MonthlyTotals(*values)

    def income_receipts(self, as_of, end, rates=None):
        """Investable net income at the beginning of future calendar months.

        Each month's receipts first cover that month's ongoing cost budget,
        with annual ongoing costs spread over 12 months. All flows use the first
        of the month; current-month income is already in the recorded portfolio.
        Monthly deficits create no withdrawals. Finite outgoings are funded
        separately by their deadlines.
        """
        return tuple((budget.day, budget.surplus) for budget in self.income_budgets(as_of, end, rates)
                     if budget.surplus)

    def income_budgets(self, as_of, end, rates=None):
        """Retain the actual income and living-cost deductions, including zero savings."""
        _validate_date(as_of)
        _validate_date(end)
        gross = gross_income_by_date(self.incomes, as_of, end, rates)
        budgets = []
        try:
            for day, values in sorted(gross.items()):
                income = fsum(values)
                living = self.monthly_spending(rates, as_of=day)
                budgets.append(IncomeBudget(day, income, living, max(0., income - living)))
        except OverflowError as error:
            raise ValueError('Income receipts exceed the supported numeric range.') from error
        return tuple(budgets)

    def scheduled_payments(self, as_of):
        """Expand all unpaid finite commitments; past recurring payments are paid."""
        _validate_date(as_of)
        payments = []
        for index, outgoing in enumerate(self.outgoings):
            if outgoing.ongoing:
                continue
            for month in range(outgoing.payment_count):
                due = month_after(outgoing.start, month * outgoing.interval_months)
                if outgoing.frequency == 'once' or due >= as_of:
                    payments.append(Payment(outgoing.name, outgoing.amount, due, outgoing.currency, index))
        return tuple(payments)

    def buffer_increases(self, as_of, rates=None):
        """Future ongoing costs create new fixed buffers when their payments start."""
        _validate_date(as_of)
        months = self.emergency_months + self.cash_months
        return tuple(sorted((outgoing.start, huf_value(outgoing.amount, outgoing.currency, rates) / outgoing.interval_months * months)
                            for outgoing in self.outgoings if outgoing.ongoing and outgoing.start > as_of))

    @property
    def currencies(self):
        return frozenset({'HUF', *(entry.currency for entry in (*self.incomes, *self.outgoings))})


@dataclass(frozen=True)
class AllocationRow:
    name: str
    amount: float
    stock: float
    mak: float
    cash: float = 0
    future_amount: float | None = None
    income_funded: float | None = None
    outgoing_index: int | None = None
    due: date | None = None


@dataclass(frozen=True)
class ExpenseFunding:
    """Calculation evidence before any shortage caps today's funded allocation."""
    payment: Payment
    deadline: date
    full_reserve: float
    stock_fraction: float
    income_capacity: float
    income_funded: float
    released_reserve: float
    contributions: tuple[tuple[date, float], ...]


@dataclass(frozen=True)
class Recommendation:
    rows: tuple[AllocationRow, ...]
    market_return: float
    mak_return: float
    shortfall: Decimal = Decimal(0)
    expenses: tuple[ExpenseFunding, ...] = ()
    receipts: tuple[tuple[date, float], ...] = ()
    buffer_increases: tuple[tuple[date, float], ...] = ()
    income_budgets: tuple[IncomeBudget, ...] = ()

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


def gross_income_by_date(incomes, as_of, end, rates=None):
    """Group scheduled future income in HUF before any spending is deducted."""
    gross = {}
    for income in incomes:
        amount = huf_value(income.amount, income.currency, rates)
        for day in income.dates(as_of, end):
            if day > as_of:
                gross.setdefault(day, []).append(amount)
    return gross


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


def payment_cash_date(payment, cash_months):
    """Scheduled purchases need advance cash; operating shortfalls are due when paid."""
    return payment.cash_due or expense_cash_date(payment.due, cash_months)


def recommend(settings, as_of, allocation, *, total, rates=None):
    """Discount expenses under a declining-stock glidepath, without forecasting FX.

    Analytic integration compounds the arithmetic blended annual return
    as stock weight declines to zero at the expense's cash deadline. Future
    income can replace only its defensive reserve, preserving the stock amount
    calculated for the full expense. Emergency MÁK and the
    separate bank cash buffer are funded today. Years use 365.2425 days.
    """
    if not isinstance(settings, CalculatorSettings):
        raise ValueError('Expected validated calculator settings.')
    if not isinstance(allocation, AllocationTargets):
        raise ValueError('Expected validated long-term allocation targets from Portfolio.')
    _validate_date(as_of)
    if hasattr(settings, 'wealth'):
        settings.validate_context(as_of, rates)
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
    spending = Decimal(str(settings.monthly_spending(rates, as_of=as_of)))
    emergency = spending * settings.emergency_months
    cash = spending * settings.cash_months
    remaining = Decimal(str(total)) - emergency - cash
    rows = [AllocationRow('Emergency reserve', float(emergency), 0., float(emergency)),
            AllocationRow('Bank cash buffer', float(cash), 0., 0., float(cash))]
    expenses = settings.scheduled_payments(as_of)
    amounts = [huf_value(expense.amount, expense.currency, rates) for expense in expenses]
    last_deadline = max((payment_cash_date(expense, settings.cash_months) for expense in expenses), default=as_of)
    income_budgets = settings.income_budgets(as_of, last_deadline, rates)
    receipts = tuple((budget.day, budget.surplus) for budget in income_budgets if budget.surplus)
    capacities = [expense_income_capacity(amount, max(0, (payment_cash_date(expense, settings.cash_months) - as_of).days),
                                         days, mak_return if expense.defensive else market_return, mak_return,
                                         defensive=expense.defensive)
                  for expense, amount in zip(expenses, amounts)]
    buffer_increases = settings.buffer_increases(as_of, rates)
    gaps, payments = expense_income(expenses, capacities, receipts, mak_return, settings.cash_months,
                                    buffer_increases=buffer_increases)
    contributions = [[] for expense in expenses]
    for day, assignments in payments.items():
        for index, principal in assignments:
            contributions[index].append((day, principal))
    calculations = []
    for index, (expense, future, capacity, gap) in enumerate(zip(expenses, amounts, capacities, gaps)):
        deadline = payment_cash_date(expense, settings.cash_months)
        remaining_days = max(0, (deadline - as_of).days)
        fraction = 0. if expense.defensive else min(1., remaining_days / days)
        full_reserve = expense_reserve(future, remaining_days, days,
                                       mak_return if expense.defensive else market_return, mak_return)
        funded = capacity - gap
        released = expense_reserve(funded, remaining_days, days, mak_return, mak_return)
        stock = full_reserve * fraction
        defensive = max(0., full_reserve - stock - released)
        cash = defensive if not remaining_days else 0.
        amount = max(stock, full_reserve - released)
        rows.append(AllocationRow(expense.name, amount, stock, defensive - cash, cash,
                                  future_amount=future, income_funded=funded,
                                  outgoing_index=expense.outgoing_index, due=expense.due))
        calculations.append(ExpenseFunding(expense, deadline, full_reserve, fraction, capacity,
                                           funded, released, tuple(contributions[index])))
        remaining -= Decimal(str(amount))
    shortfall = max(Decimal(0), -remaining)
    if shortfall:
        rows = _fund_rows(rows, expenses, total, settings.cash_months)
    amount = float(max(Decimal(0), remaining))
    mix = allocation.totals
    stock = amount * (mix[STOCK_MARKET] / 100)
    mak = amount * (mix[TREASURY] / 100)
    cash = amount * (mix[CASH] / 100)
    rows.append(AllocationRow('Long-term allocation', amount, stock, mak, cash))
    result = Recommendation(tuple(rows), market_return, mak_return, shortfall,
                            tuple(calculations), tuple(receipts), tuple(buffer_increases), income_budgets)
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


def expense_income_capacity(amount, days, horizon_days, market_return, mak_return, *, defensive=False):
    """Maximum future MÁK value that can replace today's defensive reserve.

    Keep the full expense's stock glidepath intact. The second bound protects its
    defensive balance throughout that path even when stocks return less than MÁK;
    for higher stock returns, today's defensive reserve is the binding limit.
    """
    fraction = 0. if defensive else min(1., days / horizon_days)
    if not amount or fraction == 1:
        return 0.
    reserve = expense_reserve(amount, days, horizon_days, market_return, mak_return)
    growth = _log_gross_blend(mak_return, mak_return, 0.) * days / 365.2425
    return amount * (1 - fraction) * exp(min(0., log(reserve) - log(amount) + growth))


def expense_income(expenses, amounts, receipts, mak_return, cash_months, *, buffer_increases=()):
    """Return unfilled defensive capacities and dated income commitments.

    Payments map receipt dates to (expense index, principal) pairs. Each future
    payment is used once, only by expenses whose cash deadline permits it.
    Receipts are chronological (date, investable net amount) pairs.
    New fixed buffers take priority over expense commitments as ongoing costs start.
    """
    gaps, payments = list(amounts), {}
    if not receipts:
        return gaps, payments
    annual_growth = _log_gross_blend(mak_return, mak_return, 0.)
    receipts = iter(receipts)
    increases = iter(buffer_increases)
    increase = next(increases, None)
    buffer_gap = 0.

    def next_receipt():
        nonlocal increase, buffer_gap
        receipt = next(receipts, None)
        if receipt is None:
            return None, 0.
        day, income = receipt
        while increase is not None and increase[0] <= day:
            buffer_gap += increase[1]
            increase = next(increases, None)
        reserved = min(income, buffer_gap)
        buffer_gap -= reserved
        return day, income - reserved

    payment_date, available = next_receipt()
    for index in sorted(range(len(gaps)), key=lambda index: payment_cash_date(expenses[index], cash_months)):
        deadline = payment_cash_date(expenses[index], cash_months)
        while gaps[index] and payment_date is not None and payment_date <= deadline:
            if available:
                growth = (deadline - payment_date).days / 365.2425 * annual_growth
                gaps[index], principal = _use_payment(gaps[index], available, growth)
                available = max(0., available - principal)
                if principal:
                    payments.setdefault(payment_date, []).append((index, principal))
            if not available:
                payment_date, available = next_receipt()
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


def _fund_rows(rows, expenses, total, cash_months=2):
    """Protect bank/expense cash, then emergency/expense MÁK, then stocks."""
    order = [0, 1, *sorted(range(2, len(rows)),
                          key=lambda index: payment_cash_date(expenses[index - 2], cash_months))]
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
