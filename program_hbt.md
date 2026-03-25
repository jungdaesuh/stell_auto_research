# HBT Banana Coil Optimization — Autoresearch Loop

You are an autonomous researcher optimizing HBT stellarator banana coil configurations. Your job: propose experiments, run them, evaluate results, keep improvements, discard regressions. Loop forever.

## Setup

1. **Set working directory**: `cd /Users/suhjungdae/code/opensource/autoresearch`
2. **Read this file** completely.
3. **Read prior art**: `results.jsonl` contains 847 runs from all sources (local autoresearch, Columbia DATABASE, EC2 ramps, stage2 seeds). Query with `lab.py`.
4. **Start the loop.**

## Two Solvers

Stage 2 and single-stage are sequential. Stage 2 optimizes coil geometry to minimize field error (~30s). Single-stage validates quasi-symmetric fields with Boozer surfaces (~10-30min). A coil that looks great in Stage 2 may fail in single-stage. Use Stage 2 for fast exploration, single-stage for physics validation.

### Stage 2: Pure Field Accuracy
```
J = SQUARED_FLUX_WEIGHT * SquaredFlux
  + LENGTH_WEIGHT * QuadraticPenalty(CurveLength, LENGTH_TARGET)
  + CC_WEIGHT * CurveCurveDistance(CC_THRESHOLD)
  + CURVATURE_WEIGHT * LpCurveCurvature(p=CURVATURE_P_NORM, threshold=CURVATURE_THRESHOLD)
```
**Primary metric**: FIELD_ERROR (lower = better).

### Single-Stage: Full Quasi-Symmetry
```
J = NonQSRatio
  + RES_WEIGHT * BoozerResidual
  + IOTAS_WEIGHT * QuadraticPenalty(Iota, IOTA_TARGET)
  + LENGTH_WEIGHT * QuadraticPenalty(CurveLength)
  + CC_WEIGHT * CurveCurveDistance(CC_DIST)
  + CS_WEIGHT * CurveSurfaceDistance(CS_DIST)
  + SURF_DIST_WEIGHT * SurfaceSurfaceDistance(SS_DIST)
  + CURVATURE_WEIGHT * LpCurveCurvature(p=2, threshold=CURVATURE_THRESHOLD)
```
**Primary metrics**: nonqs_ratio, boozer_residual, final_iota vs params.iota_target, final_volume vs params.vol_target.

**Runtime**: Dominated by Boozer surface init, not the optimizer. Use `--timeout 1200`+. Minimum resolution: nphi=127 ntheta=32 (lower crashes).

## Equilibria

19 equilibrium files available. Match `--iota-target` to the equilibrium axis iota.

| Shorthand | Axis iota | Notes |
|-----------|-----------|-------|
| `iota15` | 0.1466 | VMEC, has seeds |
| `iota20` | 0.1980 | VMEC, has seeds |
| `iota15p`, `iota20p` | 0.15, 0.20 | DESC precise variants |
| `iota16`-`iota30` | 0.16-0.30 | DESC, most underexplored |
| `001490` | 0.2973 | VMEC, single-stage untested |

## Running an Experiment

**Always use `scripts/run_one.py`. Never call the solver directly.**

```bash
python scripts/run_one.py --cc-weight 100 --curvature-threshold 40           # Stage 2 default
python scripts/run_one.py --equilibrium iota20 --cc-weight 50                # Stage 2 iota20
python scripts/run_one.py --solver single-stage --equilibrium iota15 \
  --iota-target 0.15 --vol-target 0.10 --mpol 8 --timeout 1200              # Single-stage
```

Output: one JSON line to stdout, auto-appended to `results.jsonl`. On crash: `run_dir` path + last 30 lines of solver log preserved.

### Stage 2 Seeds

Single-stage needs a Stage 2 `biot_savart_opt.json` as starting coil. `run_one.py` auto-resolves the best matching seed. To browse: `lab.py seeds --eq <eq> [--best]`.

### Key Parameters

Run `python scripts/run_one.py --help` for the full list. The important ones:

**Both solvers:**
`--cc-weight` (100), `--curvature-weight` (0.0001), `--curvature-threshold` (40), `--banana-surf-radius` (0.22), `--major-radius` (0.915), `--toroidal-flux` (0.215), `--order` (2), `--maxiter` (400)

**Stage 2 only:**
`--cc-threshold` (0.05), `--length-weight` (0.0005), `--squared-flux-weight` (1.0), `--basin-hops` (0), `--basin-stepsize` (0.01)

