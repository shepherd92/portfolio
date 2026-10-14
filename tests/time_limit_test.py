"""Planning deadlines remain shared across objectives and numerical retries."""

import io
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import main as cli
import portfolio.planning as planning
from portfolio.models import AllocationTargets, CASH, STOCK_MARKET, TREASURY, Holding, MarketSnapshot, Portfolio, Quote
from portfolio.planning import DEFAULT_TIME_LIMIT, PlanningError, rebalance
from support import portfolio_fixture


def inputs(stock_target=100):
    holdings = (Holding('A', 'A', 3),)
    allocation = AllocationTargets(
        {STOCK_MARKET: stock_target, TREASURY: 0, CASH: 100 - stock_target},
        {STOCK_MARKET: {'A': 100}, TREASURY: {}, CASH: {'EUR': 100}})
    return Portfolio(holdings, allocation, {'EUR': 10}), MarketSnapshot({'A': Quote(10, 'EUR')}, {})


class TimeLimitTests(unittest.TestCase):
    def test_all_objectives_share_default_or_custom_budget(self):
        real_milp = planning.milp
        for stock_target in (100, 80):
            for requested in (None, 90):
                clock, limits = SimpleNamespace(now=100.), []

                def solve(*args, **kwargs):
                    limits.append(kwargs['options']['time_limit'])
                    result = real_milp(*args, **kwargs)
                    clock.now += 10
                    return result

                options = {} if requested is None else {'time_limit': requested}
                with (self.subTest(stock_target=stock_target, requested=requested),
                      patch.object(planning, 'monotonic', side_effect=lambda: clock.now),
                      patch.object(planning, 'milp', side_effect=solve)):
                    rebalance(*inputs(stock_target), **options)
                budget = DEFAULT_TIME_LIMIT if requested is None else requested
                self.assertEqual(limits, [budget - 10 * index for index in range(4)])

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
                result = rebalance(*inputs(), time_limit=90)
            self.assertEqual(result.final_shares['A'], 4)
            self.assertEqual(limits, [90 - 10 * index for index in range(4 + len(failed_calls))])

    def test_expired_budget_stops_before_first_solver_call(self):
        with (patch.object(planning, 'monotonic', side_effect=(100, 105)),
              patch.object(planning, 'milp') as solver,
              self.assertRaisesRegex(PlanningError, 'Increase the time limit')):
            rebalance(*inputs(), time_limit=5)
        solver.assert_not_called()

    def test_expired_budget_stops_before_next_stage_or_retry(self):
        real_milp = planning.milp
        for fail_first in (False, True):
            clock = SimpleNamespace(now=100.)

            def solve(*args, **kwargs):
                clock.now += 5
                if fail_first:
                    return SimpleNamespace(status=2, x=None, message='Injected numerical infeasibility')
                return real_milp(*args, **kwargs)

            with (self.subTest(fail_first=fail_first),
                  patch.object(planning, 'monotonic', side_effect=lambda: clock.now),
                  patch.object(planning, 'milp', side_effect=solve) as solver,
                  self.assertRaisesRegex(PlanningError, 'Increase the time limit')):
                rebalance(*inputs(), time_limit=5)
            self.assertEqual(solver.call_count, 1)

    def test_invalid_limits_fail_before_solver_even_for_cash_only(self):
        cash_only = (portfolio_fixture((), {CASH: 100}, {'EUR': 10}), MarketSnapshot({}, {}))
        for portfolio, market in (inputs(), cash_only):
            for value in (0, -1, True, False, float('nan'), float('inf'), -float('inf'), '5', None):
                with (self.subTest(holdings=bool(portfolio.holdings), value=value),
                      patch.object(planning, 'milp') as solver,
                      self.assertRaisesRegex(ValueError, 'Time limit')):
                    rebalance(portfolio, market, time_limit=value)
                solver.assert_not_called()

    def test_optimal_final_stage_that_overruns_deadline_is_rejected(self):
        real_milp = planning.milp
        clock, calls = SimpleNamespace(now=100.), []

        def solve(*args, **kwargs):
            result = real_milp(*args, **kwargs)
            calls.append(result)
            if len(calls) == 4:
                clock.now += 6
            return result

        with (patch.object(planning, 'monotonic', side_effect=lambda: clock.now),
              patch.object(planning, 'milp', side_effect=solve),
              self.assertRaisesRegex(PlanningError, 'Increase the time limit')):
            rebalance(*inputs(), time_limit=5)
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(result.status == 0 for result in calls))

    def test_plan_assembly_cannot_return_after_deadline(self):
        real_build = planning._build_plan
        clock = SimpleNamespace(now=100.)

        def build(*args, **kwargs):
            result = real_build(*args, **kwargs)
            clock.now += 6
            return result

        cash_only = (portfolio_fixture((), {CASH: 100}, {'EUR': 10}), MarketSnapshot({}, {}))
        with (patch.object(planning, 'monotonic', side_effect=lambda: clock.now),
              patch.object(planning, '_build_plan', side_effect=build),
              self.assertRaisesRegex(PlanningError, 'Increase the time limit')):
            rebalance(*cash_only, time_limit=5)

    def test_hierarchy_timeout_incumbent_is_never_accepted(self):
        real_milp = planning.milp
        portfolio, market = inputs(80)
        for failed_stage in range(1, 5):
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
                  self.assertRaisesRegex(PlanningError, 'Increase the time limit')):
                rebalance(portfolio, market)
            self.assertEqual(len(results), failed_stage)
            self.assertEqual(portfolio.holdings[0].shares, 3)
            self.assertEqual(portfolio.cash, {'EUR': 10})

    def test_cli_passes_custom_limit_in_seconds(self):
        portfolio, market = inputs()
        with (patch.object(cli, 'load_portfolio', return_value=portfolio),
              patch.object(cli, 'load_snapshot', return_value=market),
              patch.object(cli, 'rebalance', wraps=rebalance) as planner,
              patch('sys.stdout', new_callable=io.StringIO)):
            status = cli.main(['--snapshot', 'unused.json', 'rebalance', '--time-limit', '450'])
        self.assertEqual(status, 0)
        planner.assert_called_once_with(portfolio, market, allow_selling=False, time_limit=450.)


if __name__ == '__main__':
    unittest.main()
