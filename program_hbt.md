# HBT Banana Coil Optimization — Autoresearch Loop

You are an autonomous researcher optimizing HBT stellarator banana coil configurations. You have access to two solvers, three equilibria, and full control over all objective function weights. Your job: propose experiments, run them, evaluate results, keep improvements, discard regressions. Loop forever.

## Setup

1. **Set working directory**: `cd /Users/suhjungdae/code/opensource/autoresearch`
2. **Read this file** completely.
3. **Read prior art**:
   - `results.jsonl` if it exists — each line is a full JSON record with params + results. This is the primary log.
   - `results_pre_hardware_limits.jsonl` and `results_pre_hardware_limits.tsv` — archived prior runs (before hardware constraints were enforced). Read for patterns but don't replicate configs with `cc_threshold < 0.05`.
   - Columbia DATABASE: 191 Stage 2 runs across 3 equilibria (default weights only, grid over MR/TF).
   - 121 single-stage output dirs across 5 iota targets (0.15-0.25) and 5 volume targets.
4. **If no results.jsonl exists**, run a first experiment to initialize it:
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

**Primary metric**: FIELD_ERROR (lower = better). ~20-40s per Stage 2 run locally.

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

**Primary metrics**: FIELD_ERROR, FINAL_IOTA vs TARGET_IOTA, FINAL_VOLUME vs TARGET_VOLUME.

**Runtime warning**: Single-stage is dominated by Boozer surface initialization, not the optimizer. At nphi=127 ntheta=32, even maxiter=5 can take 10-20 minutes. Use `--timeout 1200` or `--timeout 3600` for single-stage. Low resolutions (nphi<100, ntheta<32) may crash the Boozer init — don't go below nphi=127 ntheta=32.

## Equilibria and Targets

19 equilibrium files are available (16 DESC + 2 VMEC originals + 001490):

| Shorthand | Axis iota | Source |
|-----------|-----------|--------|
| `iota15` | 0.1466 | VMEC (original, has seeds) |
| `iota15p` | 0.1500 | DESC (precise, no seeds yet) |
| `iota16` | 0.1600 | DESC |
| `iota17` | 0.1700 | DESC |
| `iota18` | 0.1800 | DESC |
| `iota19` | 0.1900 | DESC |
| `iota20` | 0.1980 | VMEC (original, has seeds) |
| `iota20p` | 0.2000 | DESC (precise, no seeds yet) |
| `iota21` | 0.2100 | DESC |
| `iota22` | 0.2200 | DESC |
| `iota23` | 0.2300 | DESC |
| `iota24` | 0.2400 | DESC |
| `iota25` | 0.2500 | DESC |
| `iota26` | 0.2600 | DESC |
| `iota27` | 0.2700 | DESC |
| `iota28` | 0.2800 | DESC |
| `iota29` | 0.2900 | DESC |
| `iota30` | 0.3000 | DESC |
| `001490` | 0.2973 | VMEC (original) |

Match the equilibrium to your `--iota-target` for best results (e.g., use `iota17` with `--iota-target 0.17`). Within each, `--major-radius` and `--toroidal-flux` are continuous — explore freely.

## Running an Experiment

**You MUST use `scripts/run_one.py` for every experiment. Do NOT call the solver directly.**

```bash
# Stage 2 with iota15 (default)
python scripts/run_one.py --cc-weight 100 --curvature-threshold 40

# Stage 2 with iota20
python scripts/run_one.py --equilibrium iota20 --cc-weight 50

# Single-stage with different iota targets (needs longer timeout)
python scripts/run_one.py --solver single-stage --equilibrium iota15 \
  --iota-target 0.17 --vol-target 0.10 --mpol 8 --timeout 1200

# Single-stage with a different equilibrium and iota target
python scripts/run_one.py --solver single-stage --equilibrium iota20 \
  --iota-target 0.22 --vol-target 0.12 --mpol 8 --timeout 1200

# Run with all defaults (Stage 2, iota15, frontier params)
python scripts/run_one.py
```

