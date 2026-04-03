-- Stellarator Coil Optimization Registry
-- SQLite schema (compatible with Postgres via minor type changes noted inline)
--
-- Design principles:
--   1. JSONL remains the append-only source of truth for local agent runs.
--      This DB is the shared, queryable registry that multiple people write to.
--   2. Fixed columns for every metric the physics team has confirmed.
--      No JSON blobs for metrics -- they are all queryable columns.
--   3. Input params split into a separate table (1:1 with runs) so adding
--      new weight params does not widen the main results table.
--   4. Artifacts are filesystem objects referenced by run_id. The DB stores
--      the manifest; the files live in a content-addressed layout.
--   5. Schema versioning via a migrations table -- no destructive ALTERs.

PRAGMA journal_mode = WAL;            -- concurrent readers + single writer
PRAGMA foreign_keys = ON;

-- ===================================================================
-- Schema version tracking
-- ===================================================================
CREATE TABLE IF NOT EXISTS schema_versions (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    description TEXT NOT NULL
);

INSERT INTO schema_versions (version, description)
VALUES (1, 'Initial registry schema');

-- ===================================================================
-- Core: one row per optimization run
-- ===================================================================
CREATE TABLE runs (
    run_id          TEXT PRIMARY KEY,       -- UUID v7 (time-sortable) or nanoid
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),

    -- Who / where / how long
    contributor     TEXT NOT NULL,           -- e.g. "jungdae", "matt"
    machine         TEXT NOT NULL,           -- e.g. "M3 Max", "EC2 c5ad.8xlarge"
    runtime_s       REAL,                    -- wall-clock seconds

    -- Solver identity
    solver          TEXT NOT NULL CHECK (solver IN ('stage2', 'single-stage')),
    method          TEXT NOT NULL DEFAULT 'weighted-sum'
                    CHECK (method IN ('weighted-sum', 'alm')),
    equilibrium     TEXT NOT NULL,           -- e.g. "iota15", "iota20"
    equilibrium_file TEXT,                   -- full wout filename

    -- Optimizer outcome
    optimizer_converged INTEGER,             -- 0/1 boolean
    termination_msg     TEXT,
    iterations          INTEGER,
    objective_J         REAL,

    -- Validation status: the Poincare gate
    --   pending       = run completed, Poincare not yet done
    --   validated     = Poincare confirms good flux surfaces
    --   fails_validation = Poincare shows islands/chaos
    --   diagnostic_only  = exploratory run, not meant for the registry frontier
    validation_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (validation_status IN (
            'pending', 'validated', 'fails_validation', 'diagnostic_only'
        )),

    -- Free-text notes (why this run was interesting, what it tested)
    notes TEXT
);

CREATE INDEX idx_runs_eq          ON runs(equilibrium);
CREATE INDEX idx_runs_solver      ON runs(solver, method);
CREATE INDEX idx_runs_validation  ON runs(validation_status);
CREATE INDEX idx_runs_created     ON runs(created_at);
CREATE INDEX idx_runs_contributor ON runs(contributor);

-- ===================================================================
-- Physics metrics: one row per run (1:1)
-- Every column here was confirmed by the physics team.
-- ===================================================================
CREATE TABLE metrics (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE CASCADE,

    -- Plasma volume (confirmed with Poincare)
    max_volume_m3           REAL,       -- [m^3]

    -- Rotational transform
    iota_actual             REAL,
    iota_target             REAL,
    iota_error              REAL,       -- |actual - target|

    -- Quasi-symmetry
    qs_error                REAL,       -- nonQS ratio
    boozer_residual         REAL,

    -- Coil forces
    max_force_N             REAL,       -- [N] max force on coils

    -- Coil geometry
    coil_length_m           REAL,       -- [m]
    max_curvature_m_inv     REAL,       -- [m^-1]
    max_curvature_2nd_m_inv REAL,       -- [m^-1] 2nd highest (lead-end vs non-lead-end)

    -- Clearances
    coil_coil_dist_m        REAL,       -- [m] minimum coil-coil distance
    coil_surface_dist_m     REAL,       -- [m] minimum coil-plasma distance
    plasma_vessel_dist_m    REAL,       -- [m] minimum plasma-vessel distance

    -- Intermediate (carried from solver output, useful for tracking)
    field_error             REAL,
    self_intersecting       INTEGER,    -- 0/1 boolean
    volume_actual           REAL        -- final volume from solver
);

