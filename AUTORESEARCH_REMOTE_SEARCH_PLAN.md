# Autoresearch-Style Remote Search Plan — Single-Stage HBT Optimization on GCloud

## Purpose

Apply the autoresearch autonomous experiment loop to the Columbia single-stage HBT banana-coil optimization problem, executed remotely on GCloud.

The autoresearch pattern is:

```
LOOP FOREVER:
  propose config → run → evaluate → keep or discard → repeat
```

This plan adapts that pattern:

- **Instead of** modifying `train.py` hyperparameters → propose `SingleStageRunnerConfig` physics parameter configs
- **Instead of** `val_bpb` → `combined_score` from field error, iota/volume closeness, curvature, self-intersection
- **Instead of** a fixed 5-minute GPU training budget → variable-duration CPU-bound L-BFGS-B optimization (probe: seconds, full: minutes to hours)
- **Instead of** local single-GPU execution → remote GCloud VMs (CPU-optimized, simulation is CPU-bound)
- **Instead of** git commit per experiment → manifest per candidate in SQLite + run archive

Two search modes:

- **Single-stage mode**: every candidate gets a full remote run (maxiter=300, full resolution)
- **Two-stage mode**: cheap probe tiers screen locally, promote survivors to full remote runs

## Bottom-Line Decision

- The autoresearch loop is the right orchestration pattern — minimal, autonomous, greedy hill-climbing with structured logging
- HBT single-stage optimization is CPU-bound (SIMSOPT L-BFGS-B), so GCloud VMs need high CPU count, not GPUs
- Columbia already owns the simulation code, runner, DB, ingest, and scoring — this plan composes those, does not rebuild them
- The search surface is the manifest layer (`SingleStageRunnerConfig`), not raw solver code
- Stage 2 seeds are the starting point — the search explores objective weights, targets, and resolution around known-good seeds

## Non-Goals

- Do not modify SIMSOPT or the single-stage solver
- Do not build a general cloud HPC platform
- Do not replace Columbia's existing runner/DB/ingest infrastructure
- Do not search over solver internals or optimizer algorithms
- Do not require persistent GCloud infrastructure — use ephemeral VMs
- Do not assume probe tiers predict full-run quality until calibrated

## Architecture

```text
Autoresearch Loop (local controller)
    |
    +--> Propose: LLM or algorithmic candidate generator
    |       |
    |       v
    |   Candidate = SingleStageRunnerConfig params
    |
    +--> [Two-Stage Only] Probe Screen
    |       |
    |       +--> Tier 0: schema + seed validation (instant)
    |       +--> Tier 1: init_only=True, nphi=63, ntheta=32 (~30s)
    |       +--> Tier 2: init_only=False, maxiter=5, nphi=127 (~2-5min)
    |       +--> reject / promote
    |
    +--> Run: GCloud remote dispatch
    |       |
    |       +--> create CPU VM
    |       +--> sync repo + Stage 2 seeds
    |       +--> run single_stage_banana_example.py (maxiter=300, full resolution)
    |       +--> collect results.json + log.txt
    |       +--> delete VM
    |
    +--> Evaluate: parse results, compute combined_score
    |
    +--> Decide: if score improves frontier → keep, else → discard
    |
    +--> Log: update SQLite, results.tsv, frontier ranking
    |
    +--> Repeat (forever, until interrupted)
```

## Search Surface

All search is over `SingleStageRunnerConfig` fields — the manifest layer.

### Mutable Parameters

| Parameter | Type | Range | Default | Physics Meaning |
|-----------|------|-------|---------|-----------------|
| `stage2_bs_path` | str | valid seed paths | — | Stage 2 Biot-Savart starting coil field |
| `banana_surf_radius` | float | [0.15, 0.30] | 0.22 | Minor radius of coil winding surface (m) |
| `iota_target` | float | [0.10, 0.25] | 0.15 | Target rotational transform |
| `vol_target` | float | [0.05, 0.20] | 0.10 | Target plasma volume |
| `cc_dist` | float | [0.03, 0.10] | 0.05 | Minimum coil-coil distance threshold |
| `cc_weight` | float | [1, 1000] | 100 | Coil-coil distance penalty weight |
| `curvature_threshold` | float | [10, 60] | 20 | Max coil curvature before penalty |
| `curvature_weight` | float | [0.001, 10] | 0.1 | Curvature penalty weight |
| `constraint_weight` | float | [0.1, 100] | 1.0 | Global engineering constraint multiplier |
| `mpol` | int | [4, 18] | 8 | Poloidal Fourier resolution |
| `ntor` | int | [3, 10] | 6 | Toroidal Fourier resolution |

