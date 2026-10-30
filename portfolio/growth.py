"""Validated nominal rates and calendar-month compounding for payment schedules."""

from math import exp, isfinite, log, log1p

from portfolio.models import number


def annual_percent(value, label):
    value = number(value, label)
    if not -100 < value <= 1000:
        raise ValueError(f'{label} must be greater than -100% and at most 1,000%.')
    return value


def compound_months(amount, rate, months, label='Projected amount'):
    if not amount or not rate or not months:
        return amount
    growth = log1p(rate / 100) if rate > -50 else log((100 + rate) / 100)
    try:
        result = amount * exp(growth * months / 12)
    except OverflowError as error:
        raise ValueError(f'{label} exceeds the supported numeric range.') from error
    if not isfinite(result) or result <= 0:
        raise ValueError(f'{label} exceeds the supported numeric range.')
    return result