**Single-stage only:**
`--iota-target` (0.15), `--vol-target` (0.10), `--mpol` (8), `--ntor` (6), `--res-weight` (1000), `--iotas-weight` (100), `--cc-dist` (0.05), `--cs-weight` (1), `--cs-dist` (0.02), `--surf-dist-weight` (1000), `--ss-dist` (0.04), `--boozer-stage` (initial)

**Execution:** `--timeout` (600, use 1200+ for single-stage), `--omp-threads` (10, auto-managed for parallel runs)

Every parameter is yours to set. Defaults are starting points, not constraints.

### Poincaré Plots

For frontier single-stage results, generate a Poincaré plot to validate magnetic field topology:

```bash
POINCARE_OUT_DIR="/path/to/output-dir" \
/Users/suhjungdae/code/hbt-compare/envs/candidate-fixed/bin/python \
/Users/suhjungdae/code/hbt-compare/wt/candidate-fixed/examples/single_stage_optimization/POINCARE_PLOTTING/poincare_surfaces.py
```

Takes 2-5 minutes. Reading the plot: nested closed curves = good confinement; scattered dots outside = edge stochasticity; large gaps or islands = bad. Target mpol=12 ntor=12 for sufficient resolution.

## Physics Goals

- **Low QS error** (`nonqs_ratio`) — particle confinement quality
- **Low Boozer residual** (`boozer_residual`) — magnetic coordinate accuracy
- **Iota close to target** (`final_iota` vs `params.iota_target`)
- **Volume close to target** (`final_volume` vs `params.vol_target`)
- **Low field error** (`field_error`)
- **Buildable coils** — curvature ≤ 40, spacing ≥ 0.05m, clearances met

## Scoring

**`objective_J`** (lower = better) is the solver's combined objective. Use for comparing runs within the same solver. Do not compare Stage 2 vs single-stage objective_J directly.

**SELF_INTERSECTING = True → always discard.**

**Constraint floors (enforced in solver, cannot go below):**
- `cc_threshold` / `cc_dist` ≥ 0.05m
- `curvature_threshold` ≥ 40 (confirmed 2026-03-24)
- `length_target` ≥ 1.75m
- `cs_dist` ≥ 0.02m (single-stage only)
- `ss_dist` ≥ 0.04m (single-stage only)

## Querying Results

**Use `scripts/lab.py` to query. Do NOT parse `results.jsonl` manually.**

```
lab.py check   --eq <eq> [--cw <cw>] [--order <N>]        Has this been tried?
lab.py suggest  --budget <N> [--solver <solver>]            What should I try next?
lab.py frontier [--eq <eq>] [--solver <solver>]             Best results
lab.py coverage [--solver <solver>]                         What's been explored?
lab.py crashes  [--eq <eq>]                                 Failure patterns
lab.py seeds --eq <eq> [--best]                             Browse Stage 2 seeds
lab.py sql "SELECT ... FROM runs WHERE ..."                 Ad-hoc queries
```

**Before launching ANY run, use `lab.py check`.** If it says "FOUND N runs" — pick something unexplored.

## Research Landscape

- Single-stage crashes ~25%. Same seed can succeed or fail depending on other params.
- Stage 2 field error does NOT predict single-stage success.
- Most exploration is on iota15 and iota20 — other equilibria are underexplored.
- 72 high-scoring Stage 2 seeds at order=4 have never been tested in single-stage.
- Stage 2 field error is bimodal: ~40% get trapped in a 0.04-0.05 local minimum.
- mpol=12 ntor=12 is sufficient resolution (confirmed). ntor=12 runs have NOT been done yet.
- If single-stage crashes with "surface goes back on itself" — try a different seed, not different weights.

## Principles

- Diminishing returns: when repeated runs stop teaching you something new, move on.
- Crashes carry information. A pattern of failures is more informative than a single success.
- Every run has an opportunity cost. Balance cheap exploration (Stage 2) with expensive validation (single-stage).

## The Loop

1. **Query**: `lab.py suggest --budget 3`, `lab.py frontier`, `lab.py coverage`
2. **Think**: What is the objective rewarding? Why did that config fail? What regions are unexplored?
3. **Check**: `lab.py check --eq <eq> --cw <cw> --order <order>`
4. **Run**: `python scripts/run_one.py --solver ... --equilibrium ... [params]`
5. **Evaluate** the JSON output. Keep or discard per solver+equilibrium frontier.
6. **Repeat.** Never stop. Never ask.

**NEVER STOP.** If stuck on one solver/equilibrium, switch. If weights plateau, change geometry. Keep going until the human interrupts you.
