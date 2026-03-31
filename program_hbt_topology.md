# HBT Topology-Validated Optimization — Autoresearch Loop

You are an autonomous researcher optimizing HBT stellarator configurations with **topology validation**. Unlike the standard loop, you don't trust scalar metrics alone. You validate confinement at every checkpoint using field-line tracing, and only promote configurations that pass the topology gate.

**Selection policy**: See `AUTORESEARCH_TOPOLOGY_SELECTION_POLICY.md` for the full ranking and promotion rules. The core rule: `J` is a search metric, topology is the promotion metric.

## Setup

1. **Set working directory**: `cd /Users/suhjungdae/code/opensource/autoresearch`
2. **Read this file** completely.
3. **Pre-flight checks** (run before first optimization):
   - Verify solver is registered: `python scripts/run_one.py --solver-root /Users/suhjungdae/code/columbia/simsopt --solver-python /opt/homebrew/Caskroom/miniforge/base/envs/columbia-jax-0.9.2/bin/python --register`
   - Verify SurfaceClassifier fix is present: `grep -c '_full_torus_surface' /Users/suhjungdae/code/columbia/simsopt/examples/single_stage_optimization/topology_scorer.py` (must return >= 1)
   - Verify equilibria exist: `ls /Users/suhjungdae/code/columbia/DATABASE/EQUILIBRIA/wout_nfp22ginsburg_*.nc | wc -l` (must return 19)
4. **Read prior art**: `results.jsonl` contains 850+ runs. Query with `lab.py`.
5. **Start the loop.**

## Solver

Use the **Columbia solver** exclusively. It has the topology scoring callback.

```bash
python scripts/run_one.py \
  --solver single-stage \
  --solver-root /Users/suhjungdae/code/columbia/simsopt \
  --equilibrium <eq> \
  --iota-target <iota> --vol-target 0.10 \
  --mpol <mpol> --ntor <ntor> \
  --curvature-threshold 40 \
  --curvature-weight 0.1 \
  --checkpoint-every 10 \
  --topology-scorer-every 10 \
  --ftol 1e-15 --gtol 1e-15 \
  --timeout 3600
```

**Always pass all of these:**
- `--solver-root` — Columbia solver path
- `--checkpoint-every 10` — saves replayable artifacts (biot_savart.json, surf_{name}.json)
- `--topology-scorer-every 10` — runs confinement scoring, writes `topology_archive.jsonl`
- `--ftol 1e-15 --gtol 1e-15` — forces many iterations. Without these, optimized seeds converge in 1 iteration because the solver's mpol-based tolerance table (e.g. mpol=8 → ftol=1e-5) is already satisfied.
- `--curvature-threshold 40` and `--curvature-weight 0.1`

**When using optimized seeds** (single-stage outputs as starting points):
```bash
python scripts/run_one.py \
  --solver single-stage \
  --solver-root /Users/suhjungdae/code/columbia/simsopt \
  --stage2-bs-path <path-to-biot_savart_opt.json> \
  --equilibrium <eq> \
  --iota-target <iota> --vol-target 0.10 \
  --mpol <mpol> --ntor <ntor> \
  --curvature-threshold 40 \
  --curvature-weight 0.1 \
  --checkpoint-every 10 \
  --topology-scorer-every 10 \
  --ftol 1e-15 --gtol 1e-15 \
  --timeout 3600
```

`--stage2-bs-path` skips Boozer pre-check (which fails or times out on rough stage-2 coils at high mpol).

The Columbia solver writes:
- `topology_archive.jsonl` — confinement score at each scored checkpoint
- `checkpoint_iter*/` — replayable artifacts (`biot_savart.json`, `surf_{name}.json`)
- `best_topology/` — checkpoint with highest confinement score (same artifact format)
- Standard outputs (`biot_savart_opt.json`, `surf_opt.json`, `surf_opt_{name}.json`, `results.json`)

Note: Checkpoint directories do NOT contain legacy-named `surf_outer.json` or `surf_opt.json`. They contain `surf_{name}.json` where `{name}` comes from the solver's surface_data entries. For Poincare replay, copy and rename (see Tier 3).

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

## What We Know

From the RES_WEIGHT pilot, Poincare batch (2026-03-29), and topology session (2026-03-31):

- **Scalar metrics do not predict confinement.** The best nonqs_ratio (0.000321) failed Poincare at 73% survival. A worse nonqs_ratio (0.000425) passed at 80%.
- **RES_WEIGHT doesn't control confinement.** 1000 vs 5000 produced identical 66% survival despite different scalar metrics.
- **Iota family matters.** iota17 and iota20 both pass strict Poincare (29/30, 97% at tmax=5000). iota15 is weaker.
- **mpol matters.** Higher mpol = more Fourier modes = better boundary shaping. mpol=16 needed for Poincare confinement, though mpol=12 is often sufficient for the optimizer.
- **ntor matters and is underexplored.** ntor=8 with mpol=9 achieved J=0.000287 with 12/12 confinement — the best result from the 2026-03-31 session. ntor exploration (never tried in 850+ runs) proved more effective than weight tuning.
- **SurfaceClassifier fix is prerequisite.** The Columbia solver's topology_scorer.py must use `_full_torus_surface()` to create a full-torus surface before passing to SurfaceClassifier. Without this fix, all topology scores falsely read 0/12 survival. Fix is at columbia/simsopt commit 28ea688822c0.

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

