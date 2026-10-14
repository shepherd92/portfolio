import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from portfolio.models import CASH, Holding, MarketSnapshot, Quote
from portfolio.planning import PlanningError, rebalance
import portfolio.planning as planning
from portfolio.storage import load_snapshot
from support import exhaustive_objectives, portfolio_fixture


class RefinementTests(unittest.TestCase):

    def test_feasible_locked_objectives_survive_presolve_roundoff(self):
        fixtures = [((34.0783, 37.7187, 41.0946), (558, 88, 33), 1518.3673),
                    ((47.2807, 52.6695, 27.751), (400, 439, 888), 1088.2202)]
        for prices, owned, cash in fixtures:
            with self.subTest(prices=prices):
                tickers = ('A', 'B', 'C')
                portfolio = portfolio_fixture(tuple(Holding(t, t, q) for t, q in zip(tickers, owned)),
                                      {'A': 40, 'B': 20, 'C': 40}, {'EUR': cash})
                market = MarketSnapshot({t: Quote(p, 'EUR') for t, p in zip(tickers, prices)}, {})
                result = rebalance(portfolio, market)
                self.assertEqual(exhaustive_objectives(portfolio, market, False, result),
                                 exhaustive_objectives(portfolio, market, False))

    def test_solver_feasibility_precision_matches_plan_validation(self):
        fixtures = [((18, 9, 4), (4, 0, 0), (57, 11, 32), 5),
                    ((28, 9, 2), (1, 1, 2), (51, 35, 14), 3),
                    ((1.1, 1.7, 1.7), (0, 2, 1), (28, 18, 54), .4),
                    ((.13, .24, .04), (5, 1, 2), (67, 29, 4), .01)]
        for prices, owned, targets, cash in fixtures:
            with self.subTest(prices=prices):
                tickers = ('A', 'B', 'C')
                portfolio = portfolio_fixture(tuple(Holding(t, t, q) for t, q in zip(tickers, owned)),
                                      dict(zip(tickers, targets)), {'EUR': cash})
                market = MarketSnapshot({t: Quote(p, 'EUR') for t, p in zip(tickers, prices)}, {})
                result = rebalance(portfolio, market, allow_selling=True)
                self.assertEqual(exhaustive_objectives(portfolio, market, True, result),
                                 exhaustive_objectives(portfolio, market, True))

    def test_aggregate_value_overflow_has_validation_error(self):
        portfolio = portfolio_fixture((Holding('A', 'group', 1), Holding('B', 'group', 1, False)),
                              {'group': 100}, {})
        market = MarketSnapshot({t: Quote(1e308, 'EUR') for t in ('A', 'B')}, {})
        with self.assertRaisesRegex(PlanningError, 'too large'):
            rebalance(portfolio, market)

    def test_unaffordable_main_does_not_trigger_unnecessary_sale(self):
        portfolio = portfolio_fixture((Holding('A', 'stocks', 0), Holding('B', 'bonds', 1)),
                              {'stocks': 50, 'bonds': 50}, {})
        market = MarketSnapshot({'A': Quote(2.5, 'EUR'), 'B': Quote(1.5, 'EUR')}, {})
        result = rebalance(portfolio, market, allow_selling=True)
        self.assertEqual(result.trades, ())
        self.assertEqual(result.cash_value, 0)

    def test_oversized_holdings_are_rejected_before_float_conversion(self):
        for shares in (2**53, 2**53 + 1, 2**53 + 3):
            with self.subTest(shares=shares), self.assertRaises(ValueError):
                Holding('A', 'group', shares)

    def test_numpy_unsigned_shares_become_python_integers(self):
        portfolio = portfolio_fixture((Holding('A', 'group', np.uint64(2)),), {'group': 50, CASH: 50}, {})
        self.assertIs(type(portfolio.holdings[0].shares), int)
        market = MarketSnapshot({'A': Quote(10, 'EUR')}, {})
        self.assertEqual(rebalance(portfolio, market, allow_selling=True).trades[0].shares, -1)

    def test_underflow_after_minor_unit_normalization_is_rejected(self):
        with self.assertRaises(ValueError):
            Quote(5e-324, 'GBp')

    def test_fx_overflow_and_underflow_are_rejected(self):
        for price, rate in ((1e-200, 1e-200), (1e200, 1e200)):
            market = MarketSnapshot({'A': Quote(price, 'USD')}, {'USD': rate})
            with self.subTest(price=price), self.assertRaises(ValueError):
                market.price('A')

    def test_huge_numeric_input_has_useful_validation_error(self):
        with self.assertRaises(ValueError):
            Quote(10**1000, 'EUR')

    def test_malformed_snapshot_shapes_are_rejected(self):
        fixtures = [[], {}, {'quotes': [], 'rates': {}, 'base_currency': 'EUR'},
                    {'quotes': {}, 'rates': [], 'base_currency': 'EUR'},
                    {'quotes': {'A': 1}, 'rates': {}, 'base_currency': 'EUR'}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'snapshot.json'
            for data in fixtures:
                path.write_text(json.dumps(data))
                with self.subTest(data=data), self.assertRaises(ValueError):
                    load_snapshot(path)


    def test_solver_success_cannot_hide_a_violated_budget(self):
        portfolio = portfolio_fixture((Holding('A', 'group', 0),), {'group': 100}, {'EUR': 1})
        market = MarketSnapshot({'A': Quote(10, 'EUR')}, {})
        real_milp = planning.milp

        def bad_solution(*args, **kwargs):
            result = real_milp(*args, **kwargs)
            result.x[0] = 1
            return result

        with patch.object(planning, 'milp', side_effect=bad_solution), self.assertRaises(PlanningError):
            rebalance(portfolio, market)


if __name__ == '__main__':
    unittest.main()
