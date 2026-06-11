"""Active solver adapter.

`run.py` imports this module as the harness's single solver backend. It ships
selecting the reference simsopt/banana adapter; `/setup-harness` rewrites this
file (its import target) to point at the adapter generated for your solver.
Swap the backend here — never in run.py. See contract.py for the interface an
adapter module must expose.
"""

from adapters.simsopt_banana import (
    NAME,
    SOLVER_MODES,
    ENV_REQUIREMENTS,
    add_arguments,
    run_experiment,
)

__all__ = ["NAME", "SOLVER_MODES", "ENV_REQUIREMENTS", "add_arguments", "run_experiment"]
