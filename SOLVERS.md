# Adding a Solver Variant

The autoresearch runner (`run_one.py`) can drive any SIMSOPT solver variant — different branches, worktrees, or forks. Each variant is registered once, then used by name.

## Register a solver

```bash
python scripts/run_one.py --register \
  --solver-root /path/to/your/simsopt \
  --solver-python /path/to/your/python
```

This:
1. Validates the solver scripts exist at the expected paths
2. Verifies SIMSOPT imports resolve from the specified root (not from an editable install)
3. Records the git commit and branch
4. Saves the configuration to `.solver_config.json`

## Use a registered solver

```bash
python scripts/run_one.py --solver-root /path/to/your/simsopt \
  --solver single-stage --equilibrium iota15 --iota-target 0.15 --timeout 1800
```

The `--solver-python` is remembered from registration. Unknown args (e.g., `--num-surfaces 2`) are forwarded to the solver.

Results are tagged with the solver's git commit and branch in `results.jsonl`, so you can compare runs across variants using `lab.py`:

```bash
python scripts/lab.py sql "SELECT solver_commit, solver_branch, count(*), avg(nonqs_ratio) FROM runs WHERE solver_commit IS NOT NULL GROUP BY solver_commit"
```

## Default (no --solver-root)

When `--solver-root` is omitted, `run_one.py` uses the frozen `candidate-fixed` worktree — the stable baseline for all autoresearch experiments.

## Finding your Python interpreter

Your SIMSOPT installation needs a Python environment with SIMSOPT's compiled extensions (`simsoptpp`). Common locations:

```bash
# Conda env
/path/to/conda/envs/myenv/bin/python

# System venv
/path/to/simsopt/.venv/bin/python

# Shared env (hbt-compare convention)
/Users/suhjungdae/code/hbt-compare/envs/candidate-fixed/bin/python
```

To verify your interpreter has the right packages:
```bash
/path/to/python -c "import simsopt; print(simsopt.__file__)"
```

The `--register` command will verify this automatically and warn if imports resolve to the wrong source tree.

## Currently registered solvers

Check `.solver_config.json` in the repo root, or run:
```bash
cat .solver_config.json
```
