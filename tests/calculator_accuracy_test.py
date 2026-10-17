"""Independent analytic checks of the declining-allocation expense model."""

from datetime import date
from decimal import Decimal, localcontext
from math import isfinite, nextafter
import sys
import unittest

from support import calculator_allocation as allocation

from portfolio.calculator import (CalculatorSettings, CashFlow, ReturnAssumptions,
                                  expense_reserve, recommend)


def reference_reserve(amount, days, horizon_days, market_return, mak_return):
    """High-precision antiderivative, independent of the production log formula."""
    with localcontext() as context:
        context.prec = 100
        years = Decimal(days) / Decimal('365.2425')
        horizon = Decimal(horizon_days) / Decimal('365.2425')
        duration = min(years, horizon)
        market = Decimal.from_float(float(market_return)) / 100
        mak = Decimal.from_float(float(mak_return)) / 100
        start = 1 + mak
        slope = (market - mak) / horizon
        if slope:
            fraction = duration / horizon
            end = (1 - fraction) * start + fraction * (1 + market)
            primitive = lambda value: value * value.ln() - value
            growth = (primitive(end) - primitive(start)) / slope
        else:
            growth = duration * start.ln()
        growth += max(Decimal(0), years - horizon) * (1 + market).ln()
        reserve = Decimal.from_float(float(amount)) * (-growth).exp()
        return float(reserve)


class ExpenseProjectionAccuracyTests(unittest.TestCase):
    def assert_projection(self, amount, days, horizon_days, market_return, mak_return):
        expected_reserve = reference_reserve(
            amount, days, horizon_days, market_return, mak_return)
        reserve = expense_reserve(amount, days, horizon_days, market_return, mak_return)
        self.assertTrue(isfinite(reserve) and reserve > 0)
        self.assertAlmostEqual(reserve, expected_reserve, delta=abs(expected_reserve) * 2e-12)

    def test_ordinary_rates_inside_at_and_beyond_calendar_horizon(self):
        for days in (1, 365, 2557, 2557 + 730):
            for market, mak in ((6, 0), (6, 5), (0, 6), (-10, 5)):
                with self.subTest(days=days, market=market, mak=mak):
                    self.assert_projection(100_000_000, days, 2557, market, mak)

    def test_equal_and_nearly_equal_rates_do_not_lose_precision(self):
        for market, mak in ((6, 6), (6, 6 + 1e-12), (1e-12, 2e-12),
                            (-99.9, -99.9 + 1e-12), (-1e-12, 1e-12)):
            with self.subTest(market=market, mak=mak):
                self.assert_projection(1_000_000, 365, 2557, market, mak)

    def test_rates_close_to_minus_one_hundred_use_accurate_integral(self):
        boundary = nextafter(-100., 0.)
        for market, mak in ((6, -99.9), (-99.9, 6), (6, boundary),
                            (boundary, 6), (boundary, boundary)):
            with self.subTest(market=market, mak=mak):
                self.assert_projection(1_000_000, 365, 365, market, mak)

    def test_large_finite_rates_do_not_overflow_intermediate_products(self):
        for market, mak in ((1e308, 5), (5, 1e308), (1e308, -99.9)):
            with self.subTest(market=market, mak=mak):
                self.assert_projection(1_000_000, 365, 2557, market, mak)
        self.assert_projection(1_000_000, 1, 2557, sys.float_info.max, sys.float_info.max)

    def test_corrected_reserve_leaves_only_the_true_long_term_remainder(self):
        settings = CalculatorSettings(
            horizon_years=1,
            returns=ReturnAssumptions(mak_huf=-99.9),
            outgoings=(CashFlow('One-year cost', 1000000, date(2027, 12, 16)),),
        )
        result = recommend(settings, date(2026, 10, 16), allocation(), total=3_000_000)
        days = (date(2027, 10, 1) - date(2026, 10, 16)).days
        required = reference_reserve(1_000_000, days, 365, 6, -99.9)
        self.assertAlmostEqual(result.rows[2].amount, required, delta=.000001)
        self.assertAlmostEqual(result.rows[-1].amount, 3_000_000 - required, delta=.000001)
        self.assertAlmostEqual(result.total, 3_000_000)


if __name__ == '__main__':
    unittest.main()
