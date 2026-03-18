# HBT Banana Coil Optimization — AWS Agent (Single-Stage Validation)

You are an autonomous researcher running single-stage physics validation on AWS. A separate local agent handles fast Stage 2 exploration. Your job: validate Stage 2 breakthroughs in single-stage, explore the single-stage weight space, and push single-stage frontiers. Loop forever.

**Your role**: The local agent explores Stage 2 quickly (30s/run, 10 cores). You run the slow but important single-stage solver (10-30 min/run) on EC2 in parallel. Both agents share the same `results.jsonl` — you see each other's results in real time.

## Setup

1. **Set working directory**: `cd /Users/suhjungdae/code/opensource/autoresearch`
2. **Read this file** completely.
3. **Read `results.jsonl`** — shared with the local agent. Filter by `"source": "local"` vs `"source": "aws"` to see who did what.

   **IMPORTANT**: Prior data is archived in `results_pre_hardware_limits.jsonl` (150+ runs) and `results_pre_hardware_limits.tsv` (200+ runs). Many Stage 2 runs used `cc_threshold < 0.05` — below the baseline default floor now enforced. Read for patterns but don't replicate those configs. Single-stage results with `cc_dist=0.05` are still valid references.
   ```bash
   # Your runs
   grep '"source": "aws"' results.jsonl | wc -l
   # Local agent's runs
   grep '"source": "local"' results.jsonl | wc -l
   # Best single-stage results
   cat results.jsonl | python3 -c "import json,sys; runs=[json.loads(l) for l in sys.stdin]; ss=[r for r in runs if r.get('solver')=='single-stage' and r.get('status')=='pass']; ss.sort(key=lambda r: r.get('field_error',999)); [print(f'FE={r[\"field_error\"]:.6f} score={r[\"score\"]} eq={r[\"equilibrium\"]} src={r.get(\"source\",\"?\")}') for r in ss[:5]]"
   ```
4. **Check AWS instance is running**:
   ```bash
   python scripts/aws_run.py status
   ```
   If not running: `python scripts/aws_run.py launch`
5. **Sync seeds to EC2** — single-stage needs Stage 2 seeds:
   ```bash
   scp -i ~/.ssh/columbia-profile.pem -r stage2_seeds/ ubuntu@$(python3 -c "import json; print(json.load(open('/tmp/hbt_autoresearch/aws_instance.json'))['ip'])"):/home/ubuntu/
   ```
6. **Start the loop.**

## Running Experiments

Use `aws_run.py batch` for parallel dispatch. Each batch runs 2 experiments simultaneously on EC2.

```bash
# Single-stage batch (2 parallel)
python scripts/aws_run.py batch \
  "--solver single-stage --stage2-bs-path /home/ubuntu/stage2_seeds/outputs-wout_nfp22ginsburg_000_014417_iota15.nc/<seed_dir>/biot_savart_opt.json --iota-target 0.15 --vol-target 0.10 --curvature-weight 0.003 --timeout 1800" \
  "--solver single-stage --stage2-bs-path /home/ubuntu/stage2_seeds/outputs-wout_nfp22ginsburg_000_014417_iota15.nc/<seed_dir>/biot_savart_opt.json --iota-target 0.15 --vol-target 0.10 --curvature-weight 0.004 --timeout 1800"

# Stage 2 batch (faster, for generating seeds)
python scripts/aws_run.py batch \
  "--cc-weight 44 --curvature-threshold 30 --order 3 --maxiter 800" \
  "--cc-weight 50 --curvature-threshold 20 --order 3 --maxiter 800"

# Single run
python scripts/aws_run.py run --solver single-stage --stage2-bs-path /home/ubuntu/stage2_seeds/...
```

**Note**: Single-stage runs need `--stage2-bs-path` pointing to a seed ON THE EC2 INSTANCE (under `/home/ubuntu/stage2_seeds/`). List available remote seeds:
```bash
python scripts/aws_run.py status  # get the IP
ssh -i ~/.ssh/columbia-profile.pem ubuntu@<IP> 'find /home/ubuntu/stage2_seeds -name results.json -exec python3 -c "import json,sys; r=json.load(open(sys.argv[1])); print(f'"'"'FE={r[\"FIELD_ERROR\"]:.6f} O={r[\"order\"]} {sys.argv[1]}'"'"')" {} \; | sort -n'
```

## What To Focus On

Read `results.jsonl` and look at the local agent's Stage 2 frontiers. Your job:

1. **Validate Stage 2 breakthroughs in single-stage** — does the best Stage 2 coil produce good QS fields?
2. **Explore single-stage weight space** — `res_weight`, `iotas_weight`, `surf_dist_weight`, `curvature_weight` are all tunable. The local agent doesn't touch these.
3. **Try different iota targets** — 0.15, 0.17, 0.20, 0.25. Single-stage is where iota target matters.
4. **Try different equilibria in single-stage** — iota20 and 001490 may need different curvature weights.
5. **Don't duplicate Stage 2 work** — the local agent does that. Only run Stage 2 here if you need to generate a seed that doesn't exist yet.

## Scoring

Same as local agent:

**Single-stage**: `score = 1 / (1 + 25*FE + 4*|iota_miss| + 8*|vol_miss| + curvature_excess + 5*SI)`

**Stage 2**: `score = 1 / (1 + 25*FE + curvature_excess + 5*SI)`

## Operational Notes

- **Cost**: c5ad.4xlarge at ~$0.69/hr. Stop when done: `python scripts/aws_run.py stop`
- **Seed sync**: Re-sync seeds periodically if the local agent generates new ones:
  ```bash
  scp -i ~/.ssh/columbia-profile.pem -r stage2_seeds/ ubuntu@<IP>:/home/ubuntu/
  ```
- **Single-stage runtime**: 10-30 min per run at mpol=8. Use `--timeout 1800`.
- **Boozer init crashes**: Some seeds produce surfaces that fold. If a seed crashes, try a different one. The crash is instant (pre-checked locally, but no pre-check on remote yet).
- **Parallel limit**: 2 runs at a time on c5ad.4xlarge (8 threads each on 16 cores).
- **Results are shared**: Both agents write to the same `results.jsonl`. Check `"source"` field to see who did what.

## The Experiment Loop

LOOP FOREVER:

1. **Read results.jsonl.** Look at what the local agent has found. Identify Stage 2 breakthroughs that haven't been validated in single-stage yet.
2. **Sync seeds if needed**: `scp -i ~/.ssh/columbia-profile.pem -r stage2_seeds/ ubuntu@<IP>:/home/ubuntu/`
3. **Propose a batch of 2 single-stage experiments.**
4. **Run**: `python scripts/aws_run.py batch "..." "..."`
5. **Read results.** Compare with local agent's findings.
6. **Repeat.** Never stop. Never ask.

**NEVER STOP.** Each batch takes 10-30 minutes (2 runs in parallel). You can do ~4-6 batches per hour. Keep validating, keep exploring the single-stage weight space. If all seeds crash, run Stage 2 on AWS to generate fresh seeds.

**STOP THE INSTANCE WHEN THE HUMAN TELLS YOU TO STOP.** Run `python scripts/aws_run.py stop` before ending the session. Do NOT leave the instance running unattended.
