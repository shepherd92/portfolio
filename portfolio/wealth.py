"""Assets outside the trading portfolio, their liabilities and linked cash movements.

Every entered value is the user's own share as of today. All current-month
payments are already reflected in those values and the recorded bank balance.
Asset growth changes valuations; only explicit payments create cash events.
"""

from dataclasses import dataclass
from datetime import date
from math import exp, log

from portfolio.calculator import CashFlow, month_after
from portfolio.models import currency_code, identifier, number, parse_currency_amount


@dataclass(frozen=True)
class PropertyCashFlow:
    """A scheduled owner payment, weighted by expected rental occupancy.

    The amount applies in the schedule's beginning month. Its annual
    appreciation compounds monthly from there, independently of property value.
    """
    flow: CashFlow
    direction: str = 'outgoing'
    occupancy: str = 'always'
    appreciation_percent: float = 0

    def __post_init__(self):
        _flow(self.flow)
        if self.flow.appreciation_percent:
            raise ValueError('Set property cash-flow appreciation on the property payment, not its nested schedule.')
        if self.direction not in ('income', 'outgoing'):
            raise ValueError('Property cash-flow direction must be income or outgoing.')
        if self.occupancy not in ('always', 'rented', 'vacant'):
            raise ValueError('Property cash-flow occupancy must be always, rented, or vacant.')
        _percent(self, 'appreciation_percent', -99.99, 1000)


@dataclass(frozen=True)
class Property:
    id: str
    name: str
    value: float
    currency: str = 'HUF'
    appreciation_percent: float = 0
    depreciation_percent: float = 0
    vacancy_percent: float = 0
    cashflows: tuple[PropertyCashFlow, ...] = ()

    def __post_init__(self):
        _identity(self)
        _amount(self, 'value')
        _percent(self, 'appreciation_percent', -99.99, 1000)
        _percent(self, 'depreciation_percent', 0, 100)
        _percent(self, 'vacancy_percent', 0, 100)
        _entries(self, 'cashflows', PropertyCashFlow)

    @property
    def annual_factor(self):
        """Appreciation and physical depreciation compound, without cash flows."""
        return (1 + self.appreciation_percent / 100) * (1 - self.depreciation_percent / 100)


@dataclass(frozen=True)
class PensionContribution:
    flow: CashFlow
    source: str = 'bank'

    def __post_init__(self):
        _flow(self.flow)
        if self.source not in ('bank', 'payroll', 'employer'):
            raise ValueError('Pension contribution source must be bank, payroll, or employer.')


@dataclass(frozen=True)
class Pension:
    id: str
    name: str
    currency: str = 'HUF'
    kind: str = 'balance'
    balance: float = 0
    annual_return: float = 0
    access: date | None = None
    contributions: tuple[PensionContribution, ...] = ()
    withdrawals: tuple[CashFlow, ...] = ()

    def __post_init__(self):
        _identity(self)
        _amount(self, 'balance')
        _percent(self, 'annual_return', -99.99, 1000)
        _entries(self, 'contributions', PensionContribution)
        _entries(self, 'withdrawals', CashFlow)
        if self.kind not in ('balance', 'income'):
            raise ValueError('Pension kind must be balance or income.')
        if self.access is not None:
            object.__setattr__(self, 'access', _month(self.access))
        if self.kind == 'balance' and self.access is None:
            raise ValueError('A pension balance needs an earliest access month.')
        if self.kind == 'income' and (self.balance or self.annual_return):
            raise ValueError('An income pension has no investment balance or return.')
        for flow in (*self.withdrawals, *(entry.flow for entry in self.contributions)):
            _flow(flow, self.currency)
        if self.access and any(flow.start < self.access for flow in self.withdrawals):
            raise ValueError('Pension withdrawals cannot begin before the access month.')


@dataclass(frozen=True)
class Loan:
    id: str
    name: str
    balance: float
    payment: CashFlow | None = None
    currency: str = 'HUF'
    annual_interest: float = 0
    property_id: str | None = None

    def __post_init__(self):
        _identity(self)
        _amount(self, 'balance')
        _percent(self, 'annual_interest', 0, 1000)
        if self.payment is not None:
            _flow(self.payment, self.currency)
        if self.property_id is not None:
            identifier(self.property_id, 'Linked property ID')


