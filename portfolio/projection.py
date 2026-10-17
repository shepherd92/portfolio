"""Deterministic projections of the calculator's allocation and protected reserves."""

from dataclasses import dataclass, field
from datetime import date, timedelta
from math import exp, fsum, isfinite, log, log1p
from itertools import groupby
from numbers import Integral

from portfolio.calculator import (payment_cash_date, expense_reserve,
                                  huf_value, month_after, recommend)
from portfolio.models import CASH, STOCK_MARKET, TREASURY, currency_code
from portfolio.networth import net_worth_series


@dataclass(frozen=True)
class Projection:
    dates: tuple[date, ...]
    portfolio: dict[str, tuple[float, ...]]
    purpose: dict[str, tuple[float, ...]]
    shortfalls: tuple[tuple[date, float], ...]
    currency: str
    deadline_shortfalls: tuple[tuple[date, float], ...] = ()
    net_worth: dict[str, tuple[float, ...]] = field(default_factory=dict)


def project(settings, as_of, allocation, *, total, rates=None, years=30, currency='HUF'):
    """Project current wealth, monthly savings, growth and actual expense payments.

    FX stays fixed; payments follow their explicit appreciation assumptions. The recommended initial allocation is
    rebalanced as time passes. Emergency MÁK earnings flow to long-term investment;
    monthly savings replenish fixed buffers and cash due before future commitments.
    Expenses are paid from their earmarked assets and then long-term investment,
    never from protected buffers. ``deadline_shortfalls`` records cash not ready on
    time; ``shortfalls`` records expenses still unpaid at their actual due date.
    Repeated dates show jumps when income arrives or an expense is paid.
    """
    if isinstance(years, bool) or not isinstance(years, Integral) or not 1 <= years <= 100:
        raise ValueError('Projection horizon must be a whole number from 1 to 100 years.')
    if type(as_of) is not date:
        raise ValueError('Use a calendar date without a time.')
    end = month_after(as_of, 12 * years)
    if end is None:
        raise ValueError('The projection horizon extends beyond the supported calendar.')
    if hasattr(settings, 'wealth'):
        settings.validate_context(as_of, rates, end)
    recommendation = recommend(settings, as_of, allocation, total=total, rates=rates, funding_end=end)
    currency = currency_code(currency)
    conversion = huf_value(1, currency, rates)
    ledger = _Ledger(settings, as_of, allocation, recommendation, rates, end)
    wealth_projection = settings.wealth.project(as_of, end) if hasattr(settings, 'wealth') else None
    wealth_days = {snapshot.day for snapshot in wealth_projection.snapshots if snapshot.before_payments} if wealth_projection else set()
    months = {month_after(as_of, month) for month in range(1, 12 * years + 1)}
    events = {end, *months, *wealth_days}
    if any(flow.ongoing and flow.appreciation_percent for flow in settings.outgoings):
        events.update(day for month in range(1, 12 * years + 1)
                      if (day := month_after(as_of.replace(day=1), month)) <= end)
    events.update(day for day in ledger.receipts if day <= end)
    events.update(outgoing.start for outgoing in settings.outgoings
                  if outgoing.ongoing and as_of < outgoing.start <= end)
    for pot in ledger.expenses:
        events.update(day for day in (pot.deadline, pot.due) if as_of < day <= end)
    dates, portfolio, purpose, before_payments = [], {}, {}, []

    def record(day, *, before_cash=False):
        try:
            parts, purposes = ledger.allocations(day, before_cash=before_cash)
            _display_value(fsum(parts.values()), conversion)
        except OverflowError as error:
            raise ValueError('Projection exceeds the supported numeric range; review its horizon and returns.') from error
        dates.append(day)
        before_payments.append(before_cash)
        for target, source in ((portfolio, parts), (purpose, purposes)):
            for name, value in source.items():
                target.setdefault(name, []).append(_display_value(value, conversion))

    ledger.reserve_cash(as_of)
    record(as_of)
    if ledger.pay_expenses(as_of):
        record(as_of)
    previous = as_of
    for day in sorted(events):
        ledger.grow(previous, day)
        record(day, before_cash=True)
        changed = ledger.receive_income(day)
        changed = ledger.reserve_cash(day) or changed
        changed = ledger.pay_expenses(day) or changed
        if changed or day in wealth_days:
            record(day)
        previous = day
    net_worth = {}
    if hasattr(settings, 'wealth'):
        totals = tuple(fsum(values) for values in zip(*portfolio.values()))
        net_worth = net_worth_series(settings.wealth, as_of, dates, totals, rates, currency,
                                     before_payments=before_payments, projection=wealth_projection)
    return Projection(tuple(dates), {key: tuple(values) for key, values in portfolio.items()},
                      {key: tuple(values) for key, values in purpose.items()},
                      tuple((day, _display_value(value, conversion)) for day, value in ledger.shortfalls), currency,
                      tuple((day, _display_value(value, conversion)) for day, value in ledger.deadline_shortfalls),
                      net_worth)


