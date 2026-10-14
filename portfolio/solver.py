"""Small adapter to public HiGHS bindings, independent of SciPy's bundled solver."""

import highspy
import numpy as np
from scipy.optimize import OptimizeResult
from scipy.sparse import csc_array


_STATUSES = {
    highspy.HighsModelStatus.kOptimal: 0,
    highspy.HighsModelStatus.kTimeLimit: 1,
    highspy.HighsModelStatus.kIterationLimit: 1,
    highspy.HighsModelStatus.kInfeasible: 2,
    highspy.HighsModelStatus.kUnbounded: 3,
}


def milp(objective, *, integrality, bounds, constraints, options):
    """Solve the planner's bounded linear model; only status 0 proves optimality."""
    solver = highspy.Highs()
    for name, value in {"output_flag": False, **options}.items():
        if name == "presolve" and isinstance(value, bool):
            value = "on" if value else "off"
        if solver.setOptionValue(name, value) == highspy.HighsStatus.kError:
            raise ValueError(f"Invalid optimizer option {name}: {value!r}")
    if solver.passModel(_model(objective, integrality, bounds, constraints)) == highspy.HighsStatus.kError:
        return OptimizeResult(status=4, success=False, x=None, message="HiGHS rejected the optimization model")
    run_status = solver.run()
    model_status = solver.getModelStatus()
    status = _STATUSES.get(model_status, 4)
    message = solver.modelStatusToString(model_status)
    if run_status == highspy.HighsStatus.kError:
        status = 4
        message = f"HiGHS failed to solve the model: {message}"
    solution, info = solver.getSolution(), solver.getInfo()
    values = np.asarray(solution.col_value, dtype=float)
    feasible = (solution.value_valid
                and info.primal_solution_status == highspy.kSolutionStatusFeasible
                and values.shape == (len(objective),) and np.all(np.isfinite(values)))
    if status == 0 and not feasible:
        status, message = 4, "HiGHS returned no valid optimal solution"
    values = values if status in (0, 1) and feasible else None
    return OptimizeResult(status=status, success=status == 0, x=values,
                          fun=info.objective_function_value if values is not None else None,
                          message=message)


def _model(objective, integrality, bounds, constraints):
    matrix = csc_array(constraints.A, dtype=float)
    model = highspy.HighsLp()
    model.num_row_, model.num_col_ = matrix.shape
    model.col_cost_ = objective
    model.col_lower_ = np.broadcast_to(bounds.lb, (model.num_col_,))
    model.col_upper_ = np.broadcast_to(bounds.ub, (model.num_col_,))
    model.row_lower_ = np.broadcast_to(constraints.lb, (model.num_row_,))
    model.row_upper_ = np.broadcast_to(constraints.ub, (model.num_row_,))
    model.integrality_ = [highspy.HighsVarType(int(value)) for value in integrality]
    model.a_matrix_.format_ = highspy.MatrixFormat.kColwise
    model.a_matrix_.num_col_ = model.num_col_
    model.a_matrix_.num_row_ = model.num_row_
    model.a_matrix_.start_ = matrix.indptr
    model.a_matrix_.index_ = matrix.indices
    model.a_matrix_.value_ = matrix.data
    return model