### Stage 2 Seed Parameters (when searching over seeds)

| Parameter | Type | Range | Meaning |
|-----------|------|-------|---------|
| `stage2_seed_cc_threshold` | float | [0.03, 0.10] | Seed coil-coil distance |
| `stage2_seed_curvature_threshold` | float | [20, 60] | Seed curvature limit |

### Probe-Tier Policy Knobs (not candidate knobs)

These are fixed per tier, not searched:

| Parameter | Tier 1 | Tier 2 | Full Run |
|-----------|--------|--------|----------|
| `nphi` | 63 | 127 | 255 |
| `ntheta` | 32 | 32 | 64 |
| `init_only` | True | False | False |
| `maxiter` | 0 | 5 | 300 |

### Not in V1

- New physics objective terms
- Optimizer algorithm changes (L-BFGS-B is fixed)
- Arbitrary solver code mutations
- `mpol` / `ntor` as search dimensions (too expensive per candidate; fix per campaign)

## Candidate Representation

```python
{
    "candidate_id": "cand_20260317_001",
    "parent_id": "baseline",
    "params": {
        "stage2_bs_path": "DATABASE/COIL_OPTIMIZATION/.../biot_savart.json",
        "banana_surf_radius": 0.22,
        "iota_target": 0.15,
        "vol_target": 0.10,
        "cc_dist": 0.05,
        "cc_weight": 100.0,
        "curvature_threshold": 20.0,
        "curvature_weight": 0.1,
        "constraint_weight": 1.0,
        "mpol": 8,
        "ntor": 6
    }
}
```

Translation to execution: `candidate.params → SingleStageRunnerConfig → .to_cli_args() → subprocess call to single_stage_banana_example.py`.

No patching of source code. The manifest layer is the contract.

## Execution Modes

### Single-Stage Mode

Every candidate goes directly to a full remote run. No local screening.

```text
candidate → Tier 0 validate → GCloud full run (maxiter=300) → score → rank
```

Use when:
- Search space is narrow (weight sweeps around a known-good seed)
- You trust the seed quality and just want to explore objective weighting
- You have budget for full runs (~$0.05-0.20 per candidate on CPU VMs)

### Two-Stage Mode

Probe tiers screen candidates cheaply. Only survivors get full remote runs.

```text
candidate → Tier 0 → Tier 1 (init_only, ~30s) → Tier 2 (5 iters, ~2-5min) → promote? → GCloud full run → score → rank
```

#### Tier 0: Validation (instant, zero cost)

- Schema: all fields present, types correct
- Ranges: all values within declared bounds
- Seed resolution: `stage2_bs_path` exists and is loadable
- Compatibility: `mpol`, `ntor` consistent with `nphi`, `ntheta`

#### Tier 1: Init Probe (~30 seconds)

Run `single_stage_banana_example.py` with `--init_only --nphi 63 --ntheta 32`.

Checks:
- Boozer surface initializes without failure
- No immediate self-intersection
- Initial field error is finite and within 10x of baseline
- Initial iota and volume are in a plausible range

Score: `combined_score` on init-only metrics. Reject if Boozer init fails, self-intersects, or field error is extreme.

#### Tier 2: Short Optimization (~2-5 minutes)

Run with `--maxiter 5 --nphi 127 --ntheta 32`.

Checks:
- Objective decreases over 5 iterations (optimizer is making progress)
- No NaN in objectives
- Field error and iota/volume are moving toward targets

Score: `combined_score` on 5-iteration metrics. Promote top N% or all candidates that beat a threshold.

#### Tier 3: Full Remote Run (promoted candidates)

`--maxiter 300 --nphi 255 --ntheta 64 --mpol 8 --ntor 6`. This is the real evaluation.

### Promotion Policy