19 equilibrium files available. Match `--iota-target` to the equilibrium axis iota. `run_one.py` warns if `--iota-target` deviates by more than 0.02 from the expected axis iota.

| Shorthand | Axis iota | `--iota-target` | Source | Notes |
|-----------|-----------|-----------------|--------|-------|
| `iota15` | 0.1466 | 0.15 | VMEC | Most explored |
| `iota15p` | 0.15 | 0.15 | DESC | Precise variant |
| `iota16` | 0.16 | 0.16 | DESC | Underexplored |
| `iota17` | 0.1697 | 0.17 | DESC | Passes strict Poincare (29/30 at tmax=5000) |
| `iota18` | 0.18 | 0.18 | DESC | Underexplored |
| `iota19` | 0.19 | 0.19 | DESC | Underexplored |
| `iota20` | 0.1980 | 0.20 | VMEC | Passes strict Poincare (29/30 at tmax=5000) |
| `iota20p` | 0.20 | 0.20 | DESC | Precise variant |
| `iota21` | 0.21 | 0.21 | DESC | Underexplored |
| `iota22` | 0.22 | 0.22 | DESC | Underexplored |
| `iota23` | 0.23 | 0.23 | DESC | Underexplored |
| `iota24` | 0.24 | 0.24 | DESC | Underexplored |
| `iota25` | 0.25 | 0.25 | DESC | Underexplored |
| `iota26` | 0.26 | 0.26 | DESC | Underexplored |
| `iota27` | 0.27 | 0.27 | DESC | Underexplored |
| `iota28` | 0.28 | 0.28 | DESC | Underexplored |
| `iota29` | 0.29 | 0.29 | DESC | Underexplored |
| `iota30` | 0.30 | 0.30 | DESC | Underexplored |
| `001490` | 0.2973 | 0.30 | VMEC | Single-stage untested |

**Do not assume which equilibrium is best.** iota17 and iota20 are the only confirmed Poincare passers, but 15 equilibria are underexplored.

## Key Parameters

**Always set:**
- `--solver-root /Users/suhjungdae/code/columbia/simsopt`
- `--checkpoint-every 10`
- `--topology-scorer-every 10`
- `--curvature-threshold 40`
- `--curvature-weight 0.1` (Columbia solver default; run_one.py default is 0.0001 which is 1000x too low)
- `--ftol 1e-15 --gtol 1e-15` (when using optimized seeds)
- `--timeout 3600` (increase to 7200 for mpol >= 12 with topology scoring; each topology call adds ~30s)

**Tune:**
- `--mpol` — start at 8 for new equilibria. Ramp progressively (see Progressive Ramp Rules).
- `--ntor` — start at 6. Ramp to 8, 10. ntor=8 showed strong results.
- `--res-weight` — default 1000. Does not affect confinement. Keep at 1000.
- `--iotas-weight` — default 100. Try 200.
- Other objective weights — adjust when topology-validated lane is already strong and needs local refinement.

## Evaluation — Three Tiers

### Tier 1: Solver Output (immediate)
From `results.jsonl`:
- `status=pass` and `self_intersecting=false` → proceed to Tier 2
- `status=crash` → log and move on

### Tier 2: Topology Archive (automatic)
The Columbia solver writes `topology_archive.jsonl` when `--topology-scorer-every` is set. Read it:
```bash
python3 -c "
import json
with open('<run_dir>/topology_archive.jsonl') as f:
    for line in f:
        d = json.loads(line)
        print(f'iter={d[\"accepted_iteration\"]} confinement={d[\"confinement_score\"]:.3f} survival={d[\"survival_fraction\"]:.2f}')
"
```

**Selection unit**: The best checkpoint by medium `confinement_score` (from `topology_archive.jsonl`), not the final iterate. This shortlists candidates for strict Poincare. If `topology_archive.jsonl` is missing, the run is incomplete for promotion. `run_one.py` warns when `--topology-scorer-every` was set but no archive was produced.

Look for `confinement_score` trending upward and `survival_fraction` above 0.7.

### Tier 3: Post-hoc Evaluation (for shortlisted checkpoints only)

Run on the **best topology checkpoint** from Tier 2, not the final iterate.

**Strict Poincare** (nfieldlines=50, tmax=7000): The primary promotion metric.

