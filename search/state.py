"""SQLite state tracking for Stage 2 search campaigns."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from search.probe import ProbeResult


SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS campaigns (
    campaign_id TEXT PRIMARY KEY,
    created_at  REAL NOT NULL,
    solver_root TEXT NOT NULL,
    plasma_surf TEXT NOT NULL,
    config_json TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS candidates (
    candidate_id   TEXT PRIMARY KEY,
    campaign_id    TEXT NOT NULL REFERENCES campaigns,
    candidate_hash TEXT NOT NULL,
    parent_id      TEXT,
    params_json    TEXT NOT NULL,
    created_at     REAL NOT NULL,
    tier0_status   TEXT,
    tier1_status   TEXT,
    tier2_status   TEXT,
    combined_score REAL,
    decision       TEXT,
    description    TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    run_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id   TEXT NOT NULL REFERENCES candidates,
    tier           INTEGER NOT NULL,
    status         TEXT NOT NULL,
    combined_score REAL,
    metrics_json   TEXT,
    feedback       TEXT,
    failure_reason TEXT,
    run_dir        TEXT,
    elapsed_sec    REAL,
    created_at     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS frontier (
    campaign_id    TEXT NOT NULL,
    candidate_id   TEXT NOT NULL,
    combined_score REAL NOT NULL,
    field_error    REAL,
    recorded_at    REAL NOT NULL,
    PRIMARY KEY (campaign_id, candidate_id)
);

CREATE INDEX IF NOT EXISTS idx_candidates_campaign ON candidates(campaign_id);
CREATE INDEX IF NOT EXISTS idx_candidates_score ON candidates(combined_score DESC);
CREATE INDEX IF NOT EXISTS idx_runs_candidate ON runs(candidate_id);
CREATE INDEX IF NOT EXISTS idx_frontier_score ON frontier(combined_score DESC);
"""


class SearchDB:
    """SQLite database for search state. WAL mode, shared across modules."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), timeout=5.0)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA_SQL)

    def close(self) -> None:
        self._conn.close()

    # -- Campaigns --

    def create_campaign(
        self,
        campaign_id: str,
        solver_root: str,
        plasma_surf: str,
        config: dict[str, Any],
    ) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO campaigns (campaign_id, created_at, solver_root, plasma_surf, config_json, status) "
            "VALUES (?, ?, ?, ?, ?, 'active')",
            (campaign_id, time.time(), solver_root, plasma_surf, json.dumps(config)),
        )
        self._conn.commit()

    # -- Candidates --

    def insert_candidate(
        self,
        candidate_id: str,
        campaign_id: str,
        candidate_hash: str,
        parent_id: str | None,
        params: dict[str, Any],
        description: str = "",
    ) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO candidates "
            "(candidate_id, campaign_id, candidate_hash, parent_id, params_json, created_at, description) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                candidate_id,
                campaign_id,
                candidate_hash,
                parent_id,
                json.dumps(params),
                time.time(),
                description,
            ),
        )
        self._conn.commit()

    def update_candidate_tier(
        self,
        candidate_id: str,
        tier: int,
        status: str,
        score: float | None,
        decision: str | None = None,
    ) -> None:
        col = f"tier{tier}_status"
        updates = [f"{col} = ?"]
        values: list[Any] = [status]
        if score is not None:
            updates.append("combined_score = ?")
            values.append(score)
        if decision is not None:
            updates.append("decision = ?")
            values.append(decision)
        values.append(candidate_id)
        self._conn.execute(
            f"UPDATE candidates SET {', '.join(updates)} WHERE candidate_id = ?",
            values,
        )
        self._conn.commit()

    # -- Runs --

    def insert_run(self, result: ProbeResult) -> int:
        cursor = self._conn.execute(
            "INSERT INTO runs (candidate_id, tier, status, combined_score, metrics_json, feedback, failure_reason, run_dir, elapsed_sec, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                result.candidate_id,
                result.tier,
                result.status,
                result.combined_score,
                json.dumps(result.metrics),
                result.feedback,
                result.failure_reason,
                result.run_dir,
                result.elapsed_seconds,
                time.time(),
            ),
        )
        self._conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    # -- Frontier --

    def update_frontier(
        self,
        campaign_id: str,
        candidate_id: str,
        score: float,
        field_error: float | None,
    ) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO frontier (campaign_id, candidate_id, combined_score, field_error, recorded_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (campaign_id, candidate_id, score, field_error, time.time()),
        )
        self._conn.commit()

    def frontier_best(self, campaign_id: str) -> tuple[str | None, float]:
        """Return (candidate_id, best_score) for the campaign. (None, 0.0) if empty."""
        row = self._conn.execute(
            "SELECT candidate_id, combined_score FROM frontier WHERE campaign_id = ? ORDER BY combined_score DESC LIMIT 1",
            (campaign_id,),
        ).fetchone()
        if row is None:
            return None, 0.0
        return row["candidate_id"], row["combined_score"]

    # -- Queries --

    def list_candidates(self, campaign_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM candidates WHERE campaign_id = ? ORDER BY combined_score DESC NULLS LAST",
            (campaign_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM candidates WHERE candidate_id = ?", (candidate_id,)
        ).fetchone()
        return dict(row) if row else None

    def count_candidates(self, campaign_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM candidates WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()
        return row["cnt"]
