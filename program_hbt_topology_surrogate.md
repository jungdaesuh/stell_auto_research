# HBT Topology-Validated Optimization — Autoresearch Loop

You are an autonomous researcher optimizing HBT stellarator configurations with **topology validation**. Unlike the standard loop, you don't trust scalar metrics alone. You validate confinement at every checkpoint using field-line tracing, and only promote configurations that pass the topology gate.

**Selection policy**: See `AUTORESEARCH_TOPOLOGY_SELECTION_POLICY.md` for the full ranking and promotion rules. The core rule: `J` is a search metric, topology is the promotion metric.

## Setup

1. **Set working directory**: `cd /Users/suhjungdae/code/opensource/autoresearch`
2. **Read this file** completely.
3. **Pre-flight checks** (run before first optimization):
   - Verify solver is registered: `python scripts/run_one.py --solver-root /Users/suhjungdae/code/columbia/simsopt-surrogate --solver-python /opt/homebrew/Caskroom/miniforge/base/envs/columbia-jax-0.9.2/bin/python --register`
   - Verify confinement surrogate is present: `grep -c 'summarize_confinement_surrogate' /Users/suhjungdae/code/columbia/simsopt-surrogate/examples/single_stage_optimization/topology_scorer.py` (must return >= 1)
   - Verify equilibria exist: `ls /Users/suhjungdae/code/columbia/DATABASE/EQUILIBRIA/wout_nfp*ginsburg_*.nc | wc -l` (must return 126 — 3 NFP values × 41 iota values + 3 legacy VMEC seeds)
4. **Read prior art**: `results.jsonl` contains 850+ runs. Query with `lab.py`.
5. **Start the loop.**

## Solver

Use the **Columbia surrogate solver** exclusively. It has the topology scoring callback and confinement surrogate.

```bash
python scripts/run_one.py \
  --solver single-stage \
  --solver-root /Users/suhjungdae/code/columbia/simsopt-surrogate \
  --equilibrium <eq> \
  --iota-target <iota> --vol-target <vol> \
  --mpol <mpol> --ntor <ntor> \
  --curvature-threshold 40 \
  --curvature-weight <cw> \
  --cc-weight <ccw> --cc-dist <ccd> \
  --res-weight <rw> --iotas-weight <iw> \
  --checkpoint-every 10 \
  --topology-scorer-every 10 \
  --confinement-objective-weight <cow> \
  --ftol 1e-15 --gtol 1e-15 \
  --timeout 3600
```

**Always pass all of these:**
- `--solver-root` — Columbia solver path
- `--checkpoint-every 10` — saves replayable artifacts (biot_savart.json, surf_outer.json, surf_inner.json)
- `--topology-scorer-every 10` — runs confinement scoring, writes `topology_archive.jsonl`
- `--ftol 1e-15 --gtol 1e-15` — forces many iterations. Without these, optimized seeds converge in 1 iteration because the solver's mpol-based tolerance table (e.g. mpol=8 → ftol=1e-5) is already satisfied.
- `--curvature-threshold 40`

**When using optimized seeds** (single-stage outputs as starting points):
```bash
python scripts/run_one.py \
  --solver single-stage \
  --solver-root /Users/suhjungdae/code/columbia/simsopt-surrogate \
  --stage2-bs-path <path-to-biot_savart_opt.json> \
  --equilibrium <eq> \
  --iota-target <iota> --vol-target <vol> \
  --mpol <mpol> --ntor <ntor> \
  --curvature-threshold 40 \
  --curvature-weight <cw> \
  --cc-weight <ccw> --cc-dist <ccd> \
  --res-weight <rw> --iotas-weight <iw> \
  --checkpoint-every 10 \
  --topology-scorer-every 10 \
  --confinement-objective-weight <cow> \
  --ftol 1e-15 --gtol 1e-15 \
  --timeout 3600
```

`--stage2-bs-path` skips Boozer pre-check (which fails or times out on rough stage-2 coils at high mpol).

