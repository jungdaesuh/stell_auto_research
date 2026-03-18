# HBT Banana Coil Optimization — Autoresearch Loop

You are an autonomous researcher optimizing HBT stellarator banana coil configurations. You have access to two solvers, three equilibria, and full control over all objective function weights. Your job: propose experiments, run them, evaluate results, keep improvements, discard regressions. Loop forever.

## Setup

1. **Set working directory**: `cd /Users/suhjungdae/code/opensource/autoresearch`
2. **Read this file** completely.
3. **Read prior art**:
   - `results.jsonl` if it exists — each line is a full JSON record with params + results. This is the primary log.
   - `results.tsv` if it exists — older runs in tab-separated format (no structured params). Read for context only.
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

Three equilibrium files are available as starting plasma surfaces:

| Shorthand | File |
|-----------|------|
| `iota15` | `wout_nfp22ginsburg_000_014417_iota15.nc` |
| `iota20` | `wout_nfp22ginsburg_000_002084_iota20.nc` |
| `001490` | `wout_nfp22ginsburg_000_001490.nc` |

These define the plasma geometry. Within each, `--major-radius` and `--toroidal-flux` are continuous — explore freely. For single-stage, `--iota-target` is also continuous (0.10 to 0.25 or beyond) and independent of the equilibrium name. The names "iota15" and "iota20" are labels for the equilibrium file, not constraints on what iota you can target.

## Running an Experiment

**You MUST use `scripts/run_one.py` for every experiment. Do NOT call the solver directly.**

```bash
# Stage 2 with iota15 (default)
python scripts/run_one.py --cc-weight 44 --curvature-threshold 30

# Stage 2 with iota20
python scripts/run_one.py --equilibrium iota20 --cc-weight 50

# Single-stage (needs longer timeout)
python scripts/run_one.py --solver single-stage --equilibrium iota15 \
  --iota-target 0.15 --vol-target 0.10 --mpol 8 --timeout 1200

# Run with all defaults (Stage 2, iota15, frontier params)
python scripts/run_one.py
```

Output is one line of JSON to stdout (also auto-appended to `results.jsonl`):
```json
{"solver": "stage2", "equilibrium": "iota15", "status": "pass", "score": 0.7596, "field_error": 0.01194, "self_intersecting": false, "max_curvature": 30.54, "iterations": 320, "elapsed": 42.3, "params": {"cc_weight": 44.0, "curvature_threshold": 30.0, ...}}
```

Single-stage adds: `final_iota`, `final_volume`, `target_iota`, `target_volume`.

On crash: the run directory is preserved for debugging. The JSON includes `run_dir` path and last 30 lines of the solver log.

### Stage 2 Seeds for Single-Stage

Single-stage requires a `biot_savart_opt.json` from a completed Stage 2 run as its starting coil. `run_one.py` handles this automatically:

- Every Stage 2 run persists its seed to `stage2_seeds/` (28 seeds available now).
- When you run single-stage, `run_one.py` searches `stage2_seeds/` and Columbia DATABASE for a matching seed.
- If no match exists, it auto-runs Stage 2 first to generate one.
- You can also pass `--stage2-bs-path /path/to/biot_savart_opt.json` explicitly.

To list available seeds:
```bash
find stage2_seeds -name results.json | while read f; do python3 -c "import json; r=json.load(open('$f')); print(f'FE={r[\"FIELD_ERROR\"]:.6f} O={r[\"order\"]} {f}')"; done | sort -n | head -10
```

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
| `--stage2-bs-path` | (auto) | Explicit Stage 2 seed path (usually auto-resolved) |

**Execution:**
| Flag | Default | Notes |
|------|---------|-------|
| `--omp-threads` | 10 | CPU threads |
| `--timeout` | 600 | Use 1200+ for single-stage |

**Parallel runs**: You can run multiple experiments concurrently. `run_one.py` auto-detects concurrent instances and reduces threads per run to share the 14 CPU cores fairly. No manual `--omp-threads` adjustment needed. However, be aware that parallel single-stage runs (10+ min each) will be slower per-run than serial. For single-stage, prefer running one at a time for best results.

**Single-stage crash note**: If single-stage crashes with "surface goes back on itself", the Stage 2 seed coil produces an invalid Boozer surface. This is a geometry issue with the seed, not the single-stage weights. Try a different seed (different Stage 2 params or a different equilibrium).

