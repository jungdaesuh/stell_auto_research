# HBT Stage 2 Banana Coil Optimization — Autoresearch Loop

You are an autonomous researcher optimizing HBT stellarator banana coil configurations. Your job: propose parameter configs, run experiments, evaluate results, keep improvements, discard regressions. Loop forever.

## Setup

1. **Set working directory**: `cd /Users/suhjungdae/code/opensource/autoresearch`
2. **Read this file** and understand the parameter space, scoring, and constraints.
3. **Read prior art** from the Columbia DATABASE:
   ```bash
   # 191 existing Stage 2 runs (iota20 equilibrium, default weights)
   # Best: FIELD_ERROR=0.0095, non-self-intersecting
   # Reference only — they used iota20, we use iota15. Weight patterns transfer.
   for f in $(find /Users/suhjungdae/code/columbia/DATABASE/COIL_OPTIMIZATION/outputs -name "results.json" | sort | head -5); do
     python3 -c "import json; r=json.load(open('$f')); print(f'FE={r.get(\"FIELD_ERROR\",0):.6f} SI={r.get(\"SELF_INTERSECTING\",\"?\")} MR={r.get(\"MAJOR_RADIUS\",\"?\")} CCW={r.get(\"CC_WEIGHT\",\"?\")} CT={r.get(\"CURVATURE_THRESHOLD\",\"?\")}')"
   done
   ```
4. **Read results.tsv** if it exists. It contains all prior runs with field_error, score, self_intersecting, status, and description. Identify the current frontier (best non-SI result). The last run number tells you where to continue numbering.
5. **If no results.tsv exists**, create it with the header row, then run a first experiment with known-good params:
   ```bash
   python scripts/run_one.py --cc-weight 45 --curvature-weight 0.0008 --curvature-threshold 30 --length-weight 0.0001 --toroidal-flux 0.22 --maxiter 400
   ```
   (The solver defaults self-intersect — do not run with defaults.)
6. **Start the loop.**

## Running an Experiment

Use `scripts/run_one.py`. It handles output isolation, result parsing, scoring, and cleanup. You just pass parameters and read the JSON output.

```bash
python scripts/run_one.py \
  --cc-weight 44 --curvature-weight 0.00085 --curvature-threshold 30 \
  --length-weight 0.0001 --toroidal-flux 0.22 --maxiter 400
```

Output (one line of JSON to stdout):
```json
{"status": "pass", "field_error": 0.01194, "self_intersecting": false, "max_curvature": 30.54, "score": 0.7596, "iterations": 320, "elapsed": 42.3, "run_dir": "/tmp/hbt_autoresearch/run_1773741898402", "feedback": "..."}
```

- `status`: "pass" (valid), "fail" (self-intersecting), or "crash" (solver error)
- `field_error`: the primary metric, lower is better
- `self_intersecting`: must be false for a valid result
- `score`: combined score (higher is better), 0 for crashes
- `elapsed`: wall time in seconds

**You MUST use `scripts/run_one.py` for every experiment. Do NOT call the solver directly.** The script isolates each run in a unique directory, parses results, computes scores, and cleans up. Calling the solver directly causes output directory collisions where runs overwrite each other's results, producing corrupted data. Just read the JSON output from run_one.py.

### Available parameters:

| Parameter | Flag | Range | Default | Effect |
|-----------|------|-------|---------|--------|
| `cc_weight` | `--cc-weight` | [10, 1000] | 100 | Coil-coil spacing penalty weight |
| `cc_threshold` | `--cc-threshold` | [0.02, 0.15] | 0.05 | Min coil-coil distance (m) |
| `curvature_weight` | `--curvature-weight` | [1e-6, 0.01] | 0.0001 | Curvature penalty weight |
| `curvature_threshold` | `--curvature-threshold` | [10, 100] | 40 | Max curvature before penalty |
| `length_weight` | `--length-weight` | [1e-6, 1e-2] | 0.0005 | Coil length penalty weight |
| `length_target` | `--length-target` | [1.0, 3.0] | 1.75 | Target coil length (m) |
| `banana_surf_radius` | `--banana-surf-radius` | [0.15, 0.30] | 0.22 | Coil winding surface minor radius (m) |
| `major_radius` | `--major-radius` | [0.85, 1.00] | 0.915 | Plasma major radius (m) |
| `toroidal_flux` | `--toroidal-flux` | [0.15, 0.35] | 0.24 | Flux surface label |
| `order` | `--order` | [1, 5] | 2 | Fourier modes for coil shape |
| `theta_center` | `--theta-center` | — | 0.5 | Coil poloidal center (leave at default) |
| `phi_center` | `--phi-center` | — | 0.06 | Coil toroidal center (leave at default) |
| `theta_width` | `--theta-width` | — | 0.1 | Coil poloidal width (leave at default) |
| `phi_width` | `--phi-width` | — | 0.03 | Coil toroidal width (leave at default) |

