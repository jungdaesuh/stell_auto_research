# HBT Banana Coil Optimization — ALM Autoresearch Loop

You are an autonomous researcher optimizing HBT stellarator banana coil configurations using the **Augmented Lagrangian Method (ALM)** for constraint handling. Unlike the weighted-sum approach, ALM enforces engineering constraints (coil spacing, curvature, clearances) as hard constraints with automatic multiplier convergence — you do not tune constraint weights.

## Setup

1. **Set working directory**: `cd /Users/suhjungdae/code/opensource/autoresearch`
2. **Read this file** completely.
3. **Read prior art**:
   - `results.jsonl` — shared log with both weighted-sum and ALM runs. ALM runs have `"alm": true` in their params.
   - Columbia DATABASE: 191 Stage 2 runs across 3 equilibria.
4. **Start the loop.**

## How ALM Differs from Weighted-Sum

In the standard approach, engineering constraints are soft penalties with tunable weights:
```
J = NonQSRatio + 1000*BoozerResidual + 100*Iota + 1*Length
  + 100*CurveCurve + 1*CurveSurface + 1000*SurfSurf + 0.1*Curvature
```
The agent must tune `cc_weight`, `curvature_weight`, `surf_dist_weight`, etc. Wrong weights → constraints violated → crashes.

**ALM separates physics from engineering:**
- **Objective** (what the optimizer minimizes): NonQSRatio + BoozerResidual + Iota penalty + CurveLength
- **Constraints** (enforced by ALM): coil-coil distance ≥ 0.05m, coil-surface distance ≥ 0.02m, surface-vessel distance ≥ 0.04m, curvature ≤ 40

ALM uses Lagrange multipliers that auto-converge to the correct constraint trade-offs. No weight tuning needed.

**Expected benefits:**
- Near-zero crash rate (constraints enforced, not traded away)
- No weight tuning bottleneck
- Cleaner physics optimization (objective is pure physics)

**Expected costs:**
- 2-5x longer per run (multiple L-BFGS-B inner solves per ALM outer iteration)
- New hyperparameters to explore (mu_init, mu_increase, outer_iters)

## Two Solvers

Stage 2 and single-stage are sequential. Stage 2 optimizes coil geometry (fast, ~30s). Single-stage validates quasi-symmetric fields with Boozer surfaces (slow, 10-30min). **ALM applies to single-stage only.** Stage 2 remains weighted-sum.

### Stage 2: Pure Field Accuracy (unchanged)
```
J = SQUARED_FLUX_WEIGHT * SquaredFlux + LENGTH_WEIGHT * Length
  + CC_WEIGHT * CurveCurveDistance + CURVATURE_WEIGHT * Curvature
```
**Primary metric**: FIELD_ERROR. ~20-40s per run.

### Single-Stage with ALM
```
Objective: NonQSRatio + RES_WEIGHT*BoozerResidual + IOTAS_WEIGHT*Iota + LENGTH_WEIGHT*CurveLength
Constraints (ALM-managed):
  - CurveCurve distance ≥ 0.05m
  - CurveSurface distance ≥ 0.02m
  - SurfaceVessel distance ≥ 0.04m
  - Curvature ≤ 40
```
**Primary metrics**: NonQS ratio, Boozer residual, iota accuracy, constraint satisfaction (all raw violations ≤ 0).

## Equilibria and Targets

19 equilibrium files available (same as weighted-sum):

| Shorthand | Axis iota | Source |
|-----------|-----------|--------|
| `iota15` | 0.1466 | VMEC (has seeds) |
| `iota15p` | 0.1500 | DESC |
| `iota16` | 0.1600 | DESC |
| `iota17`-`iota30` | 0.17-0.30 | DESC |
| `iota20` | 0.1980 | VMEC (has seeds) |
| `iota20p` | 0.2000 | DESC |
| `001490` | 0.2973 | VMEC |

Match the equilibrium to your `--iota-target`.

## Running an Experiment

**You MUST use `scripts/run_one.py` for every experiment. Do NOT call the solver directly.**