Promote when ALL of:
- Tier 0 passed
- Tier 1: Boozer init succeeded, no self-intersection, field error < 10x baseline
- Tier 2: objective decreased monotonically or nearly so, combined_score > threshold
- Budget: daily promotion cap not exceeded

Tier 2 threshold calibrated against the frontier: promote if `combined_score >= 0.5 * frontier_best_score` (generous — probe scores are noisy predictors).

## Scoring

### Combined Score (Columbia's existing formula)

```python
def combined_score(metrics: dict, params: dict) -> float:
    """Higher is better. Returns 1/(1+penalty), range (0, 1]."""
    if metrics.get("status") != "done":
        return 0.0

    penalty = 0.0

    field_error = metrics.get("field_error")
    penalty += 25.0 * field_error if field_error is not None else 2.0

    objective_j = metrics.get("objective_j")
    if objective_j is not None:
        penalty += 2.5 * objective_j

    boozer_residual = metrics.get("boozer_residual")
    if boozer_residual is not None:
        penalty += min(boozer_residual, 10.0)

    iota_target = params.get("iota_target", 0.15)
    final_iota = metrics.get("final_iota")
    penalty += 4.0 * abs(final_iota - iota_target) if final_iota is not None else 1.0

    vol_target = params.get("vol_target", 0.10)
    final_volume = metrics.get("final_volume")
    penalty += 8.0 * abs(final_volume - vol_target) if final_volume is not None else 1.0

    curvature_threshold = params.get("curvature_threshold", 20.0)
    max_curvature = metrics.get("max_curvature")
    if max_curvature is not None and max_curvature > curvature_threshold:
        penalty += (max_curvature - curvature_threshold) / max(curvature_threshold, 1.0)

    if metrics.get("self_intersecting", False):
        penalty += 5.0

    return 1.0 / (1.0 + penalty)
```

### Feedback String (for LLM-driven search)

```text
score=0.142 (frontier best: 0.187, rank: 5/23).
Config: iota_target=0.15, vol_target=0.10, cc_weight=100, curvature_threshold=20.
Result: field_error=0.0104, final_iota=0.148, final_volume=0.111, max_curvature=18.3.
Iota close to target (delta=-0.002). Volume slightly over (delta=+0.011).
Field error dominates penalty (contributes 0.26 of 6.04 total penalty).
No self-intersection. Curvature within threshold.
Previous best: field_error=0.0082 with cc_weight=150, curvature_threshold=25.
Suggestion: try reducing field error by increasing constraint_weight or tightening curvature.
```

### Metrics Source

| Metric | Source | Authority |
|--------|--------|-----------|
| `field_error` | `results.json` → `FIELD_ERROR` | Primary |
| `final_iota` | `results.json` → `FINAL_IOTA` or log.txt regex | Primary |
| `final_volume` | `results.json` → `FINAL_VOLUME` | Primary |
| `self_intersecting` | `results.json` → `SELF_INTERSECTING` | Primary |
| `max_curvature` | `results.json` or log.txt | Primary |
| `objective_j` | log.txt → `^Objective J\s*=\s*(.+)$` | Supplementary |
| `boozer_residual` | log.txt → `^Boozer Residual\s*=\s*(.+)$` | Supplementary |
| `nonqs_ratio` | log.txt → `^nonQS ratio\s*=\s*(.+)$` | Supplementary |
| `iterations` | `results.json` → `iterations` | Supplementary |

## GCloud Backend

### Why CPU, Not GPU

SIMSOPT's single-stage solver uses L-BFGS-B (SciPy), Boozer surface transforms, and coil geometry operations. These are CPU-bound, multi-threaded numerical workloads. No CUDA kernels. GPU VMs would waste money.

### VM Specification

```yaml
machine_type: c2-standard-16   # 16 vCPU, 64GB RAM — good for full runs
# machine_type: c2-standard-8  # 8 vCPU, 32GB RAM — good for probes
# machine_type: c2-standard-30 # 30 vCPU, 120GB RAM — for high-res runs
zone: us-central1-a            # or multi-zone fallback
boot_disk:
  image: projects/ubuntu-os-cloud/global/images/family/ubuntu-2404-lts
  size_gb: 50
  type: pd-balanced
scheduling:
  preemptible: true
  automatic_restart: false
```

### Cost Model