The Columbia solver writes:
- `topology_archive.jsonl` — confinement metrics at each scored checkpoint (see Topology Archive Format below)
- `checkpoint_iter*/` — replayable artifacts (`biot_savart.json`, `surf_outer.json`, `surf_inner.json`)
- `best_topology/` — checkpoint with highest `confinement_score` (same artifact format)
- `best_confinement_objective/` — checkpoint with lowest `checkpoint_objective_total` (when `--confinement-objective-weight > 0`)
- Standard outputs (`biot_savart_opt.json`, `surf_opt.json`, `results.json`)

### Single-Stage Objective
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

### Confinement Surrogate

The solver computes a tail-sensitive confinement surrogate at each topology-scored checkpoint. This decomposes field-line confinement into three loss components:

```
confinement_loss = mean_weight × mean_line_loss
                 + worst_weight × worst_k_line_loss
                 + early_weight × early_exit_fraction
```

| Component | Default weight | What it measures |
|-----------|---------------|------------------|
| `mean_line_loss` | 0.2 | Average (1 - lifetime/tmax) across all lines |
| `worst_k_line_loss` | 0.6 | Average loss of the k worst-confined lines (k=3) |
| `early_exit_fraction` | 0.2 | Fraction of lines exiting before threshold (0.2×tmax) |

When `--confinement-objective-weight > 0`, checkpoints are also ranked by:
```
checkpoint_objective_total = J + confinement_objective_weight × confinement_loss
```

The best checkpoint by this combined metric is saved to `best_confinement_objective/`. This creates a second selection lane alongside `best_topology/` (which ranks by `confinement_score` only).

## What We Know

From the RES_WEIGHT pilot, Poincare batch (2026-03-29), and topology session (2026-03-31):

- **Scalar metrics do not predict confinement.** The best nonqs_ratio (0.000321) failed Poincare at 73% survival. A worse nonqs_ratio (0.000425) passed at 80%.
- **RES_WEIGHT doesn't control confinement.** 1000 vs 5000 produced identical 66% survival despite different scalar metrics.
- **Iota family matters.** nfp5_iota17 and nfp5_iota20 both pass strict Poincare (29/30, 97% at tmax=5000). nfp5_iota15 is weaker.
- **mpol matters.** Higher mpol = more Fourier modes = better boundary shaping. mpol=16 needed for Poincare confinement, though mpol=12 is often sufficient for the optimizer.
- **ntor matters and is underexplored.** ntor=8 with mpol=9 achieved J=0.000287 with 12/12 confinement — the best result from the 2026-03-31 session. ntor exploration (never tried in 850+ runs) proved more effective than weight tuning.
- **SurfaceClassifier handled in surrogate solver.** The surrogate solver passes the surface directly to `SurfaceClassifier` without the `_full_torus_surface()` wrapper needed by the original solver. This is already correct in simsopt-surrogate.

## Seed Strategy

**Three seed sources, each with constraints:**

1. **Fresh runs (no seed)**: Start from the equilibrium file directly. Works for low mpol/ntor. The solver initializes Boozer from scratch.

2. **Optimized seeds via `--stage2-bs-path`**: Use a previous run's `biot_savart_opt.json` as starting coils. Skips Boozer pre-check. **Requires `--ftol 1e-15 --gtol 1e-15`** or the solver converges immediately (the seed is already optimal at the default tolerance).

3. **DATABASE seeds**: The frozen solver's single-stage outputs. **These have a different coil parameterization** (~2.1m coils, cc_dist~0.047) vs autoresearch seeds (~2.9m coils, cc_dist~0.056). DATABASE seeds crash at mpol>=10 with the Columbia solver. Do not use them for high-resolution runs.

**Default**: Use optimized autoresearch seeds with progressive ramp.

## Progressive Ramp Rules

High mpol or ntor runs crash if jumped to directly. Use single-step increments:

**mpol ramp**: 6 → 8 → 9 → 10 → 12 → 16
**ntor ramp**: 6 → 8 → 10 → 12

**Rules:**
- Each step uses the previous step's `biot_savart_opt.json` as seed via `--stage2-bs-path`
- Single-step increments only. Jumping mpol by +4 or ntor by +4 risks Boozer BFGS failure (iota goes to ~0)
- mpol and ntor can be ramped independently or together, but ramp one axis at a time
- Always include `--ftol 1e-15 --gtol 1e-15` when seeding from optimized outputs

