"""Regression tests for run.py's env-driven configuration and artifact retention.

Stdlib unittest only (the harness has no test-framework dependency).
Run from the repo root with:  python3 -m unittest discover -s tests -t .

run.py reads its configuration at import time, so the required env vars and
the import-time override cases are set once in setUpModule before the single
import. Behavior cases (_finalize_run_dir) mutate module globals per-test.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

_IMPORT_ENV = {
    # Required by run.py at import.
    "SIMSOPT_ROOT": tempfile.gettempdir(),
    "SIMSOPT_PYTHON": "/usr/bin/python3",
    "EQUILIBRIA_DIR": tempfile.gettempdir(),
    # Import-time override cases under test.
    "STAGE2_SCRIPT": "custom/stage2.py",
    "OUTPUT_BASE": "/tmp/test_output_base",
    "STAGE2_SEED_DIR": "/tmp/test_seed_dir",
    "KEEP_ARTIFACTS": "bogus-value",  # must fall back to "none"
}


def setUpModule() -> None:
    os.environ.update(_IMPORT_ENV)
    global run
    import run  # noqa: PLC0415 — import must follow env setup


class TestEnvOverrides(unittest.TestCase):
    """Env vars override directory/script defaults at import time."""

    def test_stage2_script_override(self):
        self.assertEqual(run.SOLVERS["banana"]["stage2"], "custom/stage2.py")

    def test_single_stage_script_default(self):
        self.assertEqual(
            run.SOLVERS["banana"]["single-stage"],
            "examples/single_stage_optimization/SINGLE_STAGE/single_stage_banana_example.py",
        )

    def test_output_base_override(self):
        self.assertEqual(run.OUTPUT_BASE, Path("/tmp/test_output_base"))

    def test_seed_store_override(self):
        self.assertEqual(run.STAGE2_SEED_STORE, Path("/tmp/test_seed_dir"))

    def test_artifacts_dir_default(self):
        self.assertEqual(run.ARTIFACTS_DIR, run.REPO_ROOT / "artifacts")

    def test_invalid_keep_artifacts_falls_back_to_none(self):
        self.assertEqual(run.KEEP_ARTIFACTS, "none")


class TestFinalizeRunDir(unittest.TestCase):
    """_finalize_run_dir keeps or discards the run dir per KEEP_ARTIFACTS."""

    def setUp(self):
        self._saved = (run.KEEP_ARTIFACTS, run.ARTIFACTS_DIR)
        self.artifacts = Path(tempfile.mkdtemp()) / "artifacts"
        run.ARTIFACTS_DIR = self.artifacts

    def tearDown(self):
        run.KEEP_ARTIFACTS, run.ARTIFACTS_DIR = self._saved
        shutil.rmtree(self.artifacts.parent, ignore_errors=True)

    def _run_dir(self) -> Path:
        d = Path(tempfile.mkdtemp())
        (d / "results.json").write_text("{}")
        self.addCleanup(shutil.rmtree, d, True)
        return d

    def test_pass_policy_keeps_passing_run(self):
        run.KEEP_ARTIFACTS = "pass"
        src = self._run_dir()
        run._finalize_run_dir(src, "pass", "id-pass-1")
        kept = self.artifacts / "id-pass-1" / "results.json"
        self.assertTrue(kept.exists(), f"passing run not kept at {kept}")
        self.assertFalse(src.exists(), "run dir should be moved, not copied")

    def test_pass_policy_discards_failing_run(self):
        run.KEEP_ARTIFACTS = "pass"
        src = self._run_dir()
        run._finalize_run_dir(src, "fail", "id-fail-1")
        self.assertFalse(src.exists(), "failing run should be discarded")
        self.assertFalse((self.artifacts / "id-fail-1").exists())

    def test_none_policy_discards_passing_run(self):
        run.KEEP_ARTIFACTS = "none"
        src = self._run_dir()
        run._finalize_run_dir(src, "pass", "id-pass-2")
        self.assertFalse(src.exists())
        self.assertFalse(self.artifacts.exists(), "no artifacts dir under 'none'")

    def test_all_policy_keeps_failing_run(self):
        run.KEEP_ARTIFACTS = "all"
        src = self._run_dir()
        run._finalize_run_dir(src, "fail", "id-fail-2")
        self.assertTrue((self.artifacts / "id-fail-2" / "results.json").exists())


if __name__ == "__main__":
    unittest.main()