| Machine | vCPU | RAM | Preemptible $/hr | Est. per full run (30min) | 100 runs |
|---------|------|-----|-------------------|---------------------------|----------|
| c2-standard-8 | 8 | 32GB | ~$0.10 | ~$0.05 | ~$5 |
| c2-standard-16 | 16 | 64GB | ~$0.20 | ~$0.10 | ~$10 |
| c2-standard-30 | 30 | 120GB | ~$0.37 | ~$0.19 | ~$19 |

Full run duration depends on `mpol`, `ntor`, `maxiter`, and resolution. Typical range: 10-60 minutes for maxiter=300 at mpol=8.

Two-stage campaign budget (100 candidates, ~30% promotion rate):
- 100 Tier 1 probes locally or on c2-standard-8: ~$0-5
- 30 Tier 2 probes on c2-standard-8: ~$1.50
- 30 full runs on c2-standard-16: ~$3.00
- **Total: ~$5-10 for 100 candidates screened, 30 fully evaluated**

### Backend Interface

```python
class GCloudBackend:
    def submit(self, candidate: dict, tier: int, submission_id: str) -> str:
        """Create VM, upload code + seeds, start simulation. Returns instance name."""

    def status(self, instance_name: str) -> str:
        """Poll: 'pending' | 'running' | 'done' | 'failed' | 'preempted'."""

    def fetch(self, instance_name: str) -> dict:
        """Download results.json + log.txt from GCS. Return parsed metrics."""

    def cancel(self, instance_name: str) -> None:
        """Stop and delete VM."""

    def cleanup(self, instance_name: str) -> None:
        """Delete VM, disks, GCS artifacts."""
```

### Remote Execution Flow

```text
1. gcloud compute instances create ar-run-{submission_id} \
     --machine-type=c2-standard-16 \
     --zone=us-central1-a \
     --preemptible \
     --metadata=startup-script=...

2. Startup script:
   a. Install system deps (gfortran, libopenblas, etc.)
   b. Install uv, clone Columbia repo
   c. uv sync (install SIMSOPT + deps)
   d. Sync Stage 2 seeds from GCS:
      gsutil -m cp -r gs://{bucket}/seeds/ ./DATABASE/
   e. Write manifest.json from candidate params
   f. Run: python single_stage_banana_example.py [CLI args from manifest] > log.txt 2>&1
   g. Upload results:
      gsutil cp results.json log.txt gs://{bucket}/runs/{submission_id}/
   h. Touch completion marker:
      gsutil cp /dev/null gs://{bucket}/runs/{submission_id}/_DONE

3. Controller polls gs://{bucket}/runs/{submission_id}/_DONE every 30s

4. On completion: fetch results.json + log.txt, parse, score, update SQLite

5. gcloud compute instances delete ar-run-{submission_id}
```

### Seed Data Strategy

Stage 2 seeds (`biot_savart.json` files) are required inputs. They live in `DATABASE/COIL_OPTIMIZATION/` in the Columbia repo.

Pre-stage to GCS once:
```bash
gsutil -m cp -r DATABASE/COIL_OPTIMIZATION/ gs://hbt-autoresearch/seeds/
```

VM startup syncs the needed seed file only (not the entire database):
```bash
gsutil cp gs://hbt-autoresearch/seeds/{seed_path} ./DATABASE/...
```

### Concurrency and Safety

- Max concurrent VMs: configurable, default 4
- Controller polls GCS every 30 seconds
- Preemption: detect via `gcloud compute instances describe`, mark as `preempted`, optionally retry (decrement retry budget)
- Stale cleanup: VMs older than `2 * expected_runtime` are force-deleted
- Cost guard: campaign-level spending cap, controller halts if exceeded
- Auth: ambient `gcloud` credentials only, no keys in code or manifests

### Environment Bootstrap

SIMSOPT has non-trivial dependencies (Fortran compiler, OpenBLAS, etc.). Options:

1. **Startup script installs everything** — simple, but adds 5-10 min overhead per VM
2. **Pre-baked VM image** — bake deps + SIMSOPT into a custom image, boot fast
3. **Container** — Docker image with SIMSOPT pre-installed, run on Container-Optimized OS

Recommendation: Start with option 1 (startup script). Move to option 2 (custom image) once the pipeline is validated — the one-time image bake pays for itself after ~20 runs.