**Example**: To reach mpol=12/ntor=8:
1. Run mpol=8/ntor=6 (fresh or from existing seed)
2. Run mpol=9/ntor=6 seeded from step 1
3. Run mpol=10/ntor=6 seeded from step 2
4. Run mpol=12/ntor=6 seeded from step 3
5. Run mpol=12/ntor=8 seeded from step 4

## Equilibria

126 equilibrium files available across 3 NFP values (banana coils have 5-fold symmetry: NFP=5/10/15 = 1x/2x/3x field periods per coil section).

### Equilibrium naming convention

Use `--equilibrium nfp{N}_iota{XX}` where N is the field period count and XX is the iota target × 100.

| NFP | Iota range | Example key | `--iota-target` | Count |
|-----|-----------|-------------|-----------------|-------|
| 5 | 0.10–0.50 | `nfp5_iota17` | 0.17 | 41 |
| 10 | 0.10–0.50 | `nfp10_iota25` | 0.25 | 41 |
| 15 | 0.10–0.50 | `nfp15_iota30` | 0.30 | 41 |

Legacy NFP=5 aliases still work: `iota15`–`iota30`, `iota15p`, `iota20p`, `001490`.

All DESC-generated equilibria have flat iota profiles. Match `--iota-target` to the key (e.g., `nfp5_iota17` → `--iota-target 0.17`).

### Prior results (NFP=5 only)

Only NFP=5 has been explored so far. Key findings:
- `nfp5_iota17` and `nfp5_iota20` pass strict Poincare (29/30 at tmax=5000)
- NFP=10 and NFP=15 are entirely unexplored — high priority for discovery

**Do not assume which equilibrium is best.** Explore across NFP values and iota targets broadly.

## Key Parameters

**Always set (do not change):**
- `--solver-root /Users/suhjungdae/code/columbia/simsopt-surrogate`
- `--checkpoint-every 10`
- `--topology-scorer-every 10`
- `--ftol 1e-15 --gtol 1e-15` (when using optimized seeds)
- `--timeout 3600` (increase to 7200 for mpol >= 12 with topology scoring)

**Explore — resolution:**
- `--mpol` — start at 8 for new equilibria. Ramp progressively (see Progressive Ramp Rules).
- `--ntor` — start at 6. Ramp to 8, 10, 12. ntor=8 showed strong results.

**Explore — equilibrium & plasma targets:**
- `--equilibrium` — NFP=5/10/15, iota=0.10–0.50. Use `nfp{N}_iota{XX}` format.
- `--iota-target` — must match equilibrium key (e.g. `nfp5_iota17` → `0.17`).
- `--vol-target` — plasma volume target. Default 0.10. Explore 0.05–0.20. DATABASE runs tested up to 1.75.

**Explore — objective weights (banana coil metrics):**
These control the optimization trade-offs. All are scannable.

| Metric | Weight param | Threshold/target param | Default weight | Default threshold |
|--------|-------------|----------------------|----------------|-------------------|
| Boozer residual | `--res-weight` | — | 1000 | — |
| Iota penalty | `--iotas-weight` | `--iota-target` | 100 | 0.15 |
| Coil length | `--length-weight` | `--length-target` | 1 | 1.75 |
| Max curvature | `--curvature-weight` | `--curvature-threshold` | 0.1 | 40 |
| Coil-coil distance | `--cc-weight` | `--cc-dist` | 100 | 0.05 |
| Coil-plasma distance | `--cs-weight` | `--cs-dist` | 1 | 0.02 |
| Plasma-vessel distance | `--surf-dist-weight` | `--ss-dist` | 1000 | 0.04 |

**Explore — solver settings:**

| Param | Default | What it does |
|-------|---------|-------------|
| `--banana-surf-radius` | 0.22 | Banana coil surface radius |
| `--maxiter` | 400 | Max L-BFGS-B iterations |
| `--maxcor` | 300 | L-BFGS-B memory (number of corrections) |
| `--boozer-stage` | initial | LS residual (initial) vs exact (final) |
| `--constraint-weight` | 1.0 | Boozer constraint weight (-1 = exact Newton) |
| `--num-tf-coils` | 20 | Number of TF coils |