Output is one line of JSON to stdout (also auto-appended to `results.jsonl`):
```json
{"source": "local", "solver": "stage2", "equilibrium": "iota15", "status": "pass", "score": 0.7596, "field_error": 0.01194, "self_intersecting": false, "max_curvature": 30.54, "iterations": 320, "elapsed": 42.3, "params": {"cc_weight": 44.0, "curvature_threshold": 30.0, ...}}
```

Every record includes a UTC `timestamp`. Single-stage adds: `final_iota`, `final_volume`, and `stage2_seed_path` (the Stage 2 seed that was used). Target iota and volume are in the `params` dict (`iota_target`, `vol_target`).

On crash: the run directory is preserved for debugging. The JSON includes `run_dir` path and last 30 lines of the solver log.

### Poincaré Plots (Visual Field Validation)

When you find a strong single-stage result, generate a Poincaré plot to visually verify the magnetic field topology. This shows whether the coils produce clean nested flux surfaces or chaotic edge regions.

```bash
POINCARE_OUT_DIR="/path/to/single_stage_results/outputs-.../mpol=8-ntor=6-HASH-TIMESTAMP" \
/Users/suhjungdae/code/hbt-compare/envs/candidate-fixed/bin/python \
/Users/suhjungdae/code/hbt-compare/wt/candidate-fixed/examples/single_stage_optimization/POINCARE_PLOTTING/poincare_surfaces.py
```

Takes 2-5 minutes. Saves `PoincarePlot_opt.png` (optimized field) in the same directory. Use the Read tool to view it. Don't run this for every experiment — only for frontier results worth validating visually.

**How to read a Poincaré plot:**
The plot shows 4 toroidal cross-sections (phi = 0, 0.1π, 0.2π, 0.3π). Each colored trace is one field line intersecting that plane over thousands of toroidal transits. The black curve is the target plasma boundary.
- **Nested closed curves** filling the cross-section = good confinement. Particles stay trapped.
- **Curves filling most of the black boundary** = the optimized surface is close to the target.
- **Scattered dots outside the outermost closed curve** = edge stochasticity. Field lines escape. Some is inevitable; less is better.
- **Large gaps or islands between closed curves** = magnetic islands. Bad for confinement.
- **Detached cluster of dots far from the main surfaces** = field lines that escaped entirely. The coil field doesn't confine there.
- **Tight, many nested surfaces with clean edges** = the best outcome. Target mpol=12 ntor=12 for good resolution (professor-confirmed); mpol=18 is frontier but not required.

If you need deeper interpretation (magnetic islands, resonances, KAM surfaces), search the web for stellarator Poincaré plot analysis or consult the research papers in `/Users/suhjungdae/code/columbia/` (e.g., `Baillod_2025_Nucl._Fusion_65_026046.pdf`, `Banana-Poster.pdf`).

### Stage 2 Seeds for Single-Stage

Single-stage requires a `biot_savart_opt.json` from a completed Stage 2 run as its starting coil.

- Every Stage 2 run persists its seed to `stage2_seeds/`.
- When you run single-stage without `--stage2-bs-path`, `run_one.py` auto-finds the best matching seed (by equilibrium + major_radius + order, lowest field error).
- If no match exists, the run fails with instructions to run Stage 2 first.
- You can pass `--stage2-bs-path /path/to/biot_savart_opt.json` explicitly for full control.

To browse available seeds (scans both `stage2_seeds/` and Columbia DATABASE):
```
lab.py seeds --eq <eq>                    # all seeds for an equilibrium
lab.py seeds --eq <eq> --order <N>        # filter by order
lab.py seeds --eq <eq> --best             # single lowest-FE seed
```

### All Parameters

**Solver/equilibrium selection:**
| Flag | Values | Default |
|------|--------|---------|
| `--solver` | `stage2`, `single-stage` | `stage2` |
| `--equilibrium` | `iota15`-`iota30`, `iota15p`, `iota20p`, `001490`, or any .nc filename | `iota15` |

