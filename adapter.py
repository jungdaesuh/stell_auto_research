"""Active solver-adapter selector.

`run.py` imports this module as the harness's single solver backend, but no
solver is wired in here: which adapter is active is chosen at runtime by the
`AUTORESEARCH_ADAPTER` environment variable, naming a module in the `adapters/`
package (e.g. `simsopt_banana`, `desc_umbilic`). `/setup-harness` sets it for
your solver. This indirection keeps `run.py` and every adapter decoupled.

The selected module must implement the contract in `contract.py`: `NAME`,
`SOLVER_MODES`, `ENV_REQUIREMENTS`, `add_arguments`, `run_experiment`.
"""

from __future__ import annotations

import importlib
import os
import sys

_ENV_VAR = "AUTORESEARCH_ADAPTER"
_CONTRACT_MEMBERS = ("NAME", "SOLVER_MODES", "ENV_REQUIREMENTS", "add_arguments", "run_experiment")


def _load_active_adapter():
    """Import the adapters/ module named by AUTORESEARCH_ADAPTER and validate it.

    Exits with an actionable message (not a raw traceback) on the two first-time
    failure modes: the variable is unset, or it names a module that is missing
    or does not implement the contract.
    """
    name = os.environ.get(_ENV_VAR)
    if not name:
        print(
            f"ERROR: {_ENV_VAR} not set. Name an adapter module in adapters/ "
            f"(e.g. export {_ENV_VAR}=simsopt_banana), or run /setup-harness.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        module = importlib.import_module(f"adapters.{name}")
    except ImportError as e:
        print(f"ERROR: cannot import adapter 'adapters.{name}': {e}", file=sys.stderr)
        sys.exit(1)

    missing = [m for m in _CONTRACT_MEMBERS if not hasattr(module, m)]
    if missing:
        print(
            f"ERROR: adapter 'adapters.{name}' does not implement the contract — "
            f"missing {', '.join(missing)} (see contract.py).",
            file=sys.stderr,
        )
        sys.exit(1)

    return module


_active = _load_active_adapter()

NAME = _active.NAME
SOLVER_MODES = _active.SOLVER_MODES
ENV_REQUIREMENTS = _active.ENV_REQUIREMENTS
add_arguments = _active.add_arguments
run_experiment = _active.run_experiment

__all__ = ["NAME", "SOLVER_MODES", "ENV_REQUIREMENTS", "add_arguments", "run_experiment"]
