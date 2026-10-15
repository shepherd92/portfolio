"""Planning deadlines remain shared across objectives and numerical retries."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

import portfolio.planning as planning
from portfolio.models import AllocationTargets, CASH, STOCK_MARKET, TREASURY, Holding, MarketData, Portfolio, Quote
from portfolio.planning import PLANNING_TIMEOUT_SECONDS, PlanningError, rebalance
from support import portfolio_fixture


def inputs(stock_target=100):
    holdings = (Holding('A', 'A', 3),)
    allocation = AllocationTargets(
        {STOCK_MARKET: {'A': stock_target}, TREASURY: {}, CASH: {'EUR': 100 - stock_target}})
    return Portfolio(holdings, allocation, {'EUR': 10}), MarketData({'A': Quote(10, 'EUR')}, {})


class PlanningDeadlineTests(unittest.TestCase):
    def test_all_objectives_share_one_deadline(self):
        real_milp = planning.milp
        for stock_target in (100, 80):
            clock, limits = SimpleNamespace(now=100.), []

            def solve(*args, **kwargs):
                limits.append(kwargs['options']['time_limit'])
                result = real_milp(*args, **kwargs)
                clock.now += 10
                return result

            with (self.subTest(stock_target=stock_target),
                  patch.object(planning, 'monotonic', side_effect=lambda: clock.now),
                  patch.object(planning, 'milp', side_effect=solve)):
                rebalance(*inputs(stock_target))
            self.assertEqual(limits, [PLANNING_TIMEOUT_SECONDS - 10 * index for index in range(3)])

    def test_presolve_and_translated_retries_share_remaining_budget(self):
        real_milp = planning.milp
        for failed_calls in ((1,), (2, 3)):
            clock, limits = SimpleNamespace(now=100.), []

            def solve(*args, **kwargs):
                limits.append(kwargs['options']['time_limit'])
                clock.now += 10
                if len(limits) in failed_calls:
                    return SimpleNamespace(status=2, x=None, message='Injected numerical infeasibility')
                return real_milp(*args, **kwargs)

            with (self.subTest(failed_calls=failed_calls),
                  patch.object(planning, 'monotonic', side_effect=lambda: clock.now),
                  patch.object(planning, 'milp', side_effect=solve)):
                result = rebalance(*inputs())
            self.assertEqual(result.final_shares['A'], 4)
            self.assertEqual(limits, [PLANNING_TIMEOUT_SECONDS - 10 * index for index in range(3 + len(failed_calls))])

    def test_expired_budget_stops_before_first_solver_call(self):
        with (patch.object(planning, 'monotonic', side_effect=(100, 100 + PLANNING_TIMEOUT_SECONDS)),
              patch.object(planning, 'milp') as solver,
              self.assertRaisesRegex(PlanningError, '60-minute limit')):
            rebalance(*inputs())
        solver.assert_not_called()

    def test_expired_budget_stops_before_next_stage_or_retry(self):
        real_milp = planning.milp
        for fail_first in (False, True):
            clock = SimpleNamespace(now=100.)

            def solve(*args, **kwargs):
                clock.now += PLANNING_TIMEOUT_SECONDS
                if fail_first:
                    return SimpleNamespace(status=2, x=None, message='Injected numerical infeasibility')
                return real_milp(*args, **kwargs)

            with (self.subTest(fail_first=fail_first),
                  patch.object(planning, 'monotonic', side_effect=lambda: clock.now),
                  patch.object(planning, 'milp', side_effect=solve) as solver,
                  self.assertRaisesRegex(PlanningError, '60-minute limit')):
                rebalance(*inputs())
            self.assertEqual(solver.call_count, 1)

    def test_optimal_final_stage_that_overruns_deadline_is_rejected(self):
        real_milp = planning.milp
        clock, calls = SimpleNamespace(now=100.), []

        def solve(*args, **kwargs):
            result = real_milp(*args, **kwargs)
            calls.append(result)
            if len(calls) == 3:
                clock.now += PLANNING_TIMEOUT_SECONDS + 1
            return result

        with (patch.object(planning, 'monotonic', side_effect=lambda: clock.now),
              patch.object(planning, 'milp', side_effect=solve),
              self.assertRaisesRegex(PlanningError, '60-minute limit')):
            rebalance(*inputs())
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(result.status == 0 for result in calls))

    def test_plan_assembly_cannot_return_after_deadline(self):
        real_build = planning._build_plan
        clock = SimpleNamespace(now=100.)

        def build(*args, **kwargs):
            result = real_build(*args, **kwargs)
            clock.now += PLANNING_TIMEOUT_SECONDS + 1
            return result

        cash_only = (portfolio_fixture((), {CASH: 100}, {'EUR': 10}), MarketData({}, {}))
        with (patch.object(planning, 'monotonic', side_effect=lambda: clock.now),
              patch.object(planning, '_build_plan', side_effect=build),
              self.assertRaisesRegex(PlanningError, '60-minute limit')):
            rebalance(*cash_only)

    def test_allocation_timeout_incumbent_is_never_accepted(self):
        real_milp = planning.milp
        portfolio, market = inputs(80)
        for failed_stage in range(1, 4):
            results = []

            def solve(*args, **kwargs):
                result = real_milp(*args, **kwargs)
                results.append(result)
                if len(results) == failed_stage:
                    self.assertIsNotNone(result.x)
                    result.status = 1
                    result.message = 'Injected time limit with feasible incumbent'
                return result

            with (self.subTest(failed_stage=failed_stage),
                  patch.object(planning, 'milp', side_effect=solve),
                  self.assertRaisesRegex(PlanningError, '60-minute limit')):
                rebalance(portfolio, market)
            self.assertEqual(len(results), failed_stage)
            self.assertEqual(portfolio.holdings[0].shares, 3)
            self.assertEqual(portfolio.cash, {'EUR': 10})



if __name__ == '__main__':
    unittest.main()
