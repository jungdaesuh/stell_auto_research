# autoresearch — stellarator coil optimization

Autonomous AI agent harness for stellarator coil optimization. Fork of [karpathy/autoresearch](https://github.com/karpathy/autoresearch) by [Andrej Karpathy](https://github.com/karpathy), adapted for physics optimization: instead of editing `train.py` to lower `val_bpb`, the agent picks solver parameters to optimize magnetic field configurations.

Built on Karpathy's core insight: give an AI agent a research problem and let it experiment autonomously. You wake up to a database of experiments and (hopefully) better coil configurations.

## How it works

```
program_optimization.md   ← agent instructions (human edits)
run.py                    ← run solver, store results (fixed harness)
results.db                ← experiment database (agent queries with SQL)
results.jsonl             ← append-only log (human-readable backup)
schema.sql                ← database schema (applied once)
LESSONS.md                ← append-only research memory (agent + human)
```

The agent reads `program_optimization.md`, queries `results.db` to understand what's been tried, picks parameters, calls `run.py`, evaluates the result, and loops.

## Quick start (new collaborator)

If you use Claude Code, the fastest path is the bundled setup skill:

```
/setup-harness
```

It interviews you (paths, your simsopt fork's layout, campaign goals),
verifies your solver scripts accept the flags the harness passes, writes
`.env`, generates your campaign program file from
`templates/program_template.md`, and ends with a real smoke run. Manual
setup below works too.

## Quick start (manual)

**Requirements:** SIMSOPT installed, equilibrium files, Python 3.10+.

```bash
# 1. Clone and enter the repo
git clone <repo-url>
cd autoresearch
git checkout general-harness

# 2. Configure your environment
cp .env.sample .env
# Edit .env with your paths:
#   SIMSOPT_ROOT=/path/to/your/simsopt
#   SIMSOPT_PYTHON=/path/to/conda/envs/simsopt/bin/python
#   EQUILIBRIA_DIR=/path/to/equilibria

# 3. Run your first experiment
python run.py --equilibrium nfp5_iota17 --cc-weight 100

# 4. Query results
sqlite3 results.db -header -column \
  "SELECT equilibrium, status, field_error, max_curvature FROM runs"
```

## Environment variables

| Variable | Description |
|----------|-------------|
| `SIMSOPT_ROOT` | Path to SIMSOPT repository root (contains `examples/single_stage_optimization/`) |
| `SIMSOPT_PYTHON` | Python interpreter with SIMSOPT and dependencies installed |
| `EQUILIBRIA_DIR` | Directory containing equilibrium `wout_*.nc` files |
| `STAGE2_SCRIPT` | *(optional)* Stage 2 solver script, relative to `SIMSOPT_ROOT` |
| `SINGLE_STAGE_SCRIPT` | *(optional)* single-stage solver script, relative to `SIMSOPT_ROOT` |
| `POINCARE_SCRIPT` | *(optional)* Poincare validation script, relative to `SIMSOPT_ROOT` |
| `OUTPUT_BASE` | *(optional)* scratch dir for live solver runs (default `/tmp/stellarator_harness`) |
| `KEEP_ARTIFACTS` | *(optional)* retention for completed runs' outputs: `none` (default) / `pass` / `all` |
| `ARTIFACTS_DIR` | *(optional)* where kept run dirs are moved, named by run id (default `<repo>/artifacts`) |
| `STAGE2_SEED_DIR` | *(optional)* Stage 2 seed archive (default `<repo>/stage2_seeds`) |

The script overrides exist for simsopt forks that keep the solver scripts at
non-default paths. Crashed runs always leave their dir + `run.log` under
`OUTPUT_BASE` for debugging, regardless of `KEEP_ARTIFACTS`.

Set these in `.env` (not committed) or export them in your shell.

## Two solvers

**Stage 2** (~30s) — optimizes coil geometry to minimize field error. Use for fast exploration.

**Single-stage** (~10-30min) — validates quasi-symmetric fields with Boozer surfaces. Use for physics validation. Needs a Stage 2 seed (auto-resolved from `stage2_seeds/`).

```bash
# Stage 2 (default, fast)
python run.py --equilibrium nfp5_iota17 --cc-weight 100

# Single-stage (slow, physics validation)
python run.py --solver single-stage --equilibrium nfp5_iota20 \
    --iota-target 0.20 --vol-target 0.10 --mpol 8 --timeout 1200
```

## Results

Every run writes to both `results.jsonl` (flat file) and `results.db` (SQLite). Query with standard tools:

```bash
# SQLite (recommended for agents)
sqlite3 results.db -header -column "SELECT * FROM runs WHERE status='pass' ORDER BY field_error LIMIT 10"

# JSONL (for scripts, jq, grep)
cat results.jsonl | jq 'select(.status=="pass")' | jq -s 'sort_by(.field_error)[:10]'
```

## Autonomous agent usage

Point your AI agent at `program_optimization.md` and let it go:

```
Read program_optimization.md, then start the optimization loop.
```

The agent will query the database, pick experiments, run them, evaluate results, and repeat indefinitely. See `program_optimization.md` for the full agent instructions including schema, parameters, constraints, and query examples.

## Extending to other coil types

The harness is designed for banana coils but extensible. To add a new coil type:

1. Add a solver entry to the `SOLVERS` dict in `run.py`
2. Write a new `program_<type>.md` with type-specific instructions
3. Add a `--coil-type` CLI flag to `run.py` to dispatch to the new solver

The schema, dual storage, and query patterns work for any coil type.

## Project structure

```
.env.sample                ← template for environment variables
schema.sql                 ← database schema (1 table, 28 columns)
run.py                     ← experiment harness (~740 lines)
program_optimization.md    ← agent instructions for banana coils (reference campaign)
templates/program_template.md  ← skeleton for generating your own campaign program
LESSONS.md                 ← append-only research memory
.claude/skills/setup-harness/  ← interactive first-time setup skill
results.db                 ← SQLite database (created on first run)
results.jsonl              ← append-only JSON log (created on first run)
PLAN.md                    ← design rationale
```
