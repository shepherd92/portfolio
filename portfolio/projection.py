"""Deterministic projections of the calculator's allocation and protected reserves."""

from dataclasses import dataclass
from datetime import date, timedelta
from math import exp, fsum, isfinite, log, log1p
from numbers import Integral

from portfolio.calculator import (expense_cash_date, expense_income, expense_reserve,
                                  huf_value, month_after, recommend)
from portfolio.models import CASH, STOCK_MARKET, TREASURY, currency_code


@dataclass(frozen=True)
class Projection:
    dates: tuple[date, ...]
    portfolio: dict[str, tuple[float, ...]]
    purpose: dict[str, tuple[float, ...]]
    shortfalls: tuple[tuple[date, float], ...]
    currency: str
    deadline_shortfalls: tuple[tuple[date, float], ...] = ()


def project(settings, as_of, allocation, *, total, rates=None, years=30, currency='HUF'):
    """Project current wealth, monthly savings, growth and actual expense payments.

    FX and nominal spending remain fixed. The recommended initial allocation is
    rebalanced as time passes. Emergency MÁK earnings flow to long-term investment;
    monthly savings replenish fixed buffers and cash due before future commitments.
    Expenses are paid from their earmarked assets and then long-term investment,
    never from protected buffers. ``deadline_shortfalls`` records cash not ready on
    time; ``shortfalls`` records expenses still unpaid at their actual due date.
    Repeated dates show jumps when income arrives or an expense is paid.
    """
    if isinstance(years, bool) or not isinstance(years, Integral) or not 1 <= years <= 100:
        raise ValueError('Projection horizon must be a whole number from 1 to 100 years.')
    recommendation = recommend(settings, as_of, allocation, total=total, rates=rates)
    end = month_after(as_of, 12 * years)
    if end is None:
        raise ValueError('The projection horizon extends beyond the supported calendar.')
    currency = currency_code(currency)
    conversion = huf_value(1, currency, rates)
    ledger = _Ledger(settings, as_of, allocation, recommendation, rates)
    months = {month_after(as_of, month) for month in range(1, 12 * years + 1)}
    events = {end, *months}
    for pot in ledger.expenses:
        events.update(day for day in (pot.deadline, pot.due) if as_of < day <= end)
    dates, portfolio, purpose = [], {}, {}

    def record(day, *, before_cash=False):
        try:
            parts, purposes = ledger.allocations(day, before_cash=before_cash)
            _display_value(fsum(parts.values()), conversion)
        except OverflowError as error:
            raise ValueError('Projection exceeds the supported numeric range; review its horizon and returns.') from error
        dates.append(day)
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
        changed = ledger.receive_income(day) if day in months else False
        changed = ledger.reserve_cash(day) or changed
        changed = ledger.pay_expenses(day) or changed
        if changed:
            record(day)
        previous = day
    return Projection(tuple(dates), {key: tuple(values) for key, values in portfolio.items()},
                      {key: tuple(values) for key, values in purpose.items()},
                      tuple((day, _display_value(value, conversion)) for day, value in ledger.shortfalls), currency,
                      tuple((day, _display_value(value, conversion)) for day, value in ledger.deadline_shortfalls))


@dataclass
class _ExpensePot:
    due: date
    deadline: date
    target: float
    reserve: float
    stock_scale: float
    income: float = 0
    paid: bool = False
    deadline_checked: bool = False

    def stock_fraction(self, day, horizon_days):
        return min(1., max(0, (self.deadline - day).days) / horizon_days) * self.stock_scale


class _Ledger:
    def __init__(self, settings, as_of, allocation, recommendation, rates):
        self.market_return = recommendation.market_return
        self.mak_return = recommendation.mak_return
        self.mix = {name: weight / 100 for name, weight in allocation.totals.items()}
        self.long_return = self.mix[STOCK_MARKET] * self.market_return + self.mix[TREASURY] * self.mak_return
        self.savings = settings.monthly_savings(rates)
        spending = settings.monthly_spending(rates)
        self.fixed_targets = [spending * settings.emergency_months, spending * settings.cash_months]
        self.emergency, self.cash = (row.amount for row in recommendation.rows[:2])
        self.long_term = recommendation.rows[-1].amount
        self.horizon_days = (month_after(as_of, 12 * settings.horizon_years) - as_of).days
        amounts = [huf_value(expense.amount, expense.currency, rates) for expense in settings.expenses]
        _, self.payments = expense_income(settings.expenses, amounts, self.savings, as_of,
                                          self.mak_return, settings.cash_months)
        self.expenses, self.shortfalls, self.deadline_shortfalls = [], [], []
        for expense, amount, row in zip(settings.expenses, amounts, recommendation.rows[2:-1]):
            deadline = expense_cash_date(expense.due, settings.cash_months)
            fraction = min(1., max(0, (deadline - as_of).days) / self.horizon_days)
            scale = min(1., row.stock / row.amount / fraction) if row.amount and fraction else 0.
            self.expenses.append(_ExpensePot(expense.due, deadline, amount, row.amount, scale))

    def allocations(self, day, *, before_cash=False):
        cash_day = day - timedelta(days=1) if before_cash else day
        stock = fsum(pot.reserve * pot.stock_fraction(day, self.horizon_days)
                     for pot in self.expenses if not pot.paid)
        cash = fsum(pot.reserve + pot.income for pot in self.expenses
                    if not pot.paid and cash_day >= pot.deadline)
        invested = fsum(pot.reserve + pot.income for pot in self.expenses
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
                market_return = self.mak_return + pot.stock_scale * (self.market_return - self.mak_return)
                if pot.reserve:
                    before = expense_reserve(1., start_days, self.horizon_days, market_return, self.mak_return)
                    after = expense_reserve(1., end_days, self.horizon_days, market_return, self.mak_return)
                    pot.reserve *= after / before
                pot.income = _grow(pot.income, self.mak_return, start_days - end_days)
        except (OverflowError, ZeroDivisionError) as error:
            raise ValueError('Projection exceeds the supported numeric range; review its horizon and returns.') from error
        self.long_term = self._fill_buffers(self.long_term)

    def receive_income(self, day):
        if not self.savings:
            return False
        available = self._fund_expense_cash(day, self._fill_buffers(self.savings))
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
            value = pot.reserve + pot.income
            deficit = max(0., pot.target - value)
            extra = min(self.long_term, deficit)
            self.long_term += max(0., value - pot.target) - extra
            if deficit - extra > max(1e-7, pot.target * 1e-12):
                self.shortfalls.append((day, deficit - extra))
            pot.reserve = pot.income = 0.
            pot.paid = True
            changed = True
        return changed

    def _fund_expense_cash(self, day, available, *, check_deadlines=False):
        for pot in sorted(self.expenses, key=lambda pot: pot.due):
            if pot.paid or day < pot.deadline:
                continue
            deficit = max(0., pot.target - pot.reserve - pot.income)
            amount = min(available, deficit)
            pot.reserve += amount
            available -= amount
            if check_deadlines and not pot.deadline_checked:
                if deficit - amount > max(1e-7, pot.target * 1e-12):
                    self.deadline_shortfalls.append((day, deficit - amount))
                pot.deadline_checked = True
        return available

    def _fill_buffers(self, amount):
        cash = min(amount, max(0., self.fixed_targets[1] - self.cash))
        self.cash += cash
        emergency = min(amount - cash, max(0., self.fixed_targets[0] - self.emergency))
        self.emergency += emergency
        return max(0., amount - cash - emergency)


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