## The Autoresearch Loop (Adapted)

### program.md Equivalent

The agent's operating instructions, adapted from autoresearch:

```
LOOP FOREVER:

1. Read frontier state: current best configs and scores
2. Propose a new SingleStageRunnerConfig (perturb weights, try new seed, shift targets)
3. [Two-Stage] Run Tier 0 + Tier 1 locally. If rejected, log and skip.
4. Submit to GCloud (Tier 2 probe or full run depending on mode)
5. Wait for completion (poll GCS)
6. Parse results.json + log.txt, compute combined_score
7. Log to SQLite + results.tsv:
   - candidate_id, combined_score, field_error, status, description
8. If combined_score > frontier best:
   - Keep: update frontier, log as "keep"
9. If not:
   - Discard: log as "discard", move to next candidate
10. Repeat. Never stop. Never ask.
```

### results.tsv Format

```
candidate_id	combined_score	field_error	status	description
baseline_001	0.142	0.0104	keep	baseline: iota15 seed, default weights
cand_002	0.187	0.0082	keep	increased cc_weight=150, curvature_threshold=25
cand_003	0.091	0.0156	discard	lowered constraint_weight=0.5 — field error regressed
cand_004	0.000	—	crash	bad seed path — Boozer init failed
cand_005	0.195	0.0078	keep	cand_002 + vol_target=0.08 — tighter volume, better field
```

### Keep/Discard Logic

Greedy hill-climbing (same as autoresearch):
- If `combined_score > current_best_score`: **keep**, this is the new frontier
- If equal or worse: **discard**, revert to previous best as parent for next candidate

Optional: maintain a Pareto frontier across multiple objectives (field error vs. iota closeness vs. engineering feasibility) instead of single-scalar greedy.

## State Management

### SQLite Schema

Reuse Columbia's existing `db.py` schema (runs, metrics, repeat_groups) for archive state. Add search-specific tables:

```sql
CREATE TABLE search_campaigns (
    campaign_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    mode TEXT NOT NULL,                -- 'single_stage' | 'two_stage'
    machine_type TEXT NOT NULL,        -- 'c2-standard-16' etc.
    zone TEXT NOT NULL,
    base_seed TEXT NOT NULL,           -- starting Stage 2 seed path
    fixed_params TEXT NOT NULL,        -- JSON: mpol, ntor, etc. fixed for campaign
    search_surface TEXT NOT NULL,      -- JSON: which params are mutable + ranges
    budget_cap_usd REAL,
    budget_spent_usd REAL DEFAULT 0,
    status TEXT DEFAULT 'active'
);

CREATE TABLE search_candidates (
    candidate_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES search_campaigns,
    parent_id TEXT,                    -- previous best candidate
    params TEXT NOT NULL,              -- JSON: full SingleStageRunnerConfig params
    created_at TEXT NOT NULL,
    tier0_status TEXT,                 -- 'pass' | 'fail'
    tier1_status TEXT,                 -- 'pass' | 'fail' | 'skipped'
    tier2_status TEXT,                 -- 'pass' | 'fail' | 'skipped'
    full_run_status TEXT,              -- 'pending' | 'running' | 'done' | 'failed' | 'preempted'
    combined_score REAL,
    decision TEXT,                     -- 'keep' | 'discard' | 'crash'
    description TEXT
);

CREATE TABLE search_runs (
    run_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES search_candidates,
    tier INTEGER NOT NULL,             -- 0, 1, 2, or 3 (full)
    backend TEXT NOT NULL,             -- 'local' | 'gcloud'
    instance_name TEXT,
    submission_id TEXT UNIQUE,
    status TEXT NOT NULL,
    submitted_at TEXT,
    completed_at TEXT,
    result_json TEXT,                  -- JSON: full parsed results.json
    log_excerpt TEXT,                  -- last 200 lines of log.txt
    combined_score REAL,
    field_error REAL,
    final_iota REAL,
    final_volume REAL,
    self_intersecting INTEGER,
    max_curvature REAL,
    failure_reason TEXT
);

CREATE TABLE search_frontier (
    campaign_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    combined_score REAL NOT NULL,
    field_error REAL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (campaign_id, candidate_id)
);
```