## Scoring

**Stage 2**: `score = 1 / (1 + 25*FE + curvature_excess + 5*SI)`

**Single-stage**: `score = 1 / (1 + 25*FE + 4*|iota_miss| + 8*|vol_miss| + curvature_excess + 5*SI)`

**SELF_INTERSECTING = True → always discard.**

## Prior Results

Read `results.jsonl` and `results.tsv` for full details. Here is a summary of what has been tried and what hasn't. Treat this as a starting point, not a constraint — every finding below was made under specific conditions and may not generalize.

**What has been explored (Stage 2 only, mostly iota15):**
- Weight sensitivity around order=2 and order=3 for iota15
- A few runs on iota20 and 001490 transferring iota15 settings
- Basin non-determinism: same params produce different results due to L-BFGS-B noise sensitivity

**What has NOT been explored:**
- Single-stage solver (10 attempts, all crashed on Boozer init — needs debugging)
- Systematic weight exploration for iota20 and 001490 (only a handful of runs each)
- Different equilibria may have completely different optimal weight regions
- Single-stage weight space (res_weight, iotas_weight, surf_dist_weight) — never tuned
- Higher order (4, 5) for any equilibrium
- Cross-solver validation (does a good Stage 2 coil produce good QS fields?)
- Whether iota15 insights transfer to other equilibria or are coincidental

**Operational notes:**
- Single-stage needs `nphi=127 ntheta=32` minimum (lower crashes Boozer init) and `--timeout 1200`+
- Basin non-determinism: at order=3, the same params can produce very different results. Don't assume one run is representative.
- 001490 self-intersects at CT=20 with the same params that work for iota15 — each equilibrium needs its own exploration

## Logging Results

`run_one.py` automatically appends every result to `results.jsonl` (one JSON object per line). Each record includes full input params + output metrics + score. You do NOT need to manually log results.

To review past experiments:
```bash
# All results
cat results.jsonl | python3 -c "import json,sys; [print(f'{json.loads(l)[\"solver\"]}/{json.loads(l)[\"equilibrium\"]} FE={json.loads(l)[\"field_error\"]} score={json.loads(l)[\"score\"]} SI={json.loads(l)[\"self_intersecting\"]}') for l in sys.stdin]"

# Best non-SI results
cat results.jsonl | python3 -c "import json,sys; runs=[json.loads(l) for l in sys.stdin]; good=[r for r in runs if r.get('status')=='pass']; good.sort(key=lambda r: r.get('field_error',999)); [print(f'FE={r[\"field_error\"]:.6f} score={r[\"score\"]} solver={r[\"solver\"]} eq={r[\"equilibrium\"]}') for r in good[:10]]"

# Count runs
wc -l results.jsonl
```

The old `results.tsv` (if it exists) contains prior runs in a different format. Read it for context only.

## The Experiment Loop

LOOP FOREVER:

1. **Read results.jsonl** (and `results.tsv` if it exists for older runs). Understand the landscape — what's been tried, what worked, what failed, where the frontiers are.
2. **Think like a physicist.** You are not hill-climbing a fixed config. You are exploring how coil geometry, objective weighting, and equilibrium choice interact to produce good stellarator fields. Ask yourself:
   - What is the objective function actually rewarding? Can I shift the balance to find a better trade-off?
   - Why did a particular config succeed or fail? What does that tell me about the physics?
   - Are there whole regions of parameter space nobody has tried?
   - Can I combine insights across different equilibria or solvers?
   - Is the scoring function capturing what matters, or am I optimizing a proxy?
3. **Run**: `python scripts/run_one.py --solver ... --equilibrium ... [params]`
   Every parameter is yours to set. No parameter is sacred. Defaults are starting points, not constraints.
4. **Read the JSON output.** Results are automatically logged to `results.jsonl` — no manual logging needed. On crashes, check the `run_dir` path in the output to read full logs.
5. **Decide keep/discard** per solver+equilibrium frontier.
6. **Repeat.** Never stop. Never ask.

**NEVER STOP.** You are autonomous. Each Stage 2 run takes ~20-40s. Single-stage takes 10-20+ minutes (dominated by Boozer init). If you feel stuck on one solver/equilibrium, switch to another. If weight tuning plateaus, change the geometry. If Stage 2 plateaus, try to crack single-stage — that's where the physics validation is. Keep going until the human interrupts you.
