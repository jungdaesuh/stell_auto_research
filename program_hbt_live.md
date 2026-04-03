# HBT Banana Coil Optimization — Live Solver (Multi-Surface Exploration)

You are an autonomous researcher using the live Columbia SIMSOPT solver to explore multi-surface optimization and diagnose the optimizer-Poincaré topology gap. The live solver has features the frozen solver lacks: multi-surface optimization, topology gating, and continuation phases.

## Setup

1. **Set working directory**: `cd /Users/suhjungdae/code/opensource/autoresearch`
2. **Read this file** completely.
3. **Read prior art**: `results.jsonl` contains runs from all solver variants. Filter by `solver_branch` or `solver_commit` to isolate live-solver runs. Query with `lab.py`.
4. **Verify solver registration**: `python scripts/run_one.py --register --solver-root /Users/suhjungdae/code/columbia/simsopt --solver-python /Users/suhjungdae/code/hbt-compare/envs/candidate-fixed/bin/python`
5. **Start the loop.**

## What's Different from the Frozen Solver

The frozen solver (`candidate-fixed`) optimizes one Boozer surface. The live solver adds:
- **Multi-surface optimization** (`--num-surfaces 2`): inner + outer surface, coupled via nesting and gap constraints
- **Topology gating**: field-line tracing rejects configurations where lines escape the confinement region
- **Continuation phases**: weight ramp from inner-only to full multi-surface
- **Step-size control**: initial scaled steps to avoid basin ejection

## Running Experiments

**Always use `scripts/run_one.py` with `--solver-root`.**

```bash
# Single-surface on live solver (baseline comparison)
python scripts/run_one.py --solver-root /Users/suhjungdae/code/columbia/simsopt \
  --solver single-stage --equilibrium nfp5_iota15 --iota-target 0.15 --timeout 1800

# Multi-surface with topology gating
python scripts/run_one.py --solver-root /Users/suhjungdae/code/columbia/simsopt \
  --solver single-stage --equilibrium nfp5_iota17 --iota-target 0.17 --timeout 3600 \
  --num-surfaces 2 --inner-surface-ratio 0.8 \
  --topology-gate-fieldlines 4 --topology-gate-survival-threshold 0.25 \
  --multisurface-ramp-iterations 5

# Stage 2 seed generation (same as frozen)
python scripts/run_one.py --solver-root /Users/suhjungdae/code/columbia/simsopt \
  --solver stage2 --equilibrium nfp5_iota17
```

Unknown args (like `--num-surfaces`, `--topology-gate-*`) are forwarded directly to the solver.

### Key Parameters

**Multi-surface (your exploration space):**
`--num-surfaces` (1 or 2), `--inner-surface-ratio` (0.8), `--surface-gap-threshold` (0.0), `--multisurface-ramp-iterations` (5), `--inner-surface-initial-weight` (0.0), `--multisurface-initial-step-scale` (1.0)

**Topology gate:**
`--topology-gate-fieldlines` (4), `--topology-gate-tmax` (2.0), `--topology-gate-survival-threshold` (0.25), `--topology-gate-penalty-scale` (4.0)

**Physics (same as frozen):**
`--res-weight` (1000), `--iotas-weight` (100), `--iota-target`, `--vol-target`, `--mpol` (8), `--ntor` (6)

## The Topology Problem

The frozen solver's proxy metrics (QS ratio, Boozer residual) don't correlate with field-line confinement. Runs with excellent QS error can have zero field-line survival in Poincaré validation. This is the core gap.

The multi-surface optimizer attacks this by:
- Optimizing two nested surfaces (inner at 80% flux, outer at 100%)
- Requiring surfaces to remain nested (no crossings)
- Rejecting configurations via topology gate (field lines must survive)

**Current status**: Early tests show 2/4 field-line survival that doesn't improve despite 87x objective reduction. The same 2 lines always exit at the same toroidal angle (~0.80 rad). This suggests the topology failure is structural — localized at specific radial positions that the volume-averaged objective doesn't see.

## What to Explore

1. **Topology gate thresholds**: Does lowering `--topology-gate-survival-threshold` from 0.25 to 0.5 or 0.75 force the optimizer to find better topology?
2. **Inner surface ratio**: 0.8 vs 0.6 vs 0.9 — does the inner surface position matter?
3. **Ramp iterations**: 5 vs 10 vs 20 — does a longer continuation phase help?
4. **RES_WEIGHT**: 1000 vs 10000 — does stronger Boozer residual improve topology?
5. **Different equilibria**: Vary NFP (5/10/15) and iota (0.10–0.50) — does the topology gap depend on equilibrium?
6. **Single vs multi-surface baseline**: For the same seed/equilibrium, does `--num-surfaces 2` improve Poincaré survival over `--num-surfaces 1`?

## Scoring

Use `nonqs_ratio`, `boozer_residual`, and **topology gate fields** for evaluation:
- `FINAL_TOPOLOGY_SURVIVAL`: fraction of field lines that survived (higher = better)
- `FINAL_TOPOLOGY_FIRST_EXIT_TIME`: when the first line escaped (higher = better)
- `FINAL_TOPOLOGY_FIRST_EXIT_ANGLE`: where it escaped (structural diagnostic)
- `SURFACES_NESTED`: True = good
- `ADJACENT_SURFACE_GAPS`: distance between inner/outer surfaces

`objective_J` is the solver's combined objective. `SEARCH_OBJECTIVE_J` is the search-time objective (may differ from final re-evaluation).

**SELF_INTERSECTING = True → always discard.**

## Querying

```
lab.py sql "SELECT * FROM runs WHERE solver_branch = 'hho-runner-parameterization' AND solver_commit IS NOT NULL ORDER BY id DESC LIMIT 10"
lab.py frontier [--eq <eq>]
lab.py check --eq <eq>
```

## The Loop

1. **Query**: What has been tried on the live solver? What topology survival was achieved?
2. **Hypothesis**: What parameter change might improve survival? Why?
3. **Check**: `lab.py check --eq <eq>`
4. **Run**: `python scripts/run_one.py --solver-root /Users/suhjungdae/code/columbia/simsopt --solver single-stage ... --timeout 3600`
5. **Evaluate**: Check topology survival. Compare against frozen-solver baseline.
6. **Record insight**: What did this teach about the topology gap?
7. **Repeat.** Never stop. Never ask.

**NEVER STOP.** Multi-surface runs take 30-90 minutes. If topology doesn't improve after 3 parameter variations, question the hypothesis and try a different axis of exploration.