Compose with Columbia's archive DB — search runs that complete successfully are also ingested into the main `runs` + `metrics` tables via the existing `ingest.py` path.

## Proposed File Structure

```
autoresearch/                         # forked/adapted repo
├── program.md                        # agent instructions (adapted for HBT)
├── search/
│   ├── __init__.py
│   ├── candidate.py                  # candidate schema, validation, ranges
│   ├── scorer.py                     # combined_score, feedback string
│   ├── probe.py                      # Tier 0/1/2 local probe runner
│   ├── controller.py                 # main autoresearch loop
│   ├── state.py                      # SQLite search tables
│   ├── gcloud_backend.py             # VM lifecycle + GCS artifact sync
│   ├── config.py                     # campaign config (zone, machine, budget)
│   ├── startup_script.sh             # VM bootstrap template
│   └── system_prompt.txt             # LLM system prompt for candidate generation
├── scripts/
│   ├── run_search.py                 # CLI entry point
│   ├── stage_seeds_to_gcs.py         # one-time: upload Stage 2 seeds to GCS
│   ├── bake_vm_image.py              # optional: create pre-baked VM image
│   └── analyze_campaign.py           # frontier analysis + reporting
└── AUTORESEARCH_REMOTE_SEARCH_PLAN.md
```

Columbia repo assets used (not copied):
- `hho_platform/manifests.py` → `SingleStageRunnerConfig`
- `hho_platform/runner.py` → run directory layout
- `hho_platform/ingest.py` → result parsing
- `hho_platform/db.py` → archive ingestion
- `single_stage_banana_example.py` → the actual solver (runs on remote VM)

## LLM-Driven Candidate Generation

### System Prompt (search/system_prompt.txt)

```text
You are optimizing single-stage stellarator banana-coil configurations for the HBT experiment.

OBJECTIVE: Maximize combined_score = 1/(1+penalty), which minimizes field error while
meeting rotational transform (iota) and volume targets under engineering constraints.

PARAMETERS YOU CONTROL (with ranges):
- banana_surf_radius: [0.15, 0.30] m — coil winding surface minor radius
- iota_target: [0.10, 0.25] — target rotational transform
- vol_target: [0.05, 0.20] — target plasma volume
- cc_dist: [0.03, 0.10] — minimum coil-coil distance threshold
- cc_weight: [1, 1000] — coil-coil distance penalty weight
- curvature_threshold: [10, 60] — max curvature before penalty
- curvature_weight: [0.001, 10] — curvature penalty weight
- constraint_weight: [0.1, 100] — global engineering constraint multiplier

SCORING (penalty components, lower penalty = higher score):
- field_error × 25.0 (dominant term — quasi-symmetry deviation)
- |final_iota - iota_target| × 4.0
- |final_volume - vol_target| × 8.0
- boozer_residual (capped at 10)
- curvature excess / threshold (if above threshold)
- +5.0 if self-intersecting

KNOWN PATTERNS:
- Higher cc_weight (100-200) with moderate curvature_threshold (20-30) produces best field errors
- Weight RATIOS matter more than absolute values
- iota_target=0.15 and iota_target=0.20 are the two main families
- banana_surf_radius=0.22 is the established default — deviate cautiously
- Self-intersection is a hard failure — keep curvature_threshold reasonable

FAILURE MODES TO AVOID:
- Boozer init failure: bad seed + target combination
- Self-intersection: curvature_threshold too high or banana_surf_radius too small
- Divergence: extreme weight ratios (e.g., cc_weight=1000 with curvature_weight=0.001)
- Slow convergence: constraint_weight too low

Output a complete parameter dict as JSON. Propose ONE candidate per turn.
```

### Standalone Strategies (no LLM needed)

- **Random**: uniform sample within ranges
- **Perturbation**: current best ± 10-30% on each param independently
- **Latin hypercube**: space-filling design for initial exploration
- **Bayesian (GP surrogate)**: fit Gaussian process to (params → combined_score), acquire next candidate by expected improvement

## Milestones

### Milestone 0: Contract and Round-Trip

- [ ] `candidate.py`: schema with validation, ranges, translation to `SingleStageRunnerConfig`
- [ ] `scorer.py`: `combined_score` from `results.json` + `log.txt` metrics
- [ ] Validate round-trip: candidate dict → CLI args → (simulated) results → score
- [ ] Unit tests for schema, scoring, feedback string

