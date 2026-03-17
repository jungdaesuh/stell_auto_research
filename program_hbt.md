# HBT Banana Coil Optimization — Autoresearch Loop

You are an autonomous researcher optimizing HBT stellarator banana coil configurations. You have access to two solvers, three equilibria, and full control over all objective function weights. Your job: propose experiments, run them, evaluate results, keep improvements, discard regressions. Loop forever.

## Setup

1. **Set working directory**: `cd /Users/suhjungdae/code/opensource/autoresearch`
2. **Read this file** completely.
3. **Read prior art**:
   - `results.tsv` if it exists — contains all prior runs. Identify the frontier per solver/equilibrium.
     - **Format check**: If the header is `run field_error score self_intersecting status description` (old 6-column format), all prior runs are Stage 2 / iota15. Rename it to `results_old.tsv` and create a fresh `results.tsv` with the new 8-column header. You can reference `results_old.tsv` for prior knowledge but log new runs to `results.tsv`.
   - Columbia DATABASE: 191 Stage 2 runs across 3 equilibria (default weights only, grid over MR/TF).
     ```bash
     find /Users/suhjungdae/code/columbia/DATABASE/COIL_OPTIMIZATION/outputs -name "results.json" | wc -l
     ```
   - 121 single-stage output dirs across 5 iota targets (0.15-0.25) and 5 volume targets.
4. **If no results.tsv exists**, create it with the header and run a first experiment:
   ```bash
   python scripts/run_one.py --solver stage2 --equilibrium iota15
   ```
5. **Start the loop.**

## Two Solvers

Stage 2 and single-stage are sequential in the physics pipeline. Stage 2 optimizes the coil geometry to minimize field error on the plasma surface. Single-stage takes that optimized coil and tests whether it produces good quasi-symmetric fields with the desired rotational transform (iota) and plasma volume. A coil that looks great in Stage 2 may fail in single-stage if the Boozer surface doesn't converge or the iota/volume targets aren't met. Use Stage 2 for fast exploration of coil geometry, then validate promising configs in single-stage.

### Stage 2: Pure Field Accuracy
Minimizes SquaredFlux (B·n on plasma surface) with engineering regularization.

```
J = SQUARED_FLUX_WEIGHT * SquaredFlux
  + LENGTH_WEIGHT * QuadraticPenalty(CurveLength, LENGTH_TARGET)
  + CC_WEIGHT * CurveCurveDistance(CC_THRESHOLD)
  + CURVATURE_WEIGHT * LpCurveCurvature(p=CURVATURE_P_NORM, threshold=CURVATURE_THRESHOLD)
```

**Primary metric**: FIELD_ERROR (lower = better). ~20-40s per run.

### Single-Stage: Full Quasi-Symmetry
Minimizes non-QS ratio + Boozer residual + iota/volume tracking + engineering constraints.

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

**Primary metrics**: FIELD_ERROR, FINAL_IOTA vs TARGET_IOTA, FINAL_VOLUME vs TARGET_VOLUME. ~2-10 min per run depending on mpol.

## Three Equilibria

| Name | File | Prior Stage 2 Runs | Best non-SI FE |
|------|------|--------------------|----------------|
| `iota15` | `wout_nfp22ginsburg_000_014417_iota15.nc` | 71 (DATABASE) + 200+ autoresearch runs | 0.00949 (DATABASE), 0.00429 (autoresearch frontier) |
| `iota20` | `wout_nfp22ginsburg_000_002084_iota20.nc` | 65 (DATABASE) | 0.01041 |
| `001490` | `wout_nfp22ginsburg_000_001490.nc` | 55 (DATABASE) | 0.01270 |

## Running an Experiment

**You MUST use `scripts/run_one.py` for every experiment. Do NOT call the solver directly.**

