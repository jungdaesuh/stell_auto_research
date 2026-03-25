# HBT Banana Coil Optimization — ALM Autoresearch Loop

You are an autonomous researcher optimizing HBT stellarator banana coils using the **Augmented Lagrangian Method (ALM)**. ALM enforces engineering constraints as hard limits with automatic multiplier convergence — you do not tune constraint weights.

## Setup

1. **Set working directory**: `cd /Users/suhjungdae/code/opensource/autoresearch`
2. **Read this file** completely.
3. **Read prior art**: `results.jsonl` contains 847 runs from all sources. ALM runs have `"alm": true` in params. Query with `lab.py`.
4. **Start the loop.**

## How ALM Differs

Standard approach: soft penalty weights (`cc_weight`, `curvature_weight`, etc.) that the agent must tune. Wrong weights → constraints violated → crashes.

**ALM separates physics from engineering:**
- **Objective**: NonQSRatio + BoozerResidual + Iota penalty + CurveLength
- **Constraints** (ALM-managed): CC dist ≥ 0.05m, CS dist ≥ 0.02m, SS dist ≥ 0.04m, curvature ≤ 40

Multipliers auto-converge. No weight tuning. Expected: near-zero crash rate, 2-5x longer per run.

## Solvers

Stage 2 (~30s, weighted-sum, unchanged) → Single-stage with ALM (~20-60min). ALM applies to single-stage only.

## Running an Experiment

**Always use `scripts/run_one.py`. Never call the solver directly.**

```bash
python scripts/run_one.py --solver single-stage --equilibrium iota15 \
  --iota-target 0.15 --vol-target 0.10 --mpol 8 \
  --alm --alm-outer-iters 10 --timeout 3600

python scripts/run_one.py --solver stage2 --equilibrium iota15    # seed generation
```

Seeds auto-resolved. `--timeout 3600` auto-set for ALM. ALM and `--basin-hops` are mutually exclusive.

### Key Parameters

Run `python scripts/run_one.py --help` for the full list.

**ALM hyperparameters (your exploration space):**
`--alm-outer-iters` (20), `--alm-mu-init` (1.0, try 0.1-100), `--alm-mu-increase` (5.0, try 2-10), `--alm-mu-max` (1e6), `--alm-tol` (1e-6)

**Physics (stays in objective):**
`--res-weight` (1000), `--iotas-weight` (100), `--iota-target` (0.15), `--vol-target` (0.10), `--mpol` (8), `--ntor` (6)

**What NOT to tune** (ALM handles these): ~~cc_weight~~, ~~curvature_weight~~, ~~cs_weight~~, ~~surf_dist_weight~~

## Scoring

Use `nonqs_ratio` + `boozer_residual` + constraint satisfaction for comparing ALM runs (not `objective_J` — that's the weighted-sum composite, not the ALM objective).

Check `ALM_FINAL_RAW_VIOLATIONS` — all values should be ≤ 0. If positive: increase `--alm-outer-iters` or `--alm-mu-init`. If line search failures: reduce `--alm-mu-max`.

**SELF_INTERSECTING = True → always discard.**

## Querying Results

```
lab.py check --eq <eq>                              Has this been tried?
lab.py suggest --budget 3                            What to try next?
lab.py frontier [--eq <eq>]                          Best results
lab.py seeds --eq <eq> [--best]                      Browse seeds
lab.py sql "SELECT * FROM runs WHERE p_alm = 1"     Filter ALM runs
```

**Before launching ANY run, use `lab.py check`.**

## What to Explore

1. **Equilibrium selection** — which of 19 equilibria work best with ALM?
2. **Seed quality** — different seeds → different basins
3. **ALM hyperparameters** — mu_init, mu_increase, outer_iters
4. **Physics weights** — res_weight, iotas_weight
5. **Resolution** — mpol, ntor
6. **Coil geometry** — order, banana_surf_radius, major_radius, toroidal_flux

## Research Landscape

- Single-stage crashes ~25% with weighted-sum. ALM should reduce this.
- Stage 2 FE does NOT predict single-stage success.
- Most exploration on iota15/iota20 — others underexplored.
- ALM is new and untested. Early runs are valuable.

## The Loop

1. **Query**: `lab.py suggest --budget 3`, `lab.py frontier`
2. **Check**: `lab.py check --eq <eq>`
3. **Run**: `python scripts/run_one.py --solver single-stage --alm --equilibrium ... [params] --timeout 3600`
4. **Evaluate**: Check `ALM_FINAL_RAW_VIOLATIONS` ≤ 0. Compare against weighted-sum baselines.
5. **Repeat.** Never stop. Never ask.

**NEVER STOP.** ALM runs take 20-60 minutes. If stuck, try a different equilibrium or seed. Keep going until the human interrupts you.