@dataclass
class _ExpensePot:
    due: date
    deadline: date
    target: float
    baseline: float
    defensive: float
    income: float = 0
    paid: bool = False
    deadline_checked: bool = False
    linked: bool = False

    @property
    def value(self):
        return self.baseline + self.defensive + self.income

    def stock(self, day, horizon_days):
        return self.baseline * min(1., max(0, (self.deadline - day).days) / horizon_days)


class _Ledger:
    def __init__(self, settings, as_of, allocation, recommendation, rates, end):
        self.settings, self.rates = settings, rates
        self.market_return = recommendation.market_return
        self.mak_return = recommendation.mak_huf_return
        self.mix = {name: weight / 100 for name, weight in allocation.totals.items()}
        self.long_return = self.mix[STOCK_MARKET] * self.market_return + self.mix[TREASURY] * recommendation.long_term_mak_return
        self._set_buffer_targets(as_of)
        self.emergency, self.cash = (row.amount for row in recommendation.rows[:2])
        self.long_term = recommendation.rows[-1].amount
        self.horizon_days = (month_after(as_of, 12 * settings.horizon_years) - as_of).days
        expenses = settings.scheduled_payments(as_of)
        amounts = [huf_value(expense.amount, expense.currency, rates) for expense in expenses]
        self.receipts = dict(recommendation.receipts)
        self.payments = recommendation.funding_schedule.payments
        self.expenses, self.shortfalls, self.deadline_shortfalls = [], [], []
        for expense, amount, row in zip(expenses, amounts, recommendation.rows[2:-1]):
            deadline = payment_cash_date(expense, settings.cash_months)
            fraction = 0. if expense.defensive else min(1., max(0, (deadline - as_of).days) / self.horizon_days)
            baseline = row.stock / fraction if fraction else 0.
            # A negative adjustment is the absent defensive principal replaced by
            # future receipts. It is never counted as money the portfolio owns.
            self.expenses.append(_ExpensePot(expense.due, deadline, amount, baseline, row.amount - baseline,
                                             linked=expense.outgoing_index < 0))
        if hasattr(settings, 'operating_payments'):
            for payment in settings.operating_payments(as_of, end):
                amount = huf_value(payment.amount, payment.currency, rates)
                self.expenses.append(_ExpensePot(payment.due, payment.due, amount, 0., 0., linked=True))

    def allocations(self, day, *, before_cash=False):
        cash_day = day - timedelta(days=1) if before_cash else day
        stock = fsum(pot.stock(day, self.horizon_days)
                     for pot in self.expenses if not pot.paid)
        cash = fsum(pot.value for pot in self.expenses
                    if not pot.paid and cash_day >= pot.deadline)
        invested = fsum(pot.value for pot in self.expenses
                        if not pot.paid and cash_day < pot.deadline)
        parts = {STOCK_MARKET: self.long_term * self.mix[STOCK_MARKET] + stock,
                 TREASURY: self.long_term * self.mix[TREASURY] + self.emergency + max(0., invested - stock),
                 CASH: self.long_term * self.mix[CASH] + self.cash + cash}
        purposes = {'Long-term investment': self.long_term, 'Emergency fund': self.emergency,
                    'Planned expenses': invested + cash, 'Monthly living costs': self.cash}
        return parts, purposes

    def grow(self, start, end):
        days = (end - start).days
        try:
            self.long_term = _grow(self.long_term, self.long_return, days)
            emergency = _grow(self.emergency, self.mak_return, days)
            self.emergency = min(emergency, self.fixed_targets[0])
            self.long_term += max(0., emergency - self.emergency)
            for pot in self.expenses:
                if pot.paid or start >= pot.deadline:
                    continue
                end_days = max(0, (pot.deadline - end).days)
                start_days = (pot.deadline - start).days
                if pot.baseline:
                    before = expense_reserve(1., start_days, self.horizon_days, self.market_return, self.mak_return)
                    after = expense_reserve(1., end_days, self.horizon_days, self.market_return, self.mak_return)
                    pot.baseline *= after / before
                pot.defensive = _grow(pot.defensive, self.mak_return, start_days - end_days)
                pot.income = _grow(pot.income, self.mak_return, start_days - end_days)
        except (OverflowError, ZeroDivisionError) as error:
            raise ValueError('Projection exceeds the supported numeric range; review its horizon and returns.') from error
        self._set_buffer_targets(end)
        emergency = min(self.emergency, self.fixed_targets[0])
        cash = min(self.cash, self.fixed_targets[1])
        self.long_term += self.emergency - emergency + self.cash - cash
        self.emergency, self.cash = emergency, cash
        self.long_term = self._fill_buffers(self.long_term)

    def receive_income(self, day):
        savings = self.receipts.get(day, 0.)
        if not savings:
            return False
        available = self._fund_expense_cash(day, self._fill_buffers(savings))
        for index, scheduled in self.payments.get(day, ()):
            pot = self.expenses[index]
            if not pot.paid and day < pot.deadline:
                amount = min(scheduled, available)
                pot.income += amount
                available -= amount
        self.long_term += available
        return True

    def reserve_cash(self, day):
        previous = self.long_term
        self.long_term = self._fund_expense_cash(day, self.long_term, check_deadlines=True)
        return self.long_term != previous or any(not pot.paid and pot.deadline == day for pot in self.expenses)

    def pay_expenses(self, day):
        changed = False
        for pot in sorted(self.expenses, key=lambda pot: pot.due):
            if pot.paid or pot.due > day:
                continue
            value = pot.value
            deficit = max(0., pot.target - value)
            extra = min(self.long_term, deficit)
            self.long_term += max(0., value - pot.target) - extra
            if deficit - extra > max(1e-7, pot.target * 1e-12):
                if pot.linked:
                    raise ValueError(f'Cannot project: a linked payment in {day:%Y-%m} has an unfunded '
                                     f'{deficit - extra:.0f} HUF. Review income, balances or its schedule.')
                self.shortfalls.append((day, deficit - extra))
            pot.baseline = pot.defensive = pot.income = 0.
            pot.paid = True
            changed = True
        return changed

    def _fund_expense_cash(self, day, available, *, check_deadlines=False):
        """Recover cash shortfalls by deadline, sharing tied deficits proportionally.

        In an underfunded projection these repairs may consume receipts already
        earmarked for later expenses. Calendar commitments describe the intended
        funding plan; they do not conceal those shortfalls or invent money.
        """
        pending = sorted((pot for pot in self.expenses if not pot.paid and pot.deadline <= day),
                         key=lambda pot: pot.deadline)
        for _, group in groupby(pending, key=lambda pot: pot.deadline):
            pots = tuple(group)
            deficits = [max(0., pot.target - pot.value) for pot in pots]
            scale = max(deficits)
            total = fsum(deficit / scale for deficit in deficits) if scale else 0.
            amounts = [min(deficit, available * (deficit / scale / total)) if deficit else 0.
                       for deficit in deficits]
            for pot, deficit, amount in zip(pots, deficits, amounts):
                pot.defensive += amount
                if check_deadlines and not pot.deadline_checked:
                    if deficit - amount > max(1e-7, pot.target * 1e-12):
                        self.deadline_shortfalls.append((day, deficit - amount))
                    pot.deadline_checked = True
            available = max(0., available - fsum(amounts))
        return available

    def _fill_buffers(self, amount):
        cash = min(amount, max(0., self.fixed_targets[1] - self.cash))
        self.cash += cash
        emergency = min(amount - cash, max(0., self.fixed_targets[0] - self.emergency))
        self.emergency += emergency
        return max(0., amount - cash - emergency)

    def _set_buffer_targets(self, day):
        spending = self.settings.monthly_spending(self.rates, as_of=day)
        self.fixed_targets = [spending * self.settings.emergency_months,
                              spending * self.settings.cash_months]


def _grow(value, annual_return, days):
    if not value or not days:
        return value
    growth = log1p(annual_return / 100) if annual_return > -50 else log((100 + annual_return) / 100)
    return value * exp(growth * days / 365.2425)


def _display_value(value, conversion):
    value /= conversion
    if not isfinite(value) or value < 0:
        raise ValueError('Projection exceeds the supported numeric range; review its horizon, returns and currency.')
    return value
