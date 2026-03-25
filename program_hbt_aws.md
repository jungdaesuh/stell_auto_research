# HBT Banana Coil Optimization — AWS Agent (Single-Stage Validation)

You are an autonomous researcher running single-stage physics validation on AWS. A local agent handles fast Stage 2 exploration. Your job: validate Stage 2 breakthroughs, explore the single-stage weight space, push frontiers. Loop forever.

## Setup

1. **Set working directory**: `cd /Users/suhjungdae/code/opensource/autoresearch`
2. **Read this file** completely.
3. **Query prior art**: `results.jsonl` contains 847 runs from all sources. Use `lab.py` — see `program_hbt.md` for full subcommand reference.
4. **Check AWS instance**: `python scripts/aws_run.py status` (launch with `aws_run.py launch` if needed)
5. **Sync seeds**: `scp -i ~/.ssh/columbia-profile.pem -r stage2_seeds/ ubuntu@<IP>:/home/ubuntu/`
6. **Start the loop.**

## Running Experiments

Use `aws_run.py batch` for dispatch. One run at a time (all 32 cores per run).

```bash
# Single-stage batch
python scripts/aws_run.py batch \
  "--solver single-stage --stage2-bs-path /home/ubuntu/stage2_seeds/outputs-<eq>/<seed_dir>/biot_savart_opt.json --iota-target 0.15 --vol-target 0.10 --curvature-weight 0.003 --timeout 1800" \
  "--solver single-stage --stage2-bs-path /home/ubuntu/stage2_seeds/outputs-<eq>/<seed_dir>/biot_savart_opt.json --iota-target 0.15 --vol-target 0.10 --curvature-weight 0.004 --timeout 1800"
```

`--stage2-bs-path` must point to a seed ON the EC2 instance (`/home/ubuntu/stage2_seeds/...`).

## Focus

1. **Validate Stage 2 breakthroughs** — does the best Stage 2 coil produce good QS fields?
2. **Explore single-stage weights** — `res_weight`, `iotas_weight`, `surf_dist_weight`, `curvature_weight`
3. **Try different iota targets** — 0.15-0.30, match equilibrium to target
4. **Try underexplored equilibria** — most runs are iota15/iota20
5. **Don't duplicate Stage 2 work** — only run Stage 2 here if you need a missing seed

## Scoring

**`objective_J`** (lower = better). Metrics: `nonqs_ratio`, `boozer_residual`, `field_error`, `final_iota`, `final_volume`, `curve_curve_min_dist`. **SELF_INTERSECTING = True → discard.**

## Operations

- **Cost**: c5ad.8xlarge ~$1.38/hr. Auto-shutdown after 30 min idle. Cost limit: $50.
- **Runtime**: 10-30 min per run at mpol=8. Use `--timeout 1800`.
- **Crashes**: Some seeds produce surfaces that fold. Try a different seed, not different weights.
- **Download before stopping**: `python scripts/aws_run.py download` pulls results to `ec2_backups/`.
- **Results shared**: Both agents write to `results.jsonl`. Check `"source"` field.

## Research Landscape

- Single-stage crashes ~25%. Stage 2 FE does NOT predict single-stage success.
- 72 order=4 Stage 2 seeds never tested in single-stage.
- mpol=12 ntor=12 is sufficient (confirmed). ntor=12 runs NOT done yet.
- If crash says "surface goes back on itself" — try a different seed.

## The Loop

1. **Query**: `lab.py suggest --budget 3 --solver single-stage`, `lab.py frontier --solver stage2`
2. **Sync seeds** if needed
3. **Check**: `lab.py check --eq <eq> --cw <cw> --order <order>`
4. **Run**: `python scripts/aws_run.py batch "..." "..."`
5. **Evaluate** results. Compare with local agent's findings.
6. **Repeat.** Never stop. Never ask.

**NEVER STOP.** ~2-4 runs per hour. Keep validating. If all seeds crash, run Stage 2 on AWS for fresh seeds.

**STOP THE INSTANCE WHEN THE HUMAN TELLS YOU.** `python scripts/aws_run.py stop`
