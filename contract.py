"""Shared harness↔adapter contract.

The harness core (`run.py`) is solver-agnostic: it owns the experiment
database, the scratch/artifact lifecycle, and the agent-facing CLI skeleton.
Everything solver-specific — how to invoke a solver, what its outputs mean,
how to validate a result — lives behind a *solver adapter* (`adapter.py`
selects one from the `adapters/` package). This module is the contract both
sides import; it has no project dependencies so neither side imports the other.

An adapter is a module exposing:

    NAME              str          — identifies the solver family; stored in the
                                     `coil_type` column.
    SOLVER_MODES      tuple[str]   — the `--solver` choices the agent may pick;
                                     SOLVER_MODES[0] is the default. A single-
                                     stage solver exposes one mode; a chained
                                     pipeline may expose several.
    ENV_REQUIREMENTS  tuple[str]   — env vars the adapter reads (informational,
                                     for setup tooling).
    add_arguments(parser) -> None  — register this solver's CLI flags, including
                                     `--equilibrium` (the target configuration).
    run_experiment(args, run_dir: Path) -> ExperimentOutcome
                                   — run ONE experiment end-to-end in run_dir.
                                     Owns the entire pipeline (one subprocess or
                                     a chain of them), metric extraction,
                                     classification, and any validation. Must
                                     not raise for solver failures — return an
                                     outcome with status "crash"/"fail".

A *canonical metric key* is a snake_case name an adapter emits in
`ExperimentOutcome.metrics`. Keys that `run.py` projects into dedicated DB
columns get their own column; every other key is preserved in the row's
`metrics` JSON blob. Adapters map their solver's native output (e.g. simsopt's
UPPERCASE results.json keys) onto canonical keys so one schema serves every
solver.
"""

from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class ExperimentOutcome:
    """The result of one experiment, in solver-agnostic terms.

    status: "pass" | "fail" | "crash". "crash" — no evaluable metrics produced
        (timeout, solver error, missing output). "fail" — ran but violated a
        gate (self-intersection, missing required metric, optimizer reported
        failure). "pass" — produced a complete, gate-passing result.
    status_reason: short machine-readable tag, e.g. "ok", "timeout",
        "self_intersecting", "no_seed".
    metrics: canonical-key → value. NaN/Inf are cleaned by the core, so adapters
        may pass raw solver floats.
    params: the solver knobs the agent set for this run, stored as JSON for
        later querying. Adapter-owned: only the adapter knows its own flags.
    validated: independent-validation verdict ("pass"/"fail") when the adapter
        ran one (e.g. Poincaré field-line tracing), else None.
    experiment_group: groups DB rows that belong to one logical experiment when
        an adapter emits a row per pipeline sub-step; None when one experiment
        is one row.
    """

    status: str
    status_reason: str
    metrics: Mapping[str, object] = field(default_factory=dict)
    params: Mapping[str, object] = field(default_factory=dict)
    validated: str | None = None
    experiment_group: str | None = None


def clean(v: object) -> object:
    """Map NaN/Inf floats to None for JSON and SQLite safety; pass else through."""
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def require_env(name: str) -> str:
    """Return env var `name`, or exit(1) with an actionable message if unset.

    For adapter configuration that has no safe default — solver paths, the
    interpreter that has the solver installed. Failing fast at import beats a
    confusing crash mid-experiment.
    """
    val = os.environ.get(name)
    if not val:
        print(
            f"ERROR: {name} not set. Export it in your shell before running run.py.",
            file=sys.stderr,
        )
        sys.exit(1)
    return val