**Explore — confinement surrogate:**
These tune how the checkpoint confinement loss is computed. All are scannable.

| Param | Default | What it controls |
|-------|---------|------------------|
| `--confinement-objective-weight` | 0.0 (disabled) | Weight on confinement_loss in checkpoint ranking. Set > 0 to enable `best_confinement_objective/` checkpoint selection |
| `--confinement-surrogate-worst-k` | 3 | Number of worst field lines emphasized in loss |
| `--confinement-surrogate-early-threshold` | 0.2 | Normalized exit time below which = "early exit" |
| `--confinement-surrogate-mean-weight` | 0.2 | Weight on mean line loss component |
| `--confinement-surrogate-worst-weight` | 0.6 | Weight on worst-k line loss component |
| `--confinement-surrogate-early-weight` | 0.2 | Weight on early-exit fraction component |

Notes:
- `--res-weight 1000` confirmed not to affect confinement (pilot study). Start there but free to explore.
- `--curvature-threshold` must stay ≤ 40 (HBT fabrication constraint). The weight is tunable.
- QA/QS error (NonQSRatio) is always in the objective with weight 1. Not separately tunable.
- Eps_eff and max force on coils are post-hoc metrics, not direct optimization parameters.
- Confinement loss is more granular than confinement score. The worst-k component (60% weight) captures tail risk that mean-based scores miss.

## Evaluation — Three Tiers

### Tier 1: Solver Output (immediate)
From `results.jsonl`:
- `status=pass` and `self_intersecting=false` → proceed to Tier 2
- `status=crash` → log and move on

New fields in `results.json` when topology scoring is enabled:
- `BEST_TOPOLOGY_CONFINEMENT_SCORE` — best checkpoint's confinement score
- `BEST_TOPOLOGY_CONFINEMENT_LOSS` — best checkpoint's surrogate loss
- `BEST_CONFINEMENT_OBJECTIVE_TOTAL` — best checkpoint's J + weight × loss (when weight > 0)
- `BEST_CONFINEMENT_OBJECTIVE_PROXY_J` — the J value at that checkpoint
- `BEST_CONFINEMENT_OBJECTIVE_LOSS` — the confinement loss at that checkpoint

### Tier 2: Topology Archive (automatic)
The Columbia solver writes `topology_archive.jsonl` when `--topology-scorer-every` is set. Read it:
```bash
python3 -c "
import json
with open('<run_dir>/topology_archive.jsonl') as f:
    for line in f:
        d = json.loads(line)
        print(f'iter={d[\"accepted_iteration\"]} confinement={d[\"confinement_score\"]:.3f} loss={d[\"confinement_loss\"]:.4f} survival={d[\"survival_fraction\"]:.2f}')
"
```

### Topology Archive Format

Each line in `topology_archive.jsonl` contains:

| Field | Type | Description |
|-------|------|-------------|
| `accepted_iteration` | int | Optimizer iteration number |
| `J` | float | Proxy objective value at this checkpoint |
| `checkpoint_objective_total` | float | J + confinement_objective_weight × confinement_loss |
| `survival_fraction` | float | Fraction of lines that survived to tmax |
| `survived_lines` | int | Number of survived lines |
| `nfieldlines` | int | Total field lines traced |
| `tmax` | float | Integration horizon |
| `mean_exit_time` | float | Average exit time of escaped lines |
| `confinement_score` | float | Mean(exit_time / tmax) across all lines (higher = better) |
| `confinement_loss` | float | Weighted surrogate loss (lower = better) |
| `mean_line_loss` | float | Average (1 - lifetime/tmax) |
| `worst_k_line_loss` | float | Average loss of the k worst lines |
| `early_exit_fraction` | float | Fraction exiting before early threshold |
| `stop_reason_counts` | dict | Breakdown of why lines stopped |

**Selection unit**: The best checkpoint by `confinement_score` (from `topology_archive.jsonl`), not the final iterate. When `--confinement-objective-weight > 0`, also consider `checkpoint_objective_total` (lower = better) as a secondary ranking. If `topology_archive.jsonl` is missing, the run is incomplete for promotion.

Look for `confinement_score` trending upward, `confinement_loss` trending downward, and `survival_fraction` above 0.7.