Gate: a candidate can be validated, translated to CLI args, and a results.json can be scored.

### Milestone 1: Local Probe Pipeline

- [ ] `probe.py`: Tier 0 + Tier 1 + Tier 2 local execution
- [ ] Run 5 known configs through all tiers, verify tier scores correlate with full-run scores
- [ ] `state.py`: SQLite search tables with WAL mode
- [ ] `controller.py`: basic loop (generate → probe → log) without remote dispatch

Gate: probe pipeline runs locally and produces ranked candidates.

### Milestone 2: GCloud Backend

- [ ] `gcloud_backend.py`: VM create, poll, fetch, cleanup
- [ ] `startup_script.sh`: bootstrap SIMSOPT environment on fresh Ubuntu VM
- [ ] `stage_seeds_to_gcs.py`: upload Stage 2 seeds to GCS bucket
- [ ] One full remote run end-to-end: submit, poll, fetch results, ingest, score
- [ ] Preemption handling + stale VM cleanup
- [ ] Concurrent VM management (up to 4 parallel)

Gate: a promoted candidate completes on GCloud and its score is ingested into SQLite without manual steps.

### Milestone 3: Full Autoresearch Loop

- [ ] `controller.py`: complete loop with GCloud dispatch
- [ ] Single-stage mode (all candidates → full remote)
- [ ] Two-stage mode (probe → promote → remote)
- [ ] `run_search.py` CLI entry point
- [ ] results.tsv logging
- [ ] Frontier tracking + keep/discard decisions
- [ ] Campaign budget tracking + cost guard

Gate: run a 50-candidate overnight campaign, wake up to ranked results with cost report.

### Milestone 4: LLM-Driven Search

- [ ] `system_prompt.txt` with frontier context injection
- [ ] LLM candidate generator (Claude API or SkyDiscover adapter)
- [ ] Feedback loop: score + feedback string → LLM → next candidate
- [ ] Compare LLM candidates vs. random baseline

Gate: LLM-generated candidates find better configs than random search over same budget.

### Milestone 5: Analysis and Hardening

- [ ] `analyze_campaign.py`: summary report, convergence curves, cost breakdown
- [ ] Parameter importance (which knobs have most effect on score)
- [ ] Pre-baked VM image for faster startup
- [ ] Retry policy, budget alerts, artifact cleanup
- [ ] Integration with Columbia's frontier analysis

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Probe score doesn't predict full-run quality | Wasted promotions or missed good candidates | Calibrate on known configs before trusting screening |
| SIMSOPT install fails on fresh VMs | Blocked pipeline | Pre-bake VM image after first successful run |
| Preemption during long full runs | Lost 30-60 min of compute | Retry budget, prefer shorter `maxiter` for initial campaigns |
| Seed path resolution breaks remotely | All remote runs fail | Pre-stage seeds to GCS, validate path before submit |
| Orphaned VMs accumulate cost | Budget overrun | Max-age cleanup, campaign budget cap, billing alerts |
| Combined_score is poorly calibrated | Search optimizes wrong thing | Keep raw metrics, audit ranking vs. physics judgment |
| SQLite contention from concurrent polling | Controller failures | WAL mode, 5s busy timeout, short write transactions |

## Quick-Start Commands

```bash
# One-time: stage seeds to GCS
python scripts/stage_seeds_to_gcs.py \
  --source DATABASE/COIL_OPTIMIZATION/ \
  --bucket hbt-autoresearch

# Single-stage: 20 candidates, all full runs on GCloud
python scripts/run_search.py \
  --mode single_stage \
  --strategy random \
  --machine c2-standard-16 \
  --zone us-central1-a \
  --candidates 20 \
  --seed DATABASE/COIL_OPTIMIZATION/.../biot_savart.json

# Two-stage: 100 candidates, probe locally, promote top 30%
python scripts/run_search.py \
  --mode two_stage \
  --strategy perturbation \
  --machine c2-standard-16 \
  --zone us-central1-a \
  --candidates 100 \
  --promote-ratio 0.3 \
  --budget-cap 15.00

# Analyze campaign results
python scripts/analyze_campaign.py --campaign latest
```