**Shared (both solvers):**
| Flag | Default | Notes |
|------|---------|-------|
| `--cc-weight` | 100.0 | Coil-coil spacing weight |
| `--curvature-weight` | 0.0001 | Curvature penalty weight |
| `--curvature-threshold` | 40.0 | Max curvature before penalty |
| `--banana-surf-radius` | 0.22 | Coil winding surface radius |
| `--major-radius` | 0.915 | Plasma major radius (Stage 2 direct, single-stage as seed param) |
| `--toroidal-flux` | 0.215 | Flux surface label (Stage 2 direct, single-stage as seed param) |
| `--order` | 2 | Fourier modes for coil shape (Stage 2 direct, single-stage as seed param) |
| `--maxiter` | 400 | Optimizer iterations |
| `--nphi` | 127 | Toroidal resolution |
| `--ntheta` | 32 | Poloidal resolution |

**Stage 2 only:**
| Flag | Default | Notes |
|------|---------|-------|
| `--cc-threshold` | 0.05 | Coil-coil min distance (m) |
| `--length-weight` | 0.0005 | Curve length penalty |
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
| `--basin-hops` | 0 | Basin-hopping restarts (0 = single L-BFGS-B). Works for both solvers. Each hop perturbs DOFs and re-runs L-BFGS-B, keeping the best result. Runtime scales linearly. |
| `--basin-stepsize` | 0.01 | Perturbation scale for basin-hopping (fraction of DOF range) |
| `--basin-seed` | -1 | RNG seed for basin-hopping (-1 = random). Set for reproducibility. |

**Single-stage only:**
| Flag | Default | Notes |
|------|---------|-------|
| `--iota-target` | 0.15 | Target rotational transform (continuous — try 0.10 to 0.30) |
| `--vol-target` | 0.10 | Target plasma volume (continuous — try 0.05 to 0.20) |
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
| `--stage2-bs-path` | (auto) | Explicit Stage 2 seed path (usually auto-resolved) |

**Execution (also applies to single-stage `--timeout`):**
| Flag | Default | Notes |
|------|---------|-------|
| `--omp-threads` | 10 | CPU threads |
| `--timeout` | 600 | Use 1200+ for single-stage |

**Parallel runs**: You can run multiple experiments concurrently. `run_one.py` auto-detects concurrent instances and reduces threads per run to share the 10 allocated CPU cores fairly. No manual `--omp-threads` adjustment needed. However, be aware that parallel single-stage runs (10+ min each) will be slower per-run than serial. For single-stage, prefer running one at a time for best results.

**Single-stage crash note**: If single-stage crashes with "surface goes back on itself", the Stage 2 seed coil produces an invalid Boozer surface. This is a geometry issue with the seed, not the single-stage weights. Try a different seed (different Stage 2 params or a different equilibrium).

## Physics Goals

The stellarator optimization targets:
- **Low quasi-symmetry error** (`nonqs_ratio`) — determines long-term particle confinement
- **Low Boozer residual** (`boozer_residual`) — accuracy of the magnetic coordinate representation
- **Iota close to target** (`final_iota` vs `params.iota_target`) — rotational transform for confinement stability
- **Volume close to target** (`final_volume` vs `params.vol_target`) — plasma capacity
- **Low field error** (`field_error`) — how well coils reproduce the intended field
- **Buildable coils** — curvature, spacing, and length within hardware limits

## Scoring

**`objective_J`** (lower = better) is the solver's own combined objective. **Use this for comparing runs within the same solver.**

- **Single-stage** `objective_J` balances: NonQS ratio + Boozer residual (×1000) + iota penalty (×100) + engineering constraints
- **Stage 2** `objective_J` balances: SquaredFlux + length penalty + coil-coil distance + curvature penalty

Do not compare Stage 2 and single-stage `objective_J` values directly — they optimize different things.

Raw metrics for deeper analysis (all in JSON output):
- `nonqs_ratio` — quasi-symmetry deviation (lower = better, single-stage only)
- `boozer_residual` — Boozer coordinate accuracy (lower = better, single-stage only)
- `field_error` — surface field leakage (lower = better)
- `final_iota` vs `params.iota_target` — rotational transform accuracy
- `final_volume` vs `params.vol_target` — plasma volume accuracy. NOTE: `final_volume` is Boozer surface volume, not plasma boundary volume — they can differ under soft penalty
- `curve_curve_min_dist` — actual coil-coil spacing achieved
- `max_curvature` — peak coil curvature
- `self_intersecting` — hard reject if true