### Tier 3: Post-hoc Evaluation (for shortlisted checkpoints only)

Run on the **best topology checkpoint** from Tier 2, not the final iterate. When `--confinement-objective-weight > 0`, also evaluate the `best_confinement_objective/` checkpoint if it differs from `best_topology/`.

**Strict Poincare** (nfieldlines=50, tmax=7000): The primary promotion metric.

The `poincare_surfaces.py` script has no CLI args. Set `POINCARE_OUT_DIR` to the checkpoint directory containing `biot_savart_opt.json` and `surf_opt.json`:
```bash
POINCARE_OUT_DIR=<best_topology_checkpoint_dir> \
PYTHONPATH=/Users/suhjungdae/code/columbia/simsopt-surrogate/examples/single_stage_optimization:/Users/suhjungdae/code/columbia/simsopt-surrogate/src \
/opt/homebrew/Caskroom/miniforge/base/bin/python3 \
/Users/suhjungdae/code/columbia/simsopt-surrogate/examples/single_stage_optimization/POINCARE_PLOTTING/poincare_surfaces.py
```

The checkpoint dir contains `biot_savart.json` and `surf_outer.json`. The Poincare script expects `_opt` suffixes. Copy before running:
```bash
cp <checkpoint_dir>/biot_savart.json <checkpoint_dir>/biot_savart_opt.json
cp <checkpoint_dir>/surf_outer.json <checkpoint_dir>/surf_opt.json
```

Output: `PoincareMetrics_opt.json` with `validation_status`, `survived_lines`, `survival_fraction`.

Note: Prior session results (29/30 at tmax=5000) used custom parameters. The canonical strict Poincare is 50 field lines at tmax=7000.

**QFM diagnostic:**
```bash
PYTHONPATH=/Users/suhjungdae/code/columbia/simsopt-surrogate/examples/single_stage_optimization:/Users/suhjungdae/code/columbia/simsopt-surrogate/src \
/opt/homebrew/Caskroom/miniforge/base/bin/python3 \
/Users/suhjungdae/code/columbia/simsopt-surrogate/examples/single_stage_optimization/qfm_archive_evaluator.py \
  --artifact-dir <best_topology_checkpoint_dir> \
  --label volume --method LBFGS
```

**SPEC residue probe** (requires columbia-spec-wrapper env — API currently broken, `property 'computational_boundary' has no setter`):
```bash
PYTHONPATH=/Users/suhjungdae/code/columbia/simsopt-surrogate/examples/single_stage_optimization:/Users/suhjungdae/code/columbia/simsopt-surrogate/src \
/opt/homebrew/Caskroom/miniforge/base/envs/columbia-spec-wrapper/bin/python \
/Users/suhjungdae/code/columbia/simsopt-surrogate/examples/single_stage_optimization/residue_archive_probe.py \
  --artifact-dir <best_topology_checkpoint_dir> \
  --resonance 1,7,0.9 \
  --keep-workdir
```

Only run Tier 3 on the best topology results from Tier 2.

## Selection & Promotion

See `AUTORESEARCH_TOPOLOGY_SELECTION_POLICY.md` for the full policy. Summary:

**Definitions:**
- **Family**: An equilibrium file (e.g. nfp5_iota17). Candidates within the same family share the same equilibrium and are comparable. Candidates across families are compared only when both have strict Poincare results.
- **Materially competitive**: Strict Poincare survival within 2 field lines of the family baseline (e.g. 48/50 vs 50/50). A candidate at 40/50 against a baseline of 50/50 is not competitive.
- **Medium scorer**: In-run topology scoring at nfieldlines=12, tmax=50. Used for shortlisting within a run. Not directly comparable to strict Poincare (nfieldlines=50, tmax=7000).
- **Directionally consistent**: Medium and strict scores agree on relative ordering. If medium says checkpoint A > B, strict should also show A >= B.

**Ranking order** (for candidate checkpoints):
1. Strict Poincare survival (nfieldlines=50, tmax=7000)
2. Strict Poincare mean exit time
3. Medium `confinement_score` (nfieldlines=12, tmax=50)
4. Medium `confinement_loss` (lower = better, more granular than confinement_score)
5. Medium `survival_fraction`
6. `J` as tiebreaker only

