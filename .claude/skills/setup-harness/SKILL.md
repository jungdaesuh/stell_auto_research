---
name: setup-harness
description: Interactive first-time setup of the autoresearch harness for a new user's simsopt fork — writes .env, generates a campaign program file from the template, scaffolds lessons memory, and smoke-tests the full pipeline. Use when a new collaborator clones this repo, or to re-generate the program file for a new campaign.
---

# setup-harness

Bootstrap this autoresearch harness against the user's own simsopt fork and
research campaign. The outcome is the four-piece system: **program file**
(agent system prompt) + **run.py** (solver runner) + **results.db/jsonl**
(experiment database) + **LESSONS.md** (research memory), all verified by a
real smoke run.

Run the phases in order. Do not skip verification.

## Phase 1 — Preflight

1. If `.env` already exists, read it and treat its values as defaults to
   confirm rather than ask from scratch. Tell the user setup has run before.
2. Read `run.py` (top ~120 lines) to know the current env contract and
   default solver script paths.
3. Read `templates/program_template.md` — you will fill it in Phase 4.

## Phase 2 — Interview

Use AskUserQuestion in small rounds. Prefer detected/confirmable defaults
over open questions. Required facts:

**Round A — paths (environment):**
- `SIMSOPT_ROOT`: path to their simsopt repo root.
- `SIMSOPT_PYTHON`: python interpreter with simsopt installed.
- `EQUILIBRIA_DIR`: directory holding their `wout_*.nc` files.

After getting paths, **verify each one immediately** (dir exists, python
runs `-c "import simsopt"`, equilibria dir contains `wout_*.nc`). If a check
fails, say what failed and re-ask — do not proceed on broken paths.

**Round B — solver layout:**
- Check whether the default script paths exist under their `SIMSOPT_ROOT`:
  - `examples/single_stage_optimization/STAGE_2/banana_coil_solver.py`
  - `examples/single_stage_optimization/SINGLE_STAGE/single_stage_banana_example.py`
  - `examples/single_stage_optimization/POINCARE_PLOTTING/poincare_surfaces.py`
- If any is missing, search their repo for the actual locations (Glob for
  the filenames, then plausible variants) and confirm with the user. Record
  non-default paths as `STAGE2_SCRIPT` / `SINGLE_STAGE_SCRIPT` /
  `POINCARE_SCRIPT` overrides (relative to `SIMSOPT_ROOT`).