CREATE INDEX idx_metrics_qs     ON metrics(qs_error);
CREATE INDEX idx_metrics_volume ON metrics(max_volume_m3);
CREATE INDEX idx_metrics_iota   ON metrics(iota_actual);

-- ===================================================================
-- Poincare validation metrics (populated after Poincare analysis)
-- ===================================================================
CREATE TABLE poincare (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE CASCADE,

    validated_at        TEXT,           -- when Poincare was run
    validated_by        TEXT,           -- who ran/reviewed it

    survival_fraction   REAL,          -- fraction of field lines that survive
    first_exit_angle    REAL,          -- toroidal angle of first loss [rad]
    phi_completeness    REAL,          -- fraction of phi slices with good surfaces

    -- Per-slice breakdown (4 phi slices in the standard 2x2 plot)
    slice_0_survival    REAL,
    slice_1_survival    REAL,
    slice_2_survival    REAL,
    slice_3_survival    REAL,

    notes               TEXT           -- e.g. "island at iota=1/5 visible in slice 2"
);

-- ===================================================================
-- Input parameters: one row per run (1:1)
-- Split from runs table so new params can be added without widening
-- the core results table. Every column is directly queryable.
-- ===================================================================
CREATE TABLE params (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE CASCADE,

    -- Resolution
    mpol    INTEGER,
    ntor    INTEGER,
    nphi    INTEGER,
    ntheta  INTEGER,

    -- Optimizer settings
    maxiter     INTEGER,
    maxcor      INTEGER,        -- L-BFGS-B memory
    ftol        REAL,
    gtol        REAL,

    -- Geometry
    major_radius        REAL,   -- [m]
    toroidal_flux       REAL,
    banana_surf_radius  REAL,
    order_param         INTEGER, -- "order" is a SQL keyword, hence order_param

    -- Weights (all solvers)
    cc_weight           REAL,
    curvature_weight    REAL,
    length_weight       REAL,

    -- Thresholds
    cc_threshold        REAL,
    curvature_threshold REAL,

    -- Stage 2 specific
    squared_flux_weight REAL,
    curvature_p_norm    INTEGER,
    num_quadpoints      INTEGER,
    length_target       REAL,
    theta_center        REAL,
    phi_center          REAL,
    theta_width         REAL,
    phi_width           REAL,
    basin_hops          INTEGER,
    basin_stepsize      REAL,
    basin_seed          INTEGER,

    -- Single-stage specific
    iota_target         REAL,
    vol_target          REAL,
    constraint_weight   REAL,
    res_weight          REAL,
    iotas_weight        REAL,
    cs_weight           REAL,
    cs_dist             REAL,
    cc_dist             REAL,
    surf_dist_weight    REAL,
    ss_dist             REAL,
    ss_length_weight    REAL,
    boozer_stage        TEXT CHECK (boozer_stage IN ('initial', 'final') OR boozer_stage IS NULL),
    num_tf_coils        INTEGER,

    -- ALM specific
    alm_enabled         INTEGER DEFAULT 0, -- 0/1 boolean
    alm_outer_iters     INTEGER,
    alm_mu_init         REAL,
    alm_mu_max          REAL,
    alm_mu_increase     REAL,
    alm_tol             REAL,

    -- Overflow: params not yet promoted to columns
    extra_json          TEXT    -- JSON object for new params not in schema yet
);

CREATE INDEX idx_params_mpol  ON params(mpol);
CREATE INDEX idx_params_order ON params(order_param);
CREATE INDEX idx_params_cw    ON params(curvature_weight);

-- ===================================================================
-- Stage 2 seed provenance (for single-stage runs)
-- ===================================================================
CREATE TABLE seeds (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE CASCADE,

    seed_source     TEXT,       -- "autoresearch", "columbia_database", "manual"
    seed_path       TEXT,       -- original path to biot_savart_opt.json
    seed_run_id     TEXT REFERENCES runs(run_id),  -- if seed came from another registry run
    seed_field_error    REAL,
    seed_major_radius   REAL,
    seed_order          INTEGER,
    seed_equilibrium    TEXT
);

