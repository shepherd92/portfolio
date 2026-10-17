"""Connect external assets' cash movements to the available investment portfolio.

These runtime settings are never saved. Existing living-cost buffers remain
unchanged: generated payments consume monthly surplus before planned expenses,
and any uncovered obligation becomes a defensive reserve rather than vanishing.
"""

from dataclasses import dataclass, field, fields
from datetime import date, timedelta
from math import fsum

from portfolio.calculator import (CalculatorSettings, IncomeBudget, MonthlyTotals, Payment, gross_income_by_date,
                                  huf_value, month_after)
from portfolio.wealth import WealthSettings


def linked_settings(settings, wealth, as_of, rates=None, *, years=100):
    """Use current external balances without including them in trading wealth.

    Finite obligations are considered through the supported horizon. For
    indefinite operating costs, reserve the next twelve months; later deficits
    remain projection withdrawals rather than a century of reserves today.
    """
    if not isinstance(settings, CalculatorSettings) or not isinstance(wealth, WealthSettings):
        raise ValueError('Provide validated calculator and external-asset settings.')
    if not (wealth.properties or wealth.pensions or wealth.loans):
        return settings
    if type(as_of) is not date:
        raise ValueError('Use a calendar date for the opening external balances.')
    if isinstance(years, bool) or not isinstance(years, int) or not 1 <= years <= 100:
        raise ValueError('The linked planning horizon must be between 1 and 100 years.')
    _validate_horizon(wealth, month_after(as_of, 12 * years) or date.max)
    return LinkedSettings(**{item.name: getattr(settings, item.name) for item in fields(CalculatorSettings)},
                          wealth=wealth, as_of=as_of, rates=rates, years=years)