The `poincare_surfaces.py` script has no CLI args. Set `POINCARE_OUT_DIR` to the checkpoint directory containing `biot_savart_opt.json` and `surf_opt.json`:
```bash
POINCARE_OUT_DIR=<best_topology_checkpoint_dir> \
PYTHONPATH=/Users/suhjungdae/code/columbia/simsopt/examples/single_stage_optimization:/Users/suhjungdae/code/columbia/simsopt/src \
/opt/homebrew/Caskroom/miniforge/base/bin/python3 \
/Users/suhjungdae/code/columbia/simsopt/examples/single_stage_optimization/POINCARE_PLOTTING/poincare_surfaces.py
```

The checkpoint dir must contain the legacy-named files. Copy from checkpoint artifacts:
```bash
cp <checkpoint_dir>/biot_savart.json <checkpoint_dir>/biot_savart_opt.json
cp <checkpoint_dir>/surf_<name>.json <checkpoint_dir>/surf_opt.json
```

Output: `PoincareMetrics_opt.json` with `validation_status`, `survived_lines`, `survival_fraction`.

Note: Prior session results (29/30 at tmax=5000) used custom parameters. The canonical strict Poincare is 50 field lines at tmax=7000.

**QFM diagnostic:**
```bash
PYTHONPATH=/Users/suhjungdae/code/columbia/simsopt/examples/single_stage_optimization:/Users/suhjungdae/code/columbia/simsopt/src \
/opt/homebrew/Caskroom/miniforge/base/bin/python3 \
/Users/suhjungdae/code/columbia/simsopt/examples/single_stage_optimization/qfm_archive_evaluator.py \
  --artifact-dir <best_topology_checkpoint_dir> \
  --label volume --method LBFGS
```

**SPEC residue probe** (requires columbia-spec-wrapper env — API currently broken, `property 'computational_boundary' has no setter`):
```bash
PYTHONPATH=/Users/suhjungdae/code/columbia/simsopt/examples/single_stage_optimization:/Users/suhjungdae/code/columbia/simsopt/src \
/opt/homebrew/Caskroom/miniforge/base/envs/columbia-spec-wrapper/bin/python \
/Users/suhjungdae/code/columbia/simsopt/examples/single_stage_optimization/residue_archive_probe.py \
  --artifact-dir <best_topology_checkpoint_dir> \
  --resonance 1,7,0.9 \
  --keep-workdir
```

Only run Tier 3 on the best topology results from Tier 2.

## Selection & Promotion

See `AUTORESEARCH_TOPOLOGY_SELECTION_POLICY.md` for the full policy. Summary:

**Definitions:**
- **Family**: An equilibrium file (e.g. iota17). Candidates within the same family share the same equilibrium and are comparable. Candidates across families are compared only when both have strict Poincare results.
- **Materially competitive**: Strict Poincare survival within 2 field lines of the family baseline (e.g. 48/50 vs 50/50). A candidate at 40/50 against a baseline of 50/50 is not competitive.
- **Medium scorer**: In-run topology scoring at nfieldlines=12, tmax=50. Used for shortlisting within a run. Not directly comparable to strict Poincare (nfieldlines=50, tmax=7000).
- **Directionally consistent**: Medium and strict scores agree on relative ordering. If medium says checkpoint A > B, strict should also show A >= B.

**Ranking order** (for candidate checkpoints):
1. Strict Poincare survival (nfieldlines=50, tmax=7000)
2. Strict Poincare mean exit time
3. Medium `confinement_score` (nfieldlines=12, tmax=50)
4. Medium `survival_fraction`
5. `J` as tiebreaker only

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
3. Form hypothesis. Recommended exploration order: equilibrium family → mpol → ntor.
4. Run experiments. Use progressive ramp for high-resolution runs.
5. Do not spend primary budget on weight tuning unless a topology-validated lane is already strong.

Phase 3 — Frontier evaluation:
6. Shortlist by checkpoint-level topology (best checkpoint from topology_archive.jsonl).
7. Run strict Poincare on shortlisted checkpoints.
8. Run QFM on shortlisted checkpoints.
9. Run residue probe if API is available.
10. Make promote/hold/reject decisions per the selection policy.

## The Loop

1. **Query**: `lab.py frontier`, `lab.py coverage`, `lab.py sql "SELECT ..."` — understand the landscape
2. **Think**: What's the confinement score trend? Is survival improving with iteration? Which checkpoint is best?
3. **Check**: `lab.py check --eq <eq>`
4. **Run**: Use the full command template from the Solver section. Include `--ftol 1e-15 --gtol 1e-15` when seeding.
5. **Evaluate**: Read `topology_archive.jsonl`. Identify best checkpoint by topology. Record both best-topology and final-J checkpoints.
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
- **Equilibrium matters.** Different iota targets produce structurally different confinement. Explore broadly.
- **Progressive ramp, not big jumps.** Single-step increments in mpol and ntor prevent Boozer initialization crashes.

**NEVER STOP.** Follow the phase plan. If stuck, move to the next phase. Keep going until the human interrupts you.
