# Stellarator Coil Optimization Registry -- Design

## Overview

Replaces the shared Google Sheets spreadsheet with a SQLite database (single
file, zero-config, works on every team member's laptop). The DB file lives in
a shared location (Dropbox/Google Drive/git-annex) and SQLite WAL mode handles
concurrent reads with a single writer.

## 1. Relational Schema

```
runs            1:1  metrics      (physics outputs)
                1:1  params       (optimizer inputs)
                1:1  poincare     (Poincare validation)
                1:1  seeds        (Stage 2 seed provenance)
                1:N  artifacts    (file manifest)
                M:N  tags         (labels)
```

### Why separate tables instead of one wide table?

- `metrics` and `params` are split from `runs` so that adding a new weight
  param or a new physics metric is an `ALTER TABLE ADD COLUMN` on the narrow
  table, not a disruptive change to the main query surface.
- `poincare` is separate because it is populated *after* the run completes,
  often by a different person. It has its own `validated_by` / `validated_at`.
- `seeds` captures the Stage 2 -> single-stage provenance chain. When a seed
  came from another registry run, `seed_run_id` creates a FK link.

### Key design decisions

1. **`run_id` is a UUID v7** (time-sortable, no coordination needed between
   machines). Not an auto-increment integer -- those collide when two people
   import runs independently.

2. **`validation_status` is a first-class column on `runs`**, not a tag.
   Every query that builds a "frontier" or "best of" list filters on it.
   The four states form a lifecycle:

   ```
   pending ──> validated
          └──> fails_validation
          └──> diagnostic_only
   ```

3. **No JSON blobs for metrics.** Every confirmed physics metric is a typed
   column. `params.extra_json` is the *only* JSON column, and it exists solely
   as a staging area for new params that haven't been promoted to columns yet.

4. **`schema_versions` table** tracks migrations. See section 4.

## 2. Artifact Storage Layout

Artifacts live on the filesystem (or S3 bucket), not in the DB. The DB stores
a manifest (`artifacts` table) that references them by `storage_key`.

### Filesystem layout

```
registry/
  registry.db                       # SQLite database file
  artifacts/
    <run_id>/                       # one directory per run
      poincare_2x2.png
      surf_opt.vts
      curves_opt.vtu
      boozer_surface.json
      results.json
      log.txt
      biot_savart_opt.json
      biot_savart_init.json
      CrossSectionOptimized.png
      NormPlotOptimized.png
```

### Rules

- **`storage_key`** = `<run_id>/<filename>` (relative to `artifacts/` root).
- **SHA-256 hash** stored per artifact for integrity checks and dedup.
  Two runs that produce byte-identical `biot_savart_opt.json` will have
  different `storage_key` values but the same `sha256`, enabling detection.
- **No nesting beyond run_id.** Flat per-run directories keep `ls` useful.
- For S3: prefix = `artifacts/<run_id>/`, same structure. The `storage_key`
  column works unchanged.

## 3. DB <-> Artifact Cross-References

### DB -> Artifacts

```sql
SELECT storage_key FROM artifacts
WHERE run_id = ? AND kind = 'poincare_plot';
```

### Artifacts -> DB

Every artifact directory is named by `run_id`. Given a file on disk at
`artifacts/019537ab-.../poincare_2x2.png`, the run_id is the parent
directory name. The `artifacts` table confirms the association.

### Integrity check

```python
for row in db.execute("SELECT storage_key, sha256 FROM artifacts"):
    path = ARTIFACT_ROOT / row["storage_key"]
    assert path.exists(), f"Missing: {path}"
    if row["sha256"]:
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]
```

## 4. Schema Evolution

### Strategy: additive migrations, never destructive

1. **`schema_versions` table** records which migrations have been applied.
   Each migration is a `.sql` file with a version number:

   ```
   registry/
     migrations/
       001_initial.sql
       002_add_max_force.sql
       003_add_coil_length.sql
   ```

2. **New metrics**: `ALTER TABLE metrics ADD COLUMN new_metric_name REAL;`
   SQLite supports this. Existing rows get NULL. No data loss.

3. **New params**: Either `ALTER TABLE params ADD COLUMN ...` or, for truly
   experimental params, put them in `params.extra_json` first. Once stable,
   promote to a real column with a migration.

4. **New artifact kinds**: Add to the CHECK constraint. In SQLite this
   requires a table rebuild, but the migration script handles it:

   ```sql
   -- In a migration file:
   ALTER TABLE artifacts RENAME TO artifacts_old;
   CREATE TABLE artifacts ( ... updated CHECK ... );
   INSERT INTO artifacts SELECT * FROM artifacts_old;
   DROP TABLE artifacts_old;
   ```

5. **Migration runner** (pseudocode):

   ```python
   current = db.execute("SELECT MAX(version) FROM schema_versions").fetchone()[0] or 0
   for migration in sorted(glob("migrations/*.sql")):
       v = int(migration.stem.split("_")[0])
       if v > current:
           db.executescript(migration.read_text())
           db.execute("INSERT INTO schema_versions (version, description) VALUES (?, ?)",
                      (v, migration.stem))
   ```

### What this gives the 3-5 person team

- Anyone can pull the latest `.db` file and run queries immediately.
- New metrics/params added by one person propagate via the migration files.
- No downtime, no coordination -- just run the migrator on open.

## 5. The Poincare Gate

### Validation lifecycle

Every run enters the registry with `validation_status = 'pending'`. This means
the optimizer finished and produced metrics, but nobody has confirmed the result
with Poincare field-line tracing.

```
                        ┌──────────────────────┐
  run_one.py ──ingest──>│  pending              │
                        │  (metrics populated)  │
                        └──────┬───────────────┘
                               │
                    Poincare analysis
                               │
              ┌────────────────┼────────────────┐
              v                v                v
     ┌────────────┐   ┌───────────────┐  ┌──────────────┐
     │ validated   │   │ fails_valid.  │  │ diagnostic   │
     │             │   │               │  │ _only        │
     │ good flux   │   │ islands/chaos │  │ intentional  │
     │ surfaces    │   │ detected      │  │ exploration  │
     └────────────┘   └───────────────┘  └──────────────┘
```

### How it works in practice

1. After a batch of runs, someone runs Poincare on the promising ones
   (low field error, not self-intersecting).

2. They update the DB:
   ```sql
   UPDATE runs SET validation_status = 'validated' WHERE run_id = ?;
   INSERT INTO poincare (run_id, validated_by, survival_fraction, ...)
   VALUES (?, 'jungdae', 0.98, ...);
   ```

3. All "frontier" and "best of" queries filter on validation_status:
   ```sql
   SELECT * FROM v_validated_frontier;  -- only Poincare-confirmed
   SELECT * FROM v_needs_poincare;      -- queue of runs to validate
   ```

4. The `v_needs_poincare` view is the team's work queue: it shows runs
   sorted by field error that haven't been Poincare-checked yet.

### Why this matters

The old spreadsheet mixed validated and unvalidated results. A run could have
excellent field error but terrible flux surfaces. The Poincare gate makes
validation status a first-class filter on every query, so the team never
accidentally treats an unvalidated result as a confirmed good configuration.

## Migration from Current System

The existing `results.jsonl` (517 runs) and `lab.py` in-memory SQLite can
coexist with this registry. The migration path:

1. `results.jsonl` remains the local agent's append-only log.
2. A `registry_ingest.py` script reads JSONL and inserts into the registry DB,
   mapping existing fields to the new schema.
3. `lab.py` can optionally read from the registry DB instead of JSONL,
   using the `v_full_run` view which has the same columns.
4. Artifact directories (`single_stage_results/`, `stage2_seeds/`) get
   reorganized into `artifacts/<run_id>/` with manifest entries.