@dataclass(frozen=True)
class CashEvent:
    """A generated payment: fractions of HUF are valid internal calculations."""
    source_id: str
    source: str
    name: str
    due: date
    amount: float
    currency: str
    direction: str
    recurring: bool
    ongoing: bool
    essential: bool
    principal: float = 0
    interest: float = 0


@dataclass(frozen=True)
class WealthBalance:
    source_id: str
    name: str
    category: str
    amount: float
    currency: str


@dataclass(frozen=True)
class WealthSnapshot:
    day: date
    balances: tuple[WealthBalance, ...]
    before_payments: bool = False


@dataclass(frozen=True)
class WealthProjection:
    events: tuple[CashEvent, ...]
    snapshots: tuple[WealthSnapshot, ...]

    @property
    def balances(self):
        return self.snapshots[-1].balances


@dataclass(frozen=True)
class WealthSettings:
    properties: tuple[Property, ...] = ()
    pensions: tuple[Pension, ...] = ()
    loans: tuple[Loan, ...] = ()

    def __post_init__(self):
        for name, model in (('properties', Property), ('pensions', Pension), ('loans', Loan)):
            _entries(self, name, model)
            names = [entry.name.casefold() for entry in getattr(self, name)]
            if len(set(names)) != len(names):
                raise ValueError(f'{name.capitalize()} must have distinct names.')
        entries = (*self.properties, *self.pensions, *self.loans)
        if len({entry.id for entry in entries}) != len(entries):
            raise ValueError('Every property, pension, and loan needs a unique ID.')
        property_ids = {entry.id for entry in self.properties}
        if any(loan.property_id is not None and loan.property_id not in property_ids for loan in self.loans):
            raise ValueError('A loan refers to a missing property.')

    @property
    def currencies(self):
        values = {entry.currency for entry in (*self.properties, *self.pensions, *self.loans)}
        for item in self.properties:
            values.update(entry.flow.currency for entry in item.cashflows)
        return frozenset(values)

    def current_balances(self):
        return _balances(self, {item.id: item.value for item in self.properties},
                         {item.id: item.balance for item in self.pensions},
                         {item.id: item.balance for item in self.loans})

    def events(self, as_of, end):
        return self.project(as_of, end).events

    def project(self, as_of, end):
        """Project up to 100 years, at fixed currencies and expected vacancy.

        Monthly dates use day one. The current month is already recorded, so
        even a current-month Once payment is not repeated. Annual returns are
        effective rates using elapsed days / 365.25. Withdrawals cannot exceed
        a pension balance; repayments cannot exceed the accrued loan debt.
        Property payments grow from their beginning month by elapsed calendar
        months / 12, then use the property's expected occupancy fraction.
        """
        _range(as_of, end)
        properties = {item.id: item.value for item in self.properties}
        pensions = {item.id: item.balance for item in self.pensions}
        loans = {item.id: item.balance for item in self.loans}
        interest_due = dict.fromkeys(loans, 0.)
        schedules = _schedules(self, as_of, end)
        dates = {end, *schedules}
        month = month_after(as_of.replace(day=1), 1)
        while month is not None and month <= end:
            dates.add(month)
            month = month_after(month, 1)
        events = []
        snapshots = [WealthSnapshot(as_of, self.current_balances())]
        previous = as_of
        for day in sorted(dates - {as_of}):
            elapsed = (day - previous).days / 365.25
            for item in self.properties:
                properties[item.id] = _grow(properties[item.id], item.annual_factor, elapsed)
            for item in self.pensions:
                pensions[item.id] = _grow(pensions[item.id], 1 + item.annual_return / 100, elapsed)
            for item in self.loans:
                grown = _grow(loans[item.id], 1 + item.annual_interest / 100, elapsed)
                interest_due[item.id] = number(interest_due[item.id] + grown - loans[item.id], 'Loan interest')
                loans[item.id] = grown
            if day in schedules:
                snapshots.append(WealthSnapshot(day, _balances(self, properties, pensions, loans), True))
            for item, flow, role in schedules.get(day, ()):
                event = _pay(item, flow, role, day, pensions, loans, interest_due)
                if event is not None:
                    events.append(event)
            snapshots.append(WealthSnapshot(day, _balances(self, properties, pensions, loans)))
            previous = day
        return WealthProjection(tuple(events), tuple(snapshots))


