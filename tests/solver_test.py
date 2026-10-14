"""Real HiGHS solves and conservative handling of interrupted or failed solves."""

from pathlib import Path
import subprocess
import sys
from textwrap import dedent
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import highspy
import numpy as np
from scipy.optimize import Bounds, LinearConstraint

import portfolio.solver as solver
def solve(objective=(1.,), *, integrality=None, bounds=None, constraints=None, **options):
    count = len(objective)
    return solver.milp(
        np.asarray(objective),
        integrality=np.ones(count) if integrality is None else integrality,
        bounds=Bounds(0, np.inf) if bounds is None else bounds,
        constraints=LinearConstraint(np.eye(count), 0, np.inf) if constraints is None else constraints,
        options=options)


class SolverTests(unittest.TestCase):
    def test_mixed_integer_model_preserves_two_sided_rows_and_bounds(self):
        result = solve((1., .1), integrality=(1, 0), bounds=Bounds((-3, 0), (4, 10)),
                       constraints=LinearConstraint([[1, 2], [-1, 1]], [3, -np.inf], [6, 1.5]),
                       mip_rel_gap=0., mip_abs_gap=0., mip_feasibility_tolerance=1e-10)
        self.assertEqual(result.status, 0)
        self.assertTrue(result.success)
        np.testing.assert_allclose(result.x, (0, 1.5))
        self.assertAlmostEqual(result.fun, .15)

    def test_negative_integer_offsets_are_preserved(self):
        for presolve in (True, False):
            with self.subTest(presolve=presolve):
                result = solve(bounds=Bounds(-3, 5), constraints=LinearConstraint([[1]], -2.4, 3),
                               presolve=presolve)
                self.assertEqual(result.status, 0)
                np.testing.assert_allclose(result.x, [-2])

    def test_infeasible_model_has_no_solution(self):
        result = solve(bounds=Bounds(0, 1), constraints=LinearConstraint([[1]], 2, np.inf))
        self.assertEqual(result.status, 2)
        self.assertFalse(result.success)
        self.assertIsNone(result.x)

    def test_unbounded_model_has_no_accepted_solution(self):
        result = solve((-1.,), integrality=(0,), presolve=False)
        self.assertEqual(result.status, 3)
        self.assertFalse(result.success)
        self.assertIsNone(result.x)

    def test_zero_time_budget_reports_timeout(self):
        result = solve(-np.ones(20), bounds=Bounds(0, 1),
                       constraints=LinearConstraint(np.ones((1, 20)), -np.inf, 10.5),
                       presolve=False, time_limit=0.)
        self.assertEqual(result.status, 1)
        self.assertFalse(result.success)

    def test_invalid_option_is_reported(self):
        with self.assertRaisesRegex(ValueError, 'Invalid optimizer option missing_option'):
            solve(missing_option=True)

    def native_solver(self, status):
        native = MagicMock()
        native.setOptionValue.return_value = highspy.HighsStatus.kOk
        native.passModel.return_value = highspy.HighsStatus.kOk
        native.run.return_value = highspy.HighsStatus.kOk
        native.getModelStatus.return_value = status
        native.modelStatusToString.return_value = status.name
        native.getSolution.return_value = SimpleNamespace(value_valid=True, col_value=[1.])
        native.getInfo.return_value = SimpleNamespace(
            primal_solution_status=highspy.kSolutionStatusFeasible, objective_function_value=1.)
        return native

    def test_all_nonoptimal_statuses_remain_failures_even_with_feasible_values(self):
        expected = {highspy.HighsModelStatus.kTimeLimit: 1,
                    highspy.HighsModelStatus.kIterationLimit: 1,
                    highspy.HighsModelStatus.kInfeasible: 2,
                    highspy.HighsModelStatus.kUnbounded: 3}
        for status in highspy.HighsModelStatus.__members__.values():
            if status == highspy.HighsModelStatus.kOptimal:
                continue
            with self.subTest(status=status), patch.object(solver.highspy, 'Highs', return_value=self.native_solver(status)):
                result = solve()
            self.assertEqual(result.status, expected.get(status, 4))
            self.assertFalse(result.success)
            self.assertEqual(result.message, status.name)
            if result.status != 1:
                self.assertIsNone(result.x)
            else:
                np.testing.assert_array_equal(result.x, [1.])

    def test_native_errors_override_stale_optimal_status(self):
        for operation in ('passModel', 'run'):
            native = self.native_solver(highspy.HighsModelStatus.kOptimal)
            getattr(native, operation).return_value = highspy.HighsStatus.kError
            with self.subTest(operation=operation), patch.object(solver.highspy, 'Highs', return_value=native):
                result = solve()
            self.assertEqual(result.status, 4)
            self.assertFalse(result.success)
            self.assertIsNone(result.x)
            self.assertIn('HiGHS', result.message)

    def test_optimal_status_without_valid_primal_values_remains_failure(self):
        for values, valid in (([1.], False), ([], True), ([np.nan], True)):
            native = self.native_solver(highspy.HighsModelStatus.kOptimal)
            native.getSolution.return_value = SimpleNamespace(value_valid=valid, col_value=values)
            with self.subTest(values=values, valid=valid), patch.object(solver.highspy, 'Highs', return_value=native):
                result = solve()
            self.assertEqual(result.status, 4)
            self.assertFalse(result.success)
            self.assertIsNone(result.x)
            self.assertIn('no valid optimal solution', result.message)

    def test_native_exception_is_not_hidden(self):
        native = self.native_solver(highspy.HighsModelStatus.kOptimal)
        native.run.side_effect = RuntimeError('Native solver failed')
        with patch.object(solver.highspy, 'Highs', return_value=native):
            with self.assertRaisesRegex(RuntimeError, 'Native solver failed'):
                solve()

    def test_repaired_hierarchical_plan_has_no_native_debug_output(self):
        # This model reached the stray C printf in SciPy's bundled HiGHS 1.12.
        # A subprocess captures native stdout without changing the GUI's FDs.
        program = dedent('''
            from portfolio.models import (AllocationTargets, CASH, STOCK_MARKET, TREASURY,
                                Holding, ManualHolding, MarketSnapshot, Portfolio, Quote)
            from portfolio.planning import rebalance

            allocation = AllocationTargets(
                {STOCK_MARKET: 60, TREASURY: 25, CASH: 15},
                {STOCK_MARKET: {'US': 60, 'EU': 40},
                 TREASURY: {'EUR': 40, 'HUF': 60}, CASH: {'EUR': 100}})
            portfolio = Portfolio(
                (Holding('US', 'US', 100), Holding('EU', 'EU', 50)), allocation, {'EUR': 63.11},
                (ManualHolding(2500.75, 'EUR'),
                 ManualHolding(2500042.35, 'HUF')))
            market = MarketSnapshot(
                {'US': Quote(69.9, 'EUR'), 'EU': Quote(7.63, 'EUR')},
                {'HUF': 1 / 399.7654321})
            plan = rebalance(portfolio, market, allow_selling=True, time_limit=10)
            assert plan.trades and plan.manual_trades
            assert plan.cash_value >= 0
            assert abs(sum(plan.allocation_values.values()) - plan.wealth) < 1e-6
        ''')
        result = subprocess.run([sys.executable, '-c', program],
                                cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr, '')


if __name__ == '__main__':
    unittest.main()