@dataclass(frozen=True)
class LinkedSettings(CalculatorSettings):
    wealth: WealthSettings = field(default_factory=WealthSettings)
    as_of: date = field(default_factory=date.today)
    rates: dict | None = field(default=None, repr=False, compare=False)
    years: int = 100
    _event_cache: dict = field(default_factory=dict, init=False, repr=False, compare=False)

    @property
    def currencies(self):
        return super().currencies | self.wealth.currencies

    def validate_context(self, as_of, rates, end=None):
        """Keep generated payments and external valuations on the same basis."""
        if as_of != self.as_of:
            raise ValueError('Linked settings require their original opening date. Rebuild them for this date.')
        for currency in sorted(self.currencies):
            if huf_value(1, currency, self.rates) != huf_value(1, currency, rates):
                raise ValueError(f'Linked settings use different exchange rates for {currency}/HUF. '
                                 'Rebuild them with the current exchange rates.')
        if end is not None and end > (month_after(self.as_of, 12 * self.years) or date.max):
            raise ValueError('Projection extends beyond the linked planning horizon. '
                             'Rebuild linked settings for the requested horizon.')

    def income_budgets(self, as_of, end, rates=None):
        """Keep the deductions that determine surplus after linked commitments."""
        budgets, _ = self._ledger(as_of, end, self.rates if rates is None else rates)
        return budgets

    def scheduled_payments(self, as_of):
        """Protect uncovered finite commitments and twelve months of operations."""
        end = month_after(self.as_of, 12 * self.years) or date.max
        _, deficits = self._ledger(as_of, end, self.rates)
        reserve_end = month_after(as_of, 12) or date.max
        return (*super().scheduled_payments(as_of),
                *(payment for event, payment in deficits
                  if not _operating(event) or payment.due <= reserve_end))

    def operating_payments(self, as_of, end):
        """Later unfunded ongoing costs are paid in the projection when due."""
        _, deficits = self._ledger(as_of, end, self.rates)
        reserve_end = month_after(as_of, 12) or date.max
        return tuple(payment for event, payment in deficits
                     if _operating(event) and payment.due > reserve_end)

    def monthly_totals(self, year, month, rates=None):
        """Scheduled gross cash movements, including linked recurring payments.

        A current/past month is an estimate using today's supplied balances,
        not a reconstruction of historic pension or loan transactions.
        """
        rates = self.rates if rates is None else rates
        totals = super().monthly_totals(year, month, rates)
        amounts = [[getattr(totals, item.name)] for item in fields(MonthlyTotals)]
        for event in self.events_for_month(year, month):
            index = (0 if event.direction == 'income' else 2) + (not event.recurring)
            amounts[index].append(huf_value(event.amount, event.currency, rates))
        return MonthlyTotals(*(fsum(values) for values in amounts))

    def events_for_month(self, year, month):
        """Native-currency linked rows for the Calculator's selected month."""
        start = date(year, month, 1)
        next_month = month_after(start, 1)
        end = next_month - timedelta(days=1) if next_month else date.max
        if start <= self.as_of:
            opening = start - timedelta(days=1) if start > date.min else start
            key = opening, end
            if key not in self._event_cache:
                self._event_cache[key] = self.wealth.events(opening, end)
            return self._event_cache[key]
        return tuple(event for event in self._events(end) if event.due >= start)

    def _events(self, end):
        if end <= self.as_of:
            return ()
        key = next((key for key in self._event_cache if key[0] == self.as_of and key[1] >= end), None)
        if key is None:
            key = self.as_of, end
            self._event_cache[key] = self.wealth.events(*key)
        return tuple(event for event in self._event_cache[key] if event.due <= end)

    def _ledger(self, as_of, end, rates):
        incomes = gross_income_by_date(self.incomes, as_of, end, rates)
        linked_income = {}
        outgoings = {}
        for index, event in enumerate(self._events(end)):
            if event.due <= as_of:
                continue
            amount = huf_value(event.amount, event.currency, rates)
            if event.direction == 'income':
                incomes.setdefault(event.due, []).append(amount)
                linked_income.setdefault(event.due, []).append((event.name, amount))
            else:
                outgoings.setdefault(event.due, []).append((index, event, amount))
        budgets, deficits = [], []
        for day in sorted(incomes.keys() | outgoings.keys()):
            income = fsum(incomes.get(day, ()))
            living = self.monthly_spending(rates, as_of=day)
            available = max(0., income - living)
            # Essential and finite commitments take priority over discretionary,
            # indefinite saving. The total cash result is independent of order.
            obligations = sorted(outgoings.get(day, ()),
                                 key=lambda item: (not item[1].essential, _operating(item[1]), item[0]))
            covered = []
            for index, event, amount in obligations:
                paid = min(available, amount)
                available -= paid
                covered.append((event.name, amount, paid))
                if amount > paid:
                    deficits.append((event, Payment(event.name, amount - paid, day, 'HUF',
                                                     -index - 1, cash_due=day, defensive=True)))
            budgets.append(IncomeBudget(day, income, living, available,
                                        tuple(linked_income.get(day, ())), tuple(covered)))
        return tuple(budgets), tuple(deficits)


def _operating(event):
    # An amortising loan ends when its balance is paid, even when its payment
    # schedule has no manually supplied final month.
    return event.ongoing and event.source != 'Loans'


def _validate_horizon(wealth, end):
    """Reject declared schedules that a bounded calculation would silently omit."""
    schedules = []
    for item in wealth.properties:
        schedules.extend((item.name, entry.flow) for entry in item.cashflows)
    for item in wealth.pensions:
        schedules.extend((item.name, entry.flow) for entry in item.contributions)
        schedules.extend((item.name, flow) for flow in item.withdrawals)
    schedules.extend((item.name, item.payment) for item in wealth.loans if item.payment is not None)
    for name, flow in schedules:
        last = (month_after(flow.start, flow.interval_months * (flow.payment_count - 1))
                if flow.payment_count is not None else flow.start)
        if last > end:
            raise ValueError(f'{name} · {flow.name} extends beyond the supported planning horizon '
                             f'({end:%Y-%m}). Review its beginning and final payment months.')