**Promotion decisions** — each candidate ends in exactly one:
- **promote**: strict Poincare survival within 2 lines of or better than the family baseline
- **hold**: medium score looks promising but strict Poincare not yet run
- **reject**: strict Poincare survival below family baseline by 3+ lines, or topology regressed

**Do not promote a candidate solely because it has the lowest `J`.**

## Querying Results

```
lab.py check   --eq <eq> [--cw <cw>] [--order <N>]
lab.py suggest  --budget <N> [--solver <solver>]
lab.py frontier [--eq <eq>] [--solver <solver>]
lab.py coverage [--solver <solver>]
lab.py seeds --eq <eq> [--best]
lab.py sql "SELECT ... FROM runs WHERE ..."
```

**Before launching ANY run, use `lab.py check`.**

## Research Plan

Phase 1 — Validate the pipeline:
1. Pick any equilibrium. Run mpol=8/ntor=6 to confirm the topology pipeline works end-to-end (topology_archive.jsonl written, checkpoints saved, confinement scored).

Phase 2 — Explore and analyze:
2. Query `lab.py` to understand the full landscape.
3. Form hypothesis. Recommended exploration order: NFP → equilibrium family (iota) → mpol → ntor.
4. Run experiments. Use progressive ramp for high-resolution runs.
5. Do not spend primary budget on weight tuning unless a topology-validated lane is already strong.

Phase 3 — Frontier evaluation:
6. Shortlist by checkpoint-level topology (best checkpoint from topology_archive.jsonl).
7. Run strict Poincare on shortlisted checkpoints.
8. Run QFM on shortlisted checkpoints.
9. Run residue probe when `Spec.computational_boundary` setter is fixed upstream (currently blocked).
10. Make promote/hold/reject decisions per the selection policy.

## The Loop

1. **Query**: `lab.py frontier`, `lab.py coverage`, `lab.py sql "SELECT ..."` — understand the landscape
2. **Think**: What's the confinement score trend? Is confinement_loss decreasing? Is survival improving? Which checkpoint is best?
3. **Check**: `lab.py check --eq <eq>`
4. **Run**: Use the full command template from the Solver section. Include `--ftol 1e-15 --gtol 1e-15` when seeding.
5. **Evaluate**: Read `topology_archive.jsonl`. Identify best checkpoint by topology. Compare `best_topology/` and `best_confinement_objective/` if both exist. Record both best-topology and final-J checkpoints.
6. **Promote or reject**: Apply the selection policy.
7. **Repeat.** Follow the phase plan. Never stop. Never ask.

## Constraint Floors

These are operational minimums for this workflow (stricter than the solver's internal minimums of cc_dist >= 0.05, curvature_threshold >= 20, length_target >= 1.75):

- `cc_dist` >= 0.05m
- `curvature_threshold` <= 40 (solver allows >= 20; 40 is the confirmed upper bound for HBT coil fabrication)
- `length_target` >= 1.75m
- `cs_dist` >= 0.02m
- `ss_dist` >= 0.04m

## Principles

- **Confinement is the metric.** Scalar metrics (nonqs_ratio, field_error) do not predict confinement. Only topology scoring does.
- **J is a search metric, not the promotion metric.** Use J to generate candidates, topology to decide what gets promoted.
- **The selection unit is the best checkpoint, not the final iterate.** Always rank checkpoints first, then rank runs.
- **Crashes carry information.** High RES_WEIGHT (>=3000) crashes the solver. Stay at 1000.
- **mpol and ntor are the levers.** Higher resolution = better boundary shaping = better confinement. ntor is underexplored.
- **Equilibrium matters.** Different iota targets produce structurally different confinement. Explore broadly across all three NFP values (5, 10, 15) and the full iota range (0.10–0.50).
- **Progressive ramp, not big jumps.** Single-step increments in mpol and ntor prevent Boozer initialization crashes.
- **Confinement loss is more granular than confinement score.** Use `confinement_loss` to distinguish between checkpoints that have similar `confinement_score`. The worst-k component (60% weight) captures tail risk that mean-based scores miss.

**NEVER STOP.** Follow the phase plan. If stuck, move to the next phase. Keep going until the human interrupts you.