def _identity(item):
    identifier(item.id, 'ID')
    identifier(item.name, 'Name')
    object.__setattr__(item, 'currency', currency_code(item.currency))


def _amount(item, name):
    value = number(getattr(item, name), name.capitalize(), minimum=0)
    parse_currency_amount(str(getattr(item, name)), item.currency)
    object.__setattr__(item, name, value)


def _percent(item, name, low, high):
    value = number(getattr(item, name), name.replace('_', ' ').capitalize())
    if not low <= value <= high:
        raise ValueError(f'{name.replace("_", " ").capitalize()} must be between {low} and {high}%.')
    object.__setattr__(item, name, value)


def _entries(item, name, model):
    values = getattr(item, name)
    if not isinstance(values, (list, tuple)) or any(not isinstance(value, model) for value in values):
        raise ValueError(f'{name.capitalize()} must contain validated {model.__name__} entries.')
    object.__setattr__(item, name, tuple(values))


def _flow(flow, currency=None):
    if not isinstance(flow, CashFlow):
        raise ValueError('Expected a validated payment schedule.')
    if currency is not None and flow.currency != currency:
        raise ValueError('Payment currency must match its pension or loan currency.')


def _month(value):
    if type(value) is not date:
        raise ValueError('Enter a valid calendar month.')
    return value.replace(day=1)


def _range(start, end):
    _month(start)
    _month(end)
    if end < start or (end - start).days > 36525:
        raise ValueError('Choose a projection end from today through 100 years ahead.')


def _grow(value, factor, years):
    if not value or not years:
        return value
    if not factor:
        return 0.
    try:
        return number(value * exp(log(factor) * years), 'Projected balance', minimum=0)
    except OverflowError as error:
        raise ValueError('Projected balance exceeds the supported numeric range.') from error


def _schedules(settings, start, end):
    schedules = {}

    def add(item, flow, role):
        if flow is not None:
            for day in flow.dates(start, end):
                if day > start:
                    schedules.setdefault(day, []).append((item, flow, role))

    for item in settings.properties:
        for entry in item.cashflows:
            add(item, entry.flow, entry)
    for item in settings.pensions:
        for contribution in item.contributions:
            add(item, contribution.flow, contribution.source)
        for flow in item.withdrawals:
            add(item, flow, 'withdrawal')
    for item in settings.loans:
        add(item, item.payment, 'repayment')
    return schedules


def _pay(item, flow, role, day, pensions, loans, interest_due):
    amount, principal, interest = flow.amount_on(day), 0., 0.
    if isinstance(item, Property):
        source, direction, essential = 'Properties', role.direction, True
        vacancy = item.vacancy_percent / 100
        weight = {'always': 1., 'rented': 1 - vacancy, 'vacant': vacancy}[role.occupancy]
        if not weight:
            return None
        months = (day.year - flow.start.year) * 12 + day.month - flow.start.month
        amount = _grow(amount, 1 + role.appreciation_percent / 100, months / 12) * weight
    elif isinstance(item, Pension):
        source, direction, essential = 'Pensions', 'outgoing', False
        if role == 'withdrawal':
            direction = 'income'
            if item.kind == 'balance':
                amount = min(amount, pensions[item.id])
                pensions[item.id] -= amount
        else:
            if item.kind == 'balance':
                pensions[item.id] = number(pensions[item.id] + amount, 'Pension balance')
            if role != 'bank':
                return None
    else:
        source, direction, essential = 'Loans', 'outgoing', True
        amount = min(amount, loans[item.id])
        interest = min(amount, interest_due[item.id])
        principal = amount - interest
        interest_due[item.id] -= interest
        loans[item.id] -= amount
    if not amount:
        return None
    return CashEvent(item.id, source, f'{item.name} · {flow.name}', day, amount, flow.currency,
                     direction, flow.frequency != 'once', flow.ongoing, essential, principal, interest)


def _balances(settings, properties, pensions, loans):
    return tuple(WealthBalance(item.id, item.name, category, balances[item.id], item.currency)
                 for entries, category, balances in ((settings.properties, 'Properties', properties),
                                                     (settings.pensions, 'Pensions', pensions),
                                                     (settings.loans, 'Loans', loans))
                 for item in entries if not isinstance(item, Pension) or item.kind == 'balance')