**Round C — campaign identity (free-text, the user's physics, not yours):**
- Campaign title and 2–5 sentence mission.
- Success metric: the one measurable claim that defines success.
- Hard invariants: hardware limits, conventions, ceilings (offer "none yet —
  fill in later" as a valid answer). Remind the user these remove agent
  freedom — keep them to real physical/hardware limits.
- Constraint floors (curvature threshold, coil-coil distance, length target,
  etc.) — offer the run.py defaults as the baseline.

**Round D — execution policy:**
- Typical timeout and `--omp-threads` for this machine.
- Autonomy: "never stop" loop vs bounded sessions.

**Round E — artifacts & directory layout:**
- `OUTPUT_BASE`: scratch dir for live solver runs (default
  `/tmp/stellarator_harness`). Crashed runs always leave their dir +
  `run.log` here for debugging.
- `KEEP_ARTIFACTS`: retention for completed runs' solver outputs (plots,
  coil JSONs, surfaces) — `none` (discard after ingest, default), `pass`
  (keep passing runs), `all` (keep every completed run). Recommend `pass`
  for a new campaign: champions stay reproducible without unbounded disk
  growth.
- `ARTIFACTS_DIR`: where kept run dirs land, named by run id so they join
  back to `results.db` (default `<repo>/artifacts`).
- `STAGE2_SEED_DIR`: Stage 2 seed archive that single-stage warm-starts
  from (default `<repo>/stage2_seeds`).
- Ask whether results (`results.db`/`results.jsonl`) staying at the repo
  root is fine — they always live there; if the user wants them elsewhere,
  suggest symlinking the repo onto the larger volume rather than patching
  run.py.
- Estimate disk: one kept run dir is typically O(1-50 MB); with `all` and
  hundreds of runs per day this adds up — say so when they pick `all`.

## Phase 3 — Introspect their solver

The harness passes a fixed set of flags (see `_build_cli` in `run.py`).
Their fork's argparse may differ. Detect drift **before** the smoke run:

1. Grep `add_argument` in their stage2 and single-stage scripts.
2. Diff against **every** flag run.py passes at launch: the per-solver flags
   `_build_cli` emits, plus `--output-root` which `_run_experiment` appends
   to both solvers (run.py scrapes `results.json` from that dir — a solver
   that ignores or rejects `--output-root` breaks result ingestion, not
   just launch).
3. Report any flag run.py passes that their script does not accept, and any
   interesting flags their script has that run.py does not pass. Missing
   flags in their script = the smoke run will crash; tell the user the two
   options: (a) adapt their script, (b) edit `_build_cli` in run.py to drop
   the flag for their fork.

Also scan `EQUILIBRIA_DIR`: list the `wout_*.nc` files. If netCDF reading is
available via their python, extract NFP/iota-profile basics for the
equilibria table; otherwise just list filenames. Note: `--equilibrium`
accepts a raw filename present in `EQUILIBRIA_DIR`, so their files work
without touching the registry in run.py.

## Phase 4 — Generate

1. **`.env`** — write from the interview answers (never commit it; it is the
   machine-specific config). Include the Round E layout values; omit any the
   user left at default so the file stays minimal.
2. **`program_<campaign-slug>.md`** — fill every `{{PLACEHOLDER}}` in
   `templates/program_template.md` with interview + introspection results:
   - `{{PARAMETER_TABLE}}`: only flags their solver actually supports.
   - `{{EQUILIBRIA_TABLE}}`: from the Phase 3 scan.
   - `{{ARTIFACT_LAYOUT}}`: the Round E answers as a table — scratch dir,
     retention policy, artifacts dir, seed store — so the agent knows where
     to find champion outputs and seeds without reading `.env`.
   - Leave explicitly-deferred sections as short "TODO — fill in as lessons
     accumulate" notes rather than inventing physics.
   Remove all template HTML comments from the generated file.

   Keep generated guidance lean: the template's fixed sections are the
   harness contract; the placeholders carry the user's facts. Don't pad
   them with strategy advice — physics findings accumulate in `LESSONS.md`.
3. **`LESSONS.md`** — already shipped with the repo; do not overwrite. If
   missing, restore the scaffold from git.

## Phase 5 — Verify (must end green)

1. Smoke Stage 2: `python run.py --equilibrium <one-of-their-wout-files>
   --maxiter 20 --timeout 300`. Expect a JSON line with `"status"` and a new
   row in `results.jsonl` and `results.db`.
2. If it crashes, read the run log (path printed in stderr / tmpdir),
   diagnose (usually flag drift from Phase 3 or equilibrium resolution), fix,
   and re-run. Do not declare setup done with a failing smoke run.
3. Optional (ask the user — it is slow): micro single-stage seeded from the
   smoke output (`--solver single-stage --maxiter 20 --mpol 2 --ntor 2
   --timeout 600`) to prove the Stage 2 → single-stage handoff.
4. Clean up smoke rows only if the user asks; by default leave them — they
   are honest history.

## Phase 6 — Report

Print a short summary:
- What was written (`.env`, `program_<slug>.md`).
- Detected flag drift and what was done about it.
- The first real launch command.
- How to start the loop: "Read `program_<slug>.md`, then start the
  optimization loop."
- Remind: `LESSONS.md` is append-only memory; the program file is the
  contract; re-run `/setup-harness` to regenerate the program for a new
  campaign (it will not overwrite the old program file — generate under a
  new slug).