```bash
# ALM single-stage with iota15
python scripts/run_one.py --solver single-stage --equilibrium iota15 \
  --iota-target 0.15 --vol-target 0.10 --mpol 8 --timeout 3600 \
  --alm --alm-outer-iters 10

# ALM with different ALM hyperparameters
python scripts/run_one.py --solver single-stage --equilibrium iota16 \
  --iota-target 0.16 --vol-target 0.10 --mpol 8 --timeout 3600 \
  --alm --alm-outer-iters 15 --alm-mu-init 10.0 --alm-mu-increase 3.0

# Stage 2 (no ALM — same as weighted-sum, used for seed generation)
python scripts/run_one.py --solver stage2 --equilibrium iota15

# Run with all defaults (Stage 2, iota15)
python scripts/run_one.py
```

Output is one line of JSON to stdout (also auto-appended to `results.jsonl`).

**IMPORTANT**: ALM runs take 2-5x longer than weighted-sum. Use `--timeout 3600` for single-stage ALM. Each ALM outer iteration runs a full L-BFGS-B solve.

### Stage 2 Seeds for Single-Stage

Same as weighted-sum — single-stage needs a Stage 2 seed. `run_one.py` auto-resolves seeds from `stage2_seeds/` and Columbia DATABASE.

### All Parameters

**Solver/equilibrium selection:**
| Flag | Values | Default |
|------|--------|---------|
| `--solver` | `stage2`, `single-stage` | `stage2` |
| `--equilibrium` | `iota15`-`iota30`, `iota15p`, `iota20p`, `001490` | `iota15` |

**Shared (both solvers):**
| Flag | Default | Notes |
|------|---------|-------|
| `--cc-weight` | 100.0 | Stage 2 only (ALM single-stage ignores this) |
| `--curvature-weight` | 0.0001 | Stage 2 only |
| `--curvature-threshold` | 40.0 | Stage 2 only (single-stage ALM uses 40 as hard constraint) |
| `--banana-surf-radius` | 0.22 | Coil winding surface radius |
| `--major-radius` | 0.915 | Stage 2 seed param |
| `--toroidal-flux` | 0.215 | Stage 2 seed param |
| `--order` | 2 | Fourier modes for coil shape |
| `--maxiter` | 400 | L-BFGS-B iterations per ALM inner solve |
| `--nphi` | 127 | Toroidal resolution |
| `--ntheta` | 32 | Poloidal resolution |

**ALM parameters (single-stage only, your exploration space):**
| Flag | Default | Notes |
|------|---------|-------|
| `--alm` | (flag) | **Required** to enable ALM mode |
| `--alm-outer-iters` | 20 | Max ALM outer iterations. More = better constraint satisfaction, longer runtime. Try 5-20. |
| `--alm-mu-init` | 1.0 | Initial penalty parameter. Higher = stronger initial constraint enforcement. Try 0.1-100. |
| `--alm-mu-max` | 1e6 | Cap on penalty growth. Rarely needs changing. |
| `--alm-mu-increase` | 5.0 | How fast penalties grow when constraints aren't improving. Try 2-10. |
| `--alm-tol` | 1e-6 | Convergence tolerance on raw constraint violations. |

**Single-stage physics parameters (your main exploration space):**
| Flag | Default | Notes |
|------|---------|-------|
| `--iota-target` | 0.15 | Target rotational transform |
| `--vol-target` | 0.10 | Target plasma volume |
| `--mpol` | 8 | Poloidal Fourier resolution |
| `--ntor` | 6 | Toroidal Fourier resolution |
| `--res-weight` | 1000 | Boozer residual weight (physics, stays in objective) |
| `--iotas-weight` | 100 | Iota tracking weight (physics, stays in objective) |
| `--constraint-weight` | 1.0 | Boozer constraint weight |
| `--maxcor` | 300 | L-BFGS-B memory |
| `--boozer-stage` | initial | `initial` or `final` |
| `--stage2-bs-path` | (auto) | Explicit Stage 2 seed path |

**Execution:**
| Flag | Default | Notes |
|------|---------|-------|
| `--omp-threads` | 10 | CPU threads |
| `--timeout` | 600 | **Use 3600 for ALM single-stage** |

## What to Explore

Since ALM eliminates weight tuning, your exploration focuses on:

1. **Equilibrium selection** — which of the 19 equilibria produce the best physics with ALM?
2. **Stage 2 seed quality** — different seeds lead to different basins
3. **ALM hyperparameters** — mu_init, mu_increase, alm_outer_iters trade off constraint enforcement speed vs compute cost
4. **Physics weights** — res_weight and iotas_weight still matter (they're in the objective, not constraints)
5. **Resolution** — mpol (8 vs higher), nphi, ntheta
6. **Coil geometry** — order, banana_surf_radius, major_radius, toroidal_flux

**What NOT to tune** (ALM handles these automatically):
- ~~cc_weight~~ — replaced by ALM constraint (cc_dist ≥ 0.05m)
- ~~curvature_weight~~ — replaced by ALM constraint (curvature ≤ 40)
- ~~cs_weight~~ — replaced by ALM constraint (cs_dist ≥ 0.02m)
- ~~surf_dist_weight~~ — replaced by ALM constraint (ss_dist ≥ 0.04m)

## Scoring

**For ALM runs, use these metrics (all in JSON output):**
- `nonqs_ratio` — quasi-symmetry deviation (lower = better)
- `boozer_residual` — Boozer coordinate accuracy (lower = better)
- `field_error` — surface field leakage (lower = better)
- `final_iota` vs `params.iota_target` — rotational transform accuracy
- `final_volume` vs `params.vol_target` — plasma volume accuracy
- `curve_curve_min_dist` — should be ≥ 0.05m (ALM enforces this)
- `max_curvature` — should be ≤ 40 (ALM enforces this)
- `self_intersecting` — hard reject if true
- `objective_J` — the solver's weighted-sum composite (NOT the ALM objective — it uses the old fixed weights for engineering terms). Use `nonqs_ratio` + `boozer_residual` + constraint satisfaction for comparing ALM runs, not `objective_J`.

**Constraint satisfaction check**: In the results JSON, look for `ALM_FINAL_RAW_VIOLATIONS`. All values should be ≤ 0 (negative = satisfied with margin). If any are positive, the ALM didn't converge — try more outer iterations or higher mu_init.

**SELF_INTERSECTING = True → always discard.**

## Logging & Querying Results

Same `results.jsonl` as weighted-sum. ALM runs have `"alm": true` in params. Use `lab.py` for queries:
```
lab.py check   --eq <eq> [--cw <cw>] [--order <N>]        Has this combo been tried?
lab.py suggest  --budget <N> [--solver <solver>]            What should I try next?
lab.py frontier [--eq <eq>] [--solver <solver>] [--top <N>] Best results
lab.py coverage [--solver <solver>]                         What's been explored?
lab.py history  --eq <eq> [--order <N>]                     What combos tried for this eq?
lab.py crashes  [--eq <eq>]                                 Crash/failure patterns
lab.py nearby   --eq <eq> --cw <cw> [--ct <ct>]             Experiments near a param point
lab.py diff     --eq <eq1> --eq2 <eq2>                      Compare two equilibria
lab.py param-effect --param <name> [--eq <eq>]              How does a param affect outcomes?
lab.py seeds --eq <eq> [--order <N>] [--best]               Browse Stage 2 seeds
lab.py schema                                               See column names
lab.py sql "SELECT ... FROM runs WHERE ..."                 Any SELECT query
```

**CRITICAL: Before launching ANY run, use `lab.py check` with the params you plan to use.** If it says "FOUND N runs" — don't re-run it.

Filter ALM vs weighted-sum:
```bash
python scripts/lab.py sql "SELECT * FROM runs WHERE json_extract(params, '$.alm') = 1 ORDER BY nonqs_ratio"
```

## Physics Goals

The stellarator optimization targets:
- **Low quasi-symmetry error** (`nonqs_ratio`) — determines long-term particle confinement
- **Low Boozer residual** (`boozer_residual`) — accuracy of the magnetic coordinate representation
- **Iota close to target** (`final_iota` vs `params.iota_target`) — rotational transform for confinement stability
- **Volume close to target** (`final_volume` vs `params.vol_target`) — plasma capacity
- **Low field error** (`field_error`) — how well coils reproduce the intended field
- **Buildable coils** — curvature ≤ 40, spacing ≥ 0.05m, clearances met (ALM enforces these)

### Poincaré Plots (Visual Field Validation)

When you find a strong ALM result, generate a Poincaré plot to visually verify the magnetic field topology:

```bash
POINCARE_OUT_DIR="/path/to/single_stage_results/outputs-.../mpol=8-ntor=6-HASH-TIMESTAMP" \
/Users/suhjungdae/code/hbt-compare/envs/candidate-fixed/bin/python \
/Users/suhjungdae/code/hbt-compare/wt/candidate-fixed/examples/single_stage_optimization/POINCARE_PLOTTING/poincare_surfaces.py
```

Takes 2-5 minutes. Saves `PoincarePlot_opt.png`. Only for frontier results worth validating visually.

**How to read a Poincaré plot:**
- **Nested closed curves** filling the cross-section = good confinement
- **Curves filling most of the black boundary** = optimized surface close to target
- **Scattered dots outside** = edge stochasticity (some inevitable, less is better)
- **Large gaps or islands** = magnetic islands (bad for confinement)
- **Tight, many nested surfaces with clean edges** = the best outcome

### Constraints

**ALM enforces these as hard constraints (floor-clamped in solver, cannot go below):**
- `cc_dist` ≥ 0.05m (5cm minimum coil-coil spacing)
- `cs_dist` ≥ 0.02m (2cm minimum coil-to-surface clearance)
- `ss_dist` ≥ 0.04m (4cm minimum surface-to-vessel clearance)
- `curvature_threshold` ≤ 40 (maximum curvature)

You can raise thresholds (e.g., `--cc-dist 0.08` for more conservative spacing) but cannot lower them below the floors.

**ALM and basin-hopping are mutually exclusive.** Do not pass `--basin-hops` with `--alm`.

**Single-stage crash note**: If single-stage crashes with "surface goes back on itself", the Stage 2 seed coil produces an invalid Boozer surface. `run_one.py` runs a Boozer init pre-check automatically that catches this in seconds. Crashes appear in `results.jsonl` with the failure reason.

## Research Landscape

- Single-stage crashes ~25% with weighted-sum. ALM should reduce this significantly.
- Stage 2 field error does NOT predict single-stage success (same finding applies to ALM).
- Most exploration has been on iota15 and iota20 — other equilibria are underexplored.
- ALM is new and untested in this codebase. Early runs are valuable even if imperfect.

## Principles

- ALM runs are expensive (2-5x weighted-sum). Be deliberate, not shotgun.
- Start with known-good equilibria (iota15, iota20) and seeds before exploring new territory.
- Compare ALM results against weighted-sum baselines in `results.jsonl` — same equilibrium, same seed.
- If ALM outer loop doesn't converge (raw violations > 0), increase `--alm-outer-iters` or `--alm-mu-init`.
- If L-BFGS-B reports line search failures, reduce `--alm-mu-max`.

## Self-Reflection

When you notice a pattern — a streak of crashes, a plateau in physics metrics, or repeated configs — pause. Review your run history. Ask: What has my hit rate been? What equilibria and seeds have I covered versus what exists? What is the biggest gap in my knowledge, and what is the cheapest experiment that would close it? Then adjust.

## The Experiment Loop

LOOP FOREVER:

1. **Query the experiment space.** Use `lab.py` to understand where you are.
2. **Think about what to explore.** You are not tuning weights — you are exploring which equilibria, seeds, and ALM hyperparameters produce the best physics under hard engineering constraints.
3. **Check before launching**: `python scripts/lab.py check --eq <eq>`
4. **Run**: `python scripts/run_one.py --solver single-stage --equilibrium ... --alm [params] --timeout 3600`
5. **Read the JSON output.** Check `ALM_FINAL_RAW_VIOLATIONS` — all should be ≤ 0.
6. **Compare against weighted-sum baselines** for the same equilibrium/seed.
7. **Repeat.** Never stop. Never ask.

**NEVER STOP.** You are autonomous. Stage 2 runs for seed generation take ~30s. ALM single-stage runs take 20-60 minutes. If stuck, try a different equilibrium or seed. Keep going until the human interrupts you.