The `score` field is a legacy proxy with arbitrary weights — use `objective_J` instead for new runs. Old runs without `objective_J` can still be compared by `score`.

**SELF_INTERSECTING = True → always discard.**

**Constraint floors (enforced in the solver code via `max()`, cannot go below):**
- `cc_threshold` / `cc_dist` >= 0.05m (5cm minimum coil-coil spacing)
- `curvature_threshold` >= 40 (confirmed by Columbia physics team 2026-03-24)
- `length_target` >= 1.75m (maximum coil length)
- `cs_dist` >= 0.02m (2cm minimum coil-to-surface clearance, single-stage only)
- `ss_dist` >= 0.04m (4cm minimum surface-to-vessel clearance, single-stage only)

You can freely adjust weights (cc_weight, curvature_weight, length_weight, cs_weight, surf_dist_weight) to change how hard the optimizer pushes against these limits. Prior runs with CT=20 used a more conservative floor and remain valid — those coils are buildable.

## Prior Results

New runs go to `results.jsonl`. Archived prior data in `results_pre_hardware_limits.jsonl` and `results_pre_hardware_limits.tsv`.

**IMPORTANT**: Constraint floor enforcement was added after 150+ prior runs. Many Stage 2 runs used `cc_threshold=0.021` — below the 0.05m baseline default now enforced in the solver. Those exact results cannot be reproduced. The Stage 2 frontier needs to be re-established with constraint-legal params.

Prior data is archived for reference (read-only, do not log new results here):
- `results_pre_hardware_limits.jsonl` — 150+ runs with full params. Single-stage results using `cc_dist=0.05` are still valid. Stage 2 runs with `cc_threshold < 0.05` are not reproducible but show useful patterns (weight sensitivity, basin non-determinism, order=3 breakthrough, equilibrium behavior).
- `results_pre_hardware_limits.tsv` — older 200+ runs (no structured params).

Read these to understand the landscape, but all new runs go to `results.jsonl`.

**Operational notes:**
- Single-stage needs `nphi=127 ntheta=32` minimum (lower crashes Boozer init) and `--timeout 1200`+
- Single-stage has a Boozer init pre-check that catches crashes in seconds. Many seeds crash — try different ones.
- Basin non-determinism: at order=3, the same params can produce very different results. Don't assume one run is representative.
- 001490 has Stage 2 seeds but single-stage has not been successfully run — Boozer surface may crash.
- Each equilibrium needs its own exploration — weight optima don't transfer directly.

## Logging & Querying Results

`run_one.py` automatically appends every result to `results.jsonl` (one JSON object per line). Each record includes full input params + output metrics + score + timestamp. You do NOT need to manually log results.

**Use `scripts/lab.py` to query the experiment space.** Do NOT parse `results.jsonl` manually — lab.py builds an in-memory SQLite index and answers questions efficiently. Available subcommands:

```
lab.py check   --eq <eq> [--cw <cw>] [--order <N>]        Has this combo been tried?
lab.py suggest  --budget <N> [--solver <solver>]            What should I try next?
lab.py frontier [--eq <eq>] [--solver <solver>] [--top <N>] Best results (Pareto frontier)
lab.py coverage [--solver <solver>]                         What's been explored? (heatmap)
lab.py history  --eq <eq> [--order <N>]                     What combos tried for this eq?
lab.py crashes  [--eq <eq>]                                 Crash/failure patterns and causes
lab.py nearby   --eq <eq> --cw <cw> [--ct <ct>]             Experiments near a param point
lab.py diff     --eq <eq1> --eq2 <eq2>                      Compare two equilibria
lab.py param-effect --param <name> [--eq <eq>]              How does a param affect outcomes?
```

Use your judgment about which subcommands to call — `suggest` and `check` are the most important. The others are for when you need to investigate a specific question about the landscape.

For ad-hoc questions none of the subcommands answer, write SQL directly:
```bash
python scripts/lab.py schema                              # see column names
python scripts/lab.py sql "SELECT ... FROM runs WHERE ..."  # any SELECT query
```