### Execution parameters:

| Flag | Default | Notes |
|------|---------|-------|
| `--maxiter` | 400 | Optimizer iterations. Higher = better convergence, slower. |
| `--nphi` | 127 | Toroidal resolution. Don't change without reason. |
| `--ntheta` | 32 | Poloidal resolution. Don't change without reason. |
| `--omp-threads` | 10 | CPU threads. This machine has 14 cores. |
| `--timeout` | 600 | Kill after this many seconds. |

## The Physics Problem

You are optimizing "banana coils" — stellarator coils producing quasi-symmetric fields inside HBT. The solver (L-BFGS-B) minimizes:

- **SquaredFlux** (B·n on plasma surface) — lower field error = better confinement
- **CurveLength** penalty — prevents excessively long coils
- **CurveCurveDistance** penalty — prevents coils from getting too close
- **LpCurveCurvature** penalty — prevents sharp bends (unfabricatable)

## Scoring

**combined score = 1 / (1 + penalty)**. Higher is better. Range (0, 1].

```
penalty = 25.0 * FIELD_ERROR
if MAX_CURVATURE > curvature_threshold:
    penalty += (MAX_CURVATURE - curvature_threshold) / curvature_threshold
if SELF_INTERSECTING:
    penalty += 5.0
```

**SELF_INTERSECTING = True → always discard, score = 0.**

## Known Patterns and Failure Modes

**What works:**
- Tighter `curvature_threshold` (20-30 instead of 40) prevents self-intersection.
- `cc_weight` in [44-50] with `curvature_weight` ~0.0008-0.00085 is the current sweet spot.
- `toroidal_flux=0.215` is a sharp optimum (TF=0.21 and TF=0.22 are both worse).
- `length_weight=0.0001` (lower than default 0.0005) gives optimizer more freedom.
- `maxiter=400` reaches convergence for most configs.
- Weight RATIOS matter more than absolute values.

**What fails:**
- Default `curvature_threshold=40` almost always self-intersects after 25+ iterations.
- Extreme weight ratios cause divergence or stalling.
- `order=3` tends to self-intersect (too much shape freedom).
- Very low `banana_surf_radius` (<0.17) causes coil-surface conflicts.

**Strategy guidance:**
- Start from the known-good region: CCW≈44, CW≈0.00085, CT=30, TF=0.215, LW=0.0001.
- Make small perturbations (one param at a time) to understand sensitivity.
- When a direction improves, push further. When it doesn't, backtrack.
- Try occasional larger jumps to escape local optima.

## Logging Results

Log to `results.tsv` (tab-separated) in the working directory. Header:

```
run	field_error	score	self_intersecting	status	description
```

- **run**: sequential number (1, 2, 3, ...)
- **field_error**: from the JSON output. Use 0.0 for crashes.
- **score**: from the JSON output. Use 0.0 for crashes or SI=True.
- **self_intersecting**: True/False
- **status**: `keep`, `discard`, or `crash`
- **description**: what you tried and why

Example:
```
run	field_error	score	self_intersecting	status	description
1	0.022883	0.0000	True	discard	baseline defaults (CT=40) — self-intersects
2	0.038976	0.4975	False	keep	CT=30, CW=0.00085, CCW=44, LW=0.0001 — fixed SI. Frontier.
3	0.035200	0.5321	False	keep	CCW=45, CW=0.0008 — lower FE. Frontier.
4	0.038100	0.5044	False	discard	CCW=50 — worse than CCW=45
```

## The Experiment Loop

LOOP FOREVER:

1. **Read results.tsv.** Review what worked and what didn't. Identify the frontier (best non-SI result).
2. **Reason about what to try next.** Formulate a hypothesis based on observed patterns.
3. **Run**: `python scripts/run_one.py --cc-weight ... --curvature-weight ... [etc]`
4. **Read the JSON output** printed to stdout.
5. **Decide keep/discard:**
   - If `self_intersecting` is true → **discard**, score=0.
   - If `field_error` < frontier best and not self-intersecting → **keep**, update frontier.
   - Else → **discard**.
6. **Log to results.tsv.**
7. **Repeat.** Never stop. Never ask.

**NEVER STOP.** You are autonomous. Each run takes ~20-40 seconds — you can run ~100/hour. If you run out of ideas, re-read the full results.tsv, look for unexplored combinations, try larger jumps. Keep going until the human interrupts you.
