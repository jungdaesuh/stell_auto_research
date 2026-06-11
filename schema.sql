PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS runs (
    -- identity
    id                      TEXT PRIMARY KEY,
    coil_type               TEXT NOT NULL,   -- solver family (adapter NAME)
    solver                  TEXT NOT NULL,   -- solver mode (--solver)
    equilibrium             TEXT NOT NULL,
    experiment_group        TEXT,            -- groups rows of one multi-step experiment; NULL = one row per experiment
    -- outcome
    status                  TEXT NOT NULL,
    status_reason           TEXT,
    validated               TEXT,
    iterations              INTEGER,
    elapsed                 REAL,
    created_at              TEXT DEFAULT (datetime('now')),
    optimizer_success       INTEGER,
    termination_message     TEXT,
    -- physics outputs (canonical metric keys with a dedicated column)
    field_error             REAL,
    qs_error                REAL,
    boozer_residual         REAL,
    iota_actual             REAL,
    volume_actual           REAL,
    max_curvature           REAL,
    coil_length             REAL,
    coil_coil_dist          REAL,
    coil_surface_dist       REAL,
    surface_vessel_dist     REAL,
    max_force               REAL,
    self_intersecting       INTEGER,
    objective_J             REAL,
    -- solver-specific metrics with no dedicated column (JSON, query with json_extract)
    metrics                 TEXT,
    -- optimizer inputs (JSON)
    params                  TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_coil_type    ON runs(coil_type);
CREATE INDEX IF NOT EXISTS idx_runs_equilibrium  ON runs(equilibrium);
CREATE INDEX IF NOT EXISTS idx_runs_group        ON runs(experiment_group);
CREATE INDEX IF NOT EXISTS idx_runs_status       ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_validated    ON runs(validated);
CREATE INDEX IF NOT EXISTS idx_runs_fe           ON runs(field_error);
CREATE INDEX IF NOT EXISTS idx_runs_qs           ON runs(qs_error);
