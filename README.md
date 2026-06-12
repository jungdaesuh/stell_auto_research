# autoresearch — a runner and logbook for optimization campaigns

Autonomous AI agent harness for physics optimization campaigns. Fork of
[karpathy/autoresearch](https://github.com/karpathy/autoresearch) by
[Andrej Karpathy](https://github.com/karpathy), adapted from LLM training to
solver-driven optimization.

It does **not** do the physics — that's yours. It does the work *around* the
physics: you describe one experiment as a command, and it runs your parameter
scans, records every result (and every failure) in a queryable SQLite database,
and keeps an append-only `LESSONS.md` so nothing learned is lost between
sessions. You decide what to try; it handles the loop and the bookkeeping.

The harness core is **solver-agnostic**. You bring your optimizer — simsopt,
DESC, anything that runs from a command — behind a small *adapter*. The shipped
reference adapter drives banana coils on simsopt; the same core runs a DESC
umbilic-coil pipeline through a different adapter, with no change to the core.

## How it works

```
program_<campaign>.md     ← agent instructions (you / the setup skill write this)
run.py                    ← generic runner: runs the adapter, stores results
adapter.py                ← selects the active solver adapter
adapters/<solver>.py      ← solver-specific glue (the only file that knows your solver)
contract.py               ← the harness↔adapter contract
results.db                ← experiment database (query with SQL)
results.jsonl             ← append-only log (human-readable backup)
schema.sql                ← database schema (applied automatically)
LESSONS.md                ← append-only research memory (agent + human)
```

The agent reads `program_<campaign>.md`, queries `results.db` to see what's been
tried, picks parameters, calls `run.py`, evaluates the result, and loops.

## Quick start (new collaborator)

If you use Claude Code, the fastest path is the bundled setup skill:

```
/setup-harness
```

It detects your solver stack (simsopt / DESC / other), **installs any missing
dependencies**, interviews you about your campaign, generates a solver adapter
and a campaign program file, and ends with a real smoke run. You do not need to
read anything below first.

## Quick start (manual, banana/simsopt reference)

**Requirements:** the reference adapter needs simsopt installed, equilibrium
files, and Python 3.10+.

```bash
# 1. Clone and enter the repo
git clone <repo-url>
cd autoresearch

# 2. Configure your shell (these are read by the banana adapter)
export SIMSOPT_ROOT=/path/to/your/simsopt
export SIMSOPT_PYTHON=/path/to/conda/envs/simsopt/bin/python
export EQUILIBRIA_DIR=/path/to/equilibria

# 3. Run your first experiment
python run.py --equilibrium nfp5_iota17 --cc-weight 100

# 4. Query results
sqlite3 results.db -header -column \
  "SELECT equilibrium, status, field_error, max_curvature FROM runs"
```

## Architecture: core + adapter

The core (`run.py`) owns the database, the scratch/artifact lifecycle, and the
agent-facing CLI skeleton — and nothing solver-specific. One experiment is:

```
run.py  →  adapter.run_experiment(args, run_dir)  →  ExperimentOutcome  →  results.db / results.jsonl
```

The adapter (see `contract.py` for the interface, `adapters/simsopt_banana.py`
for a worked example) owns everything about your solver: which flags exist
(`add_arguments`), which modes it has (`SOLVER_MODES`), and how to run one
experiment end-to-end (`run_experiment`) — whether that's a single subprocess or
a chained pipeline. It returns metrics as canonical keys; the core stores the
ones with a dedicated column and preserves the rest in a `metrics` JSON blob, so
every solver shares one schema.

## Environment variables

**Core (read by `run.py`, all optional):**

| Variable | Description |
|----------|-------------|
| `OUTPUT_BASE` | scratch dir for live runs (default `/tmp/stellarator_harness`). Crashed runs leave their dir + `run.log` here for debugging. |
| `KEEP_ARTIFACTS` | retention for completed runs' outputs: `none` (default) / `pass` / `all`. Kept dirs move to `ARTIFACTS_DIR/<run-id>`. |
| `ARTIFACTS_DIR` | where kept run dirs land, named by run id (default `<repo>/artifacts`). |

**Adapter-specific (read by the active adapter).** The banana/simsopt adapter
reads:

| Variable | Description |
|----------|-------------|
| `SIMSOPT_ROOT` | simsopt repo root (contains `examples/single_stage_optimization/`) |
| `SIMSOPT_PYTHON` | interpreter with simsopt installed |
| `EQUILIBRIA_DIR` | directory of equilibrium `wout_*.nc` files |
| `STAGE2_SCRIPT` / `SINGLE_STAGE_SCRIPT` / `POINCARE_SCRIPT` | *(optional)* solver script paths, relative to `SIMSOPT_ROOT`, for forks with a non-default layout |
| `STAGE2_SEED_DIR` | *(optional)* Stage 2 seed archive single-stage warm-starts from (default `<repo>/stage2_seeds`) |

A different solver's adapter declares its own variables (see its
`ENV_REQUIREMENTS`). Export these in your shell before invoking `run.py`.

## Solver modes (banana reference)

The `--solver` choices come from the active adapter. The banana adapter exposes
two:

**Stage 2** (~30s) — optimizes coil geometry against a fixed plasma surface to
minimize field error. Fast; use for exploration and seed generation.

**Single-stage** (~10–30min) — jointly optimizes coils and a Boozer surface for
quasi-symmetry, then runs Poincaré validation. Slow; use for physics validation.
Warm-starts from an archived Stage 2 seed (auto-resolved, or `--stage2-bs-path`).

```bash
# Stage 2 (default mode)
python run.py --equilibrium nfp5_iota17 --cc-weight 100

# Single-stage
python run.py --solver single-stage --equilibrium nfp5_iota20 \
    --iota-target 0.20 --vol-target 0.10 --mpol 8 --timeout 1200
```

## Results

Every run writes to both `results.jsonl` (flat file) and `results.db` (SQLite).

```bash
# SQLite (recommended for agents)
sqlite3 results.db -header -column "SELECT * FROM runs WHERE status='pass' ORDER BY field_error LIMIT 10"

# solver-specific metrics live in the JSON overflow column
sqlite3 results.db "SELECT id, json_extract(metrics,'\$.lead_end_curvature') FROM runs"

# JSONL (for scripts, jq, grep)
cat results.jsonl | jq 'select(.status=="pass")' | jq -s 'sort_by(.field_error)[:10]'
```

## Autonomous agent usage

Point your AI agent at the campaign program file and let it go:

```
Read program_<campaign>.md, then start the optimization loop.
```

The agent queries the database, picks experiments, runs them, evaluates results,
records lessons, and repeats. See `program_<campaign>.md` for the full
instructions including schema, parameters, constraints, and query examples.

## Adding a new solver

You don't touch `run.py`. Either:

1. Run `/setup-harness` — it detects your solver, installs deps, and generates
   the adapter for you; **or**
2. Write `adapters/<your-solver>.py` implementing the `contract.py` interface
   (`NAME`, `SOLVER_MODES`, `ENV_REQUIREMENTS`, `add_arguments`,
   `run_experiment`), using `adapters/simsopt_banana.py` as the worked example,
   and point `adapter.py` at it.

`run_experiment` can run a single subprocess or a multi-step pipeline (e.g. a
DESC chain: bumped surface → fixed-boundary equilibrium → coil optimization →
free-boundary equilibrium). The schema, dual storage, and query patterns work
for any solver; solver-specific metrics go to the `metrics` JSON column.

## Project structure

```
contract.py                ← harness↔adapter contract (ExperimentOutcome, helpers)
run.py                     ← generic experiment runner (solver-agnostic)
adapter.py                 ← active-adapter selector
adapters/simsopt_banana.py ← reference adapter (banana coils on simsopt)
schema.sql                 ← database schema
program_<campaign>.md      ← agent instructions for a campaign
templates/program_template.md  ← skeleton for generating a campaign program
LESSONS.md                 ← append-only research memory
.claude/skills/setup-harness/  ← interactive first-time setup skill
results.db / results.jsonl ← experiment database + log (created on first run)
PLAN.md                    ← design rationale
```