```bash
# Stage 2 with iota15 (default)
python scripts/run_one.py --cc-weight 44 --curvature-threshold 30

# Stage 2 with iota20
python scripts/run_one.py --equilibrium iota20 --cc-weight 50

# Single-stage
python scripts/run_one.py --solver single-stage --equilibrium iota20 \
  --iota-target 0.20 --vol-target 0.10 --mpol 8 --res-weight 500

# Run with all defaults (Stage 2, iota15, frontier params)
python scripts/run_one.py
```

Output is one line of JSON to stdout:
```json
{"solver": "stage2", "equilibrium": "iota15", "status": "pass", "field_error": 0.01194, "self_intersecting": false, "max_curvature": 30.54, "score": 0.7596, "iterations": 320, "elapsed": 42.3}
```

Single-stage adds: `final_iota`, `final_volume`, `target_iota`, `target_volume`.

### All Parameters

**Solver/equilibrium selection:**
| Flag | Values | Default |
|------|--------|---------|
| `--solver` | `stage2`, `single-stage` | `stage2` |
| `--equilibrium` | `iota15`, `iota20`, `001490`, or any .nc filename | `iota15` |

**Shared (both solvers):**
| Flag | Default | Notes |
|------|---------|-------|
| `--cc-weight` | 44.0 | Coil-coil spacing weight |
| `--curvature-weight` | 0.00085 | Curvature penalty weight |
| `--curvature-threshold` | 30.0 | Max curvature before penalty |
| `--banana-surf-radius` | 0.22 | Coil winding surface radius |
| `--major-radius` | 0.915 | Plasma major radius |
| `--toroidal-flux` | 0.215 | Flux surface label |
| `--order` | 2 | Fourier modes for coil shape |
| `--maxiter` | 400 | Optimizer iterations |
| `--nphi` | 127 | Toroidal resolution |
| `--ntheta` | 32 | Poloidal resolution |

**Stage 2 only:**
| Flag | Default | Notes |
|------|---------|-------|
| `--cc-threshold` | 0.05 | Coil-coil min distance (m) |
| `--length-weight` | 0.0001 | Curve length penalty |
| `--length-target` | 1.75 | Target coil length (m) |
| `--squared-flux-weight` | 1.0 | Weight on SquaredFlux term |
| `--curvature-p-norm` | 4 | Lp exponent for curvature penalty |
| `--num-quadpoints` | 128 | Coil discretization points |
| `--theta-center` | 0.5 | Coil poloidal center |
| `--phi-center` | 0.06 | Coil toroidal center |
| `--theta-width` | 0.1 | Coil poloidal width |
| `--phi-width` | 0.03 | Coil toroidal width |
| `--ftol` | 1e-15 | L-BFGS-B function tolerance |
| `--gtol` | 1e-15 | L-BFGS-B gradient tolerance |

**Single-stage only:**
| Flag | Default | Notes |
|------|---------|-------|
| `--iota-target` | 0.15 | Target rotational transform |
| `--vol-target` | 0.10 | Target plasma volume |
| `--mpol` | 8 | Poloidal Fourier resolution |
| `--ntor` | 6 | Toroidal Fourier resolution |
| `--constraint-weight` | 1.0 | Boozer constraint weight |
| `--cc-dist` | 0.05 | Coil-coil min distance |
| `--res-weight` | 1000 | Boozer residual weight |
| `--iotas-weight` | 100 | Iota tracking weight |
| `--cs-weight` | 1.0 | Coil-surface distance weight |
| `--cs-dist` | 0.02 | Coil-surface min distance (m) |
| `--surf-dist-weight` | 1000 | Surface-vessel distance weight |
| `--ss-dist` | 0.04 | Surface-vessel min distance (m) |
| `--ss-length-weight` | 1.0 | Curve length weight |
| `--maxcor` | 300 | L-BFGS-B memory |
| `--boozer-stage` | initial | `initial` or `final` |
| `--num-tf-coils` | 20 | TF coil count |
| `--stage2-source` | database | `database` or `local` |
| `--stage2-bs-path` | (auto) | Explicit Stage 2 seed path |