-- ===================================================================
-- Artifact manifest: what files exist for each run
-- ===================================================================
CREATE TABLE artifacts (
    artifact_id TEXT PRIMARY KEY,                   -- UUID
    run_id      TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN (
        'poincare_plot',        -- PNG, 4 phi slices 2x2
        'surface_mesh',         -- .vts (VTK structured)
        'coil_mesh',            -- .vtu (VTK unstructured)
        'boozer_surface_json',  -- BoozerSurface JSON (surface + coil objects)
        'results_json',         -- solver output
        'log_txt',              -- optimizer iteration log
        'cross_section_png',    -- CrossSection*.png
        'norm_plot_png',        -- NormPlot*.png
        'biot_savart_json',     -- biot_savart_opt.json / biot_savart_init.json
        'other'
    )),
    filename    TEXT NOT NULL,                       -- original filename
    storage_key TEXT NOT NULL,                       -- path within artifact store
    size_bytes  INTEGER,
    sha256      TEXT,                                -- content hash for dedup/integrity
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX idx_artifacts_run  ON artifacts(run_id);
CREATE INDEX idx_artifacts_kind ON artifacts(kind);

-- ===================================================================
-- Tags: lightweight labeling for runs (e.g. "pareto-frontier", "paper-fig3")
-- ===================================================================
CREATE TABLE tags (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    tag    TEXT NOT NULL,
    PRIMARY KEY (run_id, tag)
);

CREATE INDEX idx_tags_tag ON tags(tag);

-- ===================================================================
-- Useful views
-- ===================================================================

-- Full run view: joins runs + metrics + params for one-query access
CREATE VIEW v_full_run AS
SELECT
    r.*,
    m.max_volume_m3, m.iota_actual, m.iota_error, m.qs_error,
    m.boozer_residual, m.max_force_N, m.coil_length_m,
    m.max_curvature_m_inv, m.max_curvature_2nd_m_inv,
    m.coil_coil_dist_m, m.coil_surface_dist_m, m.plasma_vessel_dist_m,
    m.field_error, m.self_intersecting, m.volume_actual,
    p.mpol, p.ntor, p.order_param, p.maxiter,
    p.curvature_weight, p.cc_weight, p.res_weight, p.iotas_weight,
    p.iota_target, p.vol_target, p.alm_enabled
FROM runs r
LEFT JOIN metrics m ON r.run_id = m.run_id
LEFT JOIN params  p ON r.run_id = p.run_id;

-- Validated frontier: only Poincare-confirmed runs, ranked by volume
CREATE VIEW v_validated_frontier AS
SELECT
    r.run_id, r.contributor, r.equilibrium, r.solver, r.method,
    m.max_volume_m3, m.iota_actual, m.iota_error, m.qs_error,
    m.boozer_residual, m.coil_coil_dist_m, m.max_curvature_m_inv,
    m.coil_length_m, m.plasma_vessel_dist_m,
    p.mpol, p.order_param, p.curvature_weight,
    pc.survival_fraction, pc.phi_completeness
FROM runs r
JOIN metrics  m  ON r.run_id = m.run_id
JOIN poincare pc ON r.run_id = pc.run_id
JOIN params   p  ON r.run_id = p.run_id
WHERE r.validation_status = 'validated'
ORDER BY m.max_volume_m3 DESC;

-- Pending validation: runs that need Poincare analysis
CREATE VIEW v_needs_poincare AS
SELECT
    r.run_id, r.contributor, r.equilibrium, r.created_at,
    m.field_error, m.iota_actual, m.qs_error, m.max_curvature_m_inv,
    p.mpol, p.order_param
FROM runs r
JOIN metrics m ON r.run_id = m.run_id
JOIN params  p ON r.run_id = p.run_id
WHERE r.validation_status = 'pending'
  AND m.self_intersecting = 0
  AND m.field_error IS NOT NULL
ORDER BY m.field_error ASC;
