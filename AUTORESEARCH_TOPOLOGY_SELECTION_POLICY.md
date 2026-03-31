# Autoresearch Topology Selection Policy

This document defines how `autoresearch` should use topology in the Columbia
single-stage workflow.

The core rule is:

- Use the proxy objective to generate candidates.
- Use topology to decide what gets promoted.

This is intentionally different from the older policy of trusting lowest `J`
or final-iterate scalar metrics.

## Scope

This policy applies to HBT single-stage candidate generation launched from
`/Users/suhjungdae/code/opensource/autoresearch` against the Columbia solver in:

- `/Users/suhjungdae/code/columbia/simsopt`

Relevant tools:

- `/Users/suhjungdae/code/columbia/simsopt/examples/single_stage_optimization/SINGLE_STAGE/single_stage_banana_example.py`
- `/Users/suhjungdae/code/columbia/simsopt/examples/single_stage_optimization/topology_scorer.py`
- `/Users/suhjungdae/code/columbia/simsopt/examples/single_stage_optimization/POINCARE_PLOTTING/poincare_surfaces.py`
- `/Users/suhjungdae/code/columbia/simsopt/examples/single_stage_optimization/qfm_archive_evaluator.py`
- `/Users/suhjungdae/code/columbia/simsopt/examples/single_stage_optimization/residue_archive_probe.py`

## Why This Policy Exists

The proxy objective is still useful for fast search, but it is not sufficient as
the promotion metric.

Operational conclusion:

- `J` is a search metric.
- topology is the promotion metric.

Topology is not yet the inner-loop optimization objective. It is the selection
and validation layer that decides which runs and checkpoints matter.

## Required Solver Settings

Every autoresearch run in this workflow must set:

- `--solver-root /Users/suhjungdae/code/columbia/simsopt`
- `--checkpoint-every 10`
- `--topology-scorer-every 10`
- `--curvature-threshold 40`
- `--curvature-weight 0.1`

Use tighter or looser checkpoint cadence only if the experiment explicitly
requires it.

The minimum acceptable run artifact set is:

- `results.json`
- `topology_archive.jsonl`
- replayable checkpoint directories with `biot_savart.json` and `surf_outer.json`

If `topology_archive.jsonl` is missing, the run is incomplete for promotion.

## Selection Unit

The selection unit is the best replayable checkpoint from a run, not
automatically the final iterate.

Do not assume:

- final checkpoint is best
- lowest `J` is best
- highest-resolution run is best

Always rank checkpoints first, then rank runs.

## Definitions

- **Family**: An equilibrium file (e.g. iota17). Candidates within the same family
  share the same equilibrium and are directly comparable. Cross-family comparison
  requires both candidates to have strict Poincare results.
- **Materially competitive**: Strict Poincare survival within 2 field lines of the
  family baseline (e.g. 48/50 vs 50/50). A candidate at 40/50 against a baseline
  of 50/50 is not competitive.
- **Strict Poincare**: Field-line tracing with nfieldlines=50, tmax=7000, tol=1e-7.
  Defined in `poincare_surfaces.py`. This is the primary promotion metric.
- **Medium topology**: In-run scoring at nfieldlines=12, tmax=50. Written to
  `topology_archive.jsonl`. Used for shortlisting within a run. Not directly
  comparable to strict Poincare due to different fidelity.
- **Directionally consistent**: Medium and strict scores agree on relative ordering.
  If medium says checkpoint A > B, strict should also show A >= B.

## Ranking Policy

Rank candidate checkpoints in this order:

1. strict Poincare survival (nfieldlines=50, tmax=7000)
2. strict Poincare mean exit time
3. medium `confinement_score` (nfieldlines=12, tmax=50)
4. medium `survival_fraction`
5. `J` as a tiebreaker only

Interpretation:

- strict Poincare is the primary decision metric
- medium topology score is the in-run shortlist metric
- `J` breaks ties between topologically similar states

Do not promote a candidate solely because it has the lowest `J`.

## Workflow

### Stage 1: Broad Search

Run broad search with the existing proxy objective.

Recommended exploration order:

- equilibrium family
- `mpol`
- `ntor`

Do not spend primary budget on weight tuning unless a topology-validated lane is
already strong and needs local refinement.

### Stage 2: In-Run Checkpoint Ranking

For each completed run:

1. read `topology_archive.jsonl`
2. identify the best checkpoint by medium topology score
3. keep its `artifact_dir`
4. record both:
   - best checkpoint by topology
   - final checkpoint by `J`

If those differ, the topology checkpoint wins for downstream validation.

### Stage 3: Frontier Validation

Only for shortlisted checkpoints, run:

- strict Poincare
- QFM archive evaluation
- residue archive probe

Use:

- `/Users/suhjungdae/code/columbia/simsopt/examples/single_stage_optimization/qfm_archive_evaluator.py`
- `/Users/suhjungdae/code/columbia/simsopt/examples/single_stage_optimization/residue_archive_probe.py`

The residue probe is currently blocked: `Spec.computational_boundary` is a
read-only property with no setter, so `residue_archive_probe.py` crashes on
assignment. This requires an upstream fix in the columbia-spec-wrapper. Use
QFM as the available Tier 3 diagnostic; residue is deferred until the API
is fixed.

### Stage 4: Promotion Decision

Each run or checkpoint must end in exactly one decision:

- `promote`
- `hold`
- `reject`

Definitions:

- `promote`: topology beats or extends the current validated frontier
- `hold`: proxy improved, but topology evidence is inconclusive
- `reject`: topology regressed or failed minimum validation

## Promotion Rules

Promote a checkpoint when:

- strict Poincare survival is within 2 field lines of or better than the
  family baseline (e.g. 48/50 vs 50/50 is promotable)
- medium scorer and strict Poincare are directionally consistent
- QFM and residue do not reveal an obvious hidden topology failure mode

Hold a checkpoint when:

- medium `confinement_score` >= 0.7 but strict Poincare has not been run yet
- strict Poincare has been run but is within 3-5 lines of the baseline
  (ambiguous zone — run more field lines or longer tmax before deciding)

Reject a checkpoint when:

- strict Poincare survival is below the family baseline by 3+ field lines
- topology regresses relative to the family baseline at the same fidelity
- the candidate only improves `J` without meaningful topology support

## Budget Policy

Use compute in this order:

1. broad proxy search with archive output
2. checkpoint ranking using medium topology
3. strict Poincare on the shortlist
4. QFM on the shortlist
5. residue on the shortlist

This avoids paying residue cost on weak candidates.

## What Autoresearch Should Not Do

Do not:

- rank runs by final `J` alone
- promote a run without replayable checkpoint artifacts
- treat every topology score as globally comparable across fidelities
- assume final iterate is the right artifact to validate
- attempt residue probe until `Spec.computational_boundary` setter is fixed upstream

## Minimal Output Contract

Each promoted candidate should have a compact summary containing:

- equilibrium
- `mpol`, `ntor`
- best checkpoint path
- final checkpoint path
- best medium topology score
- strict Poincare result
- QFM result
- residue result if run
- final decision: `promote`, `hold`, or `reject`

## Immediate Operational Recommendation

For the current reopened frontier:

1. continue broad `mpol/ntor` exploration
2. shortlist by checkpoint-level topology, not final `J`
3. run QFM and residue on the best checkpoint from each serious lane
4. only then decide whether topology should move from selection into a
   second-phase optimization objective

That is the current minimum-risk, high-information policy.