**Execution:**
| Flag | Default |
|------|---------|
| `--omp-threads` | 10 |
| `--timeout` | 600 |

## Scoring

**Stage 2**: `score = 1 / (1 + 25*FE + curvature_excess + 5*SI)`

**Single-stage**: `score = 1 / (1 + 25*FE + 4*|iota_miss| + 8*|vol_miss| + curvature_excess + 5*SI)`

**SELF_INTERSECTING = True → always discard.**

## Known Patterns (from 200+ autoresearch runs on iota15 Stage 2)

**Current frontier (Run 163): FE=0.00429, order=3, CCW=50, CW=0.005, CT=20, LW=1e-5, TF=0.215, CCT=0.021, maxiter=800.**

**Breakthroughs in order of discovery:**
1. `curvature_threshold=30` with `curvature_weight≈0.0008` prevents self-intersection (Run 3).
2. `cc_weight≈44-50` sweet spot, `toroidal_flux=0.215` sharp optimum (Runs 50-68).
3. `order=3` halved field error from ~0.012 to ~0.006 (Run 137). Requires `CT=20`, `CW=0.005` to avoid SI.
4. `length_weight=1e-5` halved field error again from ~0.006 to ~0.004 (Run 154). Sharp optimum — LW=1.2e-5 self-intersects.

**Basin non-determinism:** At the order=3 frontier, the L-BFGS-B optimizer is sensitive to numerical noise. The same params can produce FE=0.004 (good basin) or FE=0.015 (bad basin). Replications often land in intermediate basins (FE≈0.006-0.008). The frontier result FE=0.00429 has been reproduced but not consistently.

**Unexplored directions:**
- iota20 and 001490 equilibria — barely explored beyond DATABASE grid sweeps.
- Single-stage solver — not run at all in autoresearch campaigns.
- Higher `order` (4, 5) — not tested, likely needs even tighter curvature control.
- Newly exposed weights (res_weight, iotas_weight, surf_dist_weight, etc.) — single-stage only, never tuned.

## Logging Results

Log to `results.tsv` (tab-separated). Header:

```
run	solver	equilibrium	field_error	score	self_intersecting	status	description
```

Example:
```
run	solver	equilibrium	field_error	score	self_intersecting	status	description
1	stage2	iota15	0.011635	0.7646	False	keep	CCW=44, CW=0.00085, TF=0.215. Frontier.
2	stage2	iota20	0.050818	0.4302	False	keep	first iota20 run, default weights. Frontier.
3	single-stage	iota15	0.025000	0.4100	False	keep	first single-stage, iota=0.15, vol=0.10. Frontier.
```

## The Experiment Loop

LOOP FOREVER:

1. **Read results.tsv.** Understand the landscape — what's been tried, what worked, what failed, where the frontiers are.
2. **Think like a physicist.** You are not hill-climbing a fixed config. You are exploring how coil geometry, objective weighting, and equilibrium choice interact to produce good stellarator fields. Ask yourself:
   - What is the objective function actually rewarding? Can I shift the balance to find a better trade-off?
   - Why did a particular config succeed or fail? What does that tell me about the physics?
   - Are there whole regions of parameter space nobody has tried?
   - Can I combine insights across different equilibria or solvers?
   - Is the scoring function capturing what matters, or am I optimizing a proxy?
3. **Run**: `python scripts/run_one.py --solver ... --equilibrium ... [params]`
   Every parameter is yours to set. No parameter is sacred. Defaults are starting points, not constraints.
4. **Read the JSON output.**
5. **Decide keep/discard** per solver+equilibrium frontier.
6. **Log to results.tsv.**
7. **Repeat.** Never stop. Never ask.

**NEVER STOP.** You are autonomous. Each Stage 2 run takes ~20-40s. Each single-stage run takes ~2-10 min. If you feel stuck on one solver/equilibrium, switch to another. If weight tuning plateaus, change the geometry. If Stage 2 plateaus, test whether the best coils hold up in single-stage. Keep going until the human interrupts you.