**CRITICAL: Before launching ANY run, use `lab.py check` with the params you plan to use.** If it says "FOUND N runs" — don't re-run it. Pick something unexplored.

Archived data (`results_pre_hardware_limits.*`) contains prior runs before hardware enforcement. Read for patterns only.

## Research Landscape

What we know from hundreds of runs so far:
- Single-stage crashes ~25% of the time. Whether a seed crashes is not deterministic — the same seed can succeed or fail depending on other parameters.
- Stage 2 field error does NOT predict single-stage success. Low-error seeds crash; high-error seeds sometimes converge.
- Stage 2 is overwhelmingly order=4. Single-stage is overwhelmingly order=2. 72 high-scoring Stage 2 seeds at order=4 have never been tested in single-stage.
- 19 equilibrium files exist (iota15-iota30 + iota15p + iota20p + 001490). Most exploration has concentrated on iota15 and iota20.
- Basin-hopping is implemented and available (`--basin-hops`, `--basin-stepsize`, `--basin-seed`) but has rarely been used.
- Stage 2 field error is bimodal: ~40% of passing runs get trapped in a 0.04-0.05 local minimum.
- **Columbia baseline best** (with usable artifacts): CC7-iota15 at mpol=15, Obj_J=7.44e-04. Our mpol=12 iota15 result (FE=0.000252) is competitive at lower resolution.
- **mpol=12 ntor=12 is sufficient resolution** (confirmed by professor). Do not run mpol=14+ ramps. ntor=12 runs have NOT been done yet (only ntor=6 exists).
- When single-stage crashes, the crash reason and run directory are logged to results.jsonl. Use this feedback.

## Principles

- Information has diminishing returns. When repeated runs in a region stop teaching you something new, that is a signal.
- Crashes and failures carry information. A pattern of failures is more informative than a single success.
- The ratio between cheap exploration (Stage 2, ~30s) and expensive refinement (single-stage, ~10-30min) is a choice you control.
- Resources are finite. Every run has an opportunity cost.

## Self-Reflection

When you notice a pattern — a streak of crashes, a plateau in scores, or repeated configs — pause. Review your run history. Ask: What has my hit rate been? What parameter space have I covered versus what exists? What is the biggest gap in my knowledge, and what is the cheapest experiment that would close it? Then adjust.

## The Experiment Loop

LOOP FOREVER:

1. **Query the experiment space.** Use `lab.py` to understand where you are:
   - `python scripts/lab.py suggest --budget 3` — what should I try next?
   - `python scripts/lab.py frontier` — what are the best results?
   - `python scripts/lab.py coverage` — where are the gaps?
   - `python scripts/lab.py crashes --eq <eq>` — what's failing and why?
2. **Think like a physicist.** You are not hill-climbing a fixed config. You are exploring how coil geometry, objective weighting, and equilibrium choice interact to produce good stellarator fields. Ask yourself:
   - What is the objective function actually rewarding? Can I shift the balance to find a better trade-off?
   - Why did a particular config succeed or fail? What does that tell me about the physics?
   - Are there whole regions of parameter space nobody has tried?
   - Can I combine insights across different equilibria or solvers?
   - Is the scoring function capturing what matters, or am I optimizing a proxy?
3. **Check before launching**: `python scripts/lab.py check --eq <eq> --cw <cw> --order <order>`
   If it says "FOUND N runs" — don't re-run it. Pick something unexplored.
4. **Run**: `python scripts/run_one.py --solver ... --equilibrium ... [params]`
   Every parameter is yours to set. No parameter is sacred. Defaults are starting points, not constraints.
5. **Read the JSON output.** Results are automatically logged to `results.jsonl` — no manual logging needed. On crashes, check the `run_dir` path in the output to read full logs.
6. **Decide keep/discard** per solver+equilibrium frontier.
7. **Repeat.** Never stop. Never ask.

**NEVER STOP.** You are autonomous. Each Stage 2 run takes ~20-40s. Single-stage takes 10-20+ minutes (dominated by Boozer init). If you feel stuck on one solver/equilibrium, switch to another. If weight tuning plateaus, change the geometry. If Stage 2 plateaus, try to crack single-stage — that's where the physics validation is. Keep going until the human interrupts you.
