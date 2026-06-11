"""Regression tests for the harness core and the banana adapter's config.

Stdlib unittest only (the harness has no test-framework dependency).
Run from the repo root with:  python3 -m unittest discover -s tests -t .

Both run.py and the banana adapter read configuration at import time, so the
required env vars and the import-time override cases are set once in
setUpModule before the single import. Behavior cases (_finalize_run_dir,
_build_record) mutate module globals / call functions per-test.
"""

from __future__ import annotations

import argparse
import os
import shutil
import tempfile
import unittest
from pathlib import Path

_IMPORT_ENV = {
    # Required by the banana adapter at import.
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
    global run, banana, contract
    import run  # noqa: PLC0415 — import must follow env setup
    import contract  # noqa: PLC0415
    from adapters import simsopt_banana as banana  # noqa: PLC0415


class TestAdapterEnvOverrides(unittest.TestCase):
    """Env vars override the banana adapter's script/seed paths at import time."""

    def test_stage2_script_override(self):
        self.assertEqual(banana.SCRIPTS["stage2"], "custom/stage2.py")

    def test_single_stage_script_default(self):
        self.assertEqual(
            banana.SCRIPTS["single-stage"],
            "examples/single_stage_optimization/SINGLE_STAGE/single_stage_banana_example.py",
        )

    def test_seed_store_override(self):
        self.assertEqual(banana.STAGE2_SEED_STORE, Path("/tmp/test_seed_dir"))


class TestCoreEnvOverrides(unittest.TestCase):
    """Env vars override the core's directory defaults at import time."""

    def test_output_base_override(self):
        self.assertEqual(run.OUTPUT_BASE, Path("/tmp/test_output_base"))

    def test_artifacts_dir_default(self):
        self.assertEqual(run.ARTIFACTS_DIR, run.REPO_ROOT / "artifacts")

    def test_invalid_keep_artifacts_falls_back_to_none(self):
        self.assertEqual(run.KEEP_ARTIFACTS, "none")


class TestBuildRecord(unittest.TestCase):
    """_build_record projects canonical metrics to columns + a metrics overflow."""

    def _args(self) -> argparse.Namespace:
        return argparse.Namespace(solver="single-stage", equilibrium="nfp5_iota15")

    def test_column_metric_projected_to_top_level(self):
        outcome = contract.ExperimentOutcome(
            "pass", "ok", metrics={"field_error": 0.01, "iota_actual": 0.15}
        )
        record = run._build_record(self._args(), outcome, elapsed=1.234)
        self.assertEqual(record["field_error"], 0.01)
        self.assertEqual(record["iota_actual"], 0.15)
        self.assertEqual(record["coil_type"], run.adapter.NAME)
        self.assertEqual(record["solver"], "single-stage")

    def test_unknown_metric_goes_to_overflow(self):
        outcome = contract.ExperimentOutcome(
            "pass", "ok", metrics={"field_error": 0.01, "lead_end_curvature": 12.0}
        )
        record = run._build_record(self._args(), outcome, elapsed=0.0)
        self.assertEqual(record["metrics"], {"lead_end_curvature": 12.0})
        self.assertNotIn("lead_end_curvature", {k: v for k, v in record.items() if k != "metrics"})

    def test_nan_metric_cleaned_to_none(self):
        outcome = contract.ExperimentOutcome(
            "fail", "incomplete_metrics", metrics={"field_error": float("nan")}
        )
        record = run._build_record(self._args(), outcome, elapsed=0.0)
        self.assertIsNone(record["field_error"])

    def test_validated_and_group_carried_through(self):
        outcome = contract.ExperimentOutcome(
            "pass", "ok", validated="pass", experiment_group="exp-7"
        )
        record = run._build_record(self._args(), outcome, elapsed=0.0)
        self.assertEqual(record["validated"], "pass")
        self.assertEqual(record["experiment_group"], "exp-7")


class TestAdapterErrorPath(unittest.TestCase):
    """An adapter that raises is recorded as a crash with reproducible params."""

    def test_unexpected_adapter_error_records_parsed_cli(self):
        args = argparse.Namespace(solver="stage2", equilibrium="nfp5_iota15", cc_weight=7.0)
        captured: dict = {}

        scratch = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, scratch, True)
        self._patch(run, "OUTPUT_BASE", scratch)
        self._patch(run, "_emit_result", lambda rec: captured.update(rec))
        self._patch(run, "_finalize_run_dir", lambda *a, **k: None)

        def boom(_args, _run_dir):
            raise RuntimeError("solver blew up")

        self._patch(run.adapter, "run_experiment", boom)

        run._run_experiment(args)

        self.assertEqual(captured["status"], "crash")
        self.assertTrue(captured["status_reason"].startswith("adapter_error"))
        self.assertEqual(captured["params"]["cc_weight"], 7.0)
        self.assertEqual(captured["params"]["equilibrium"], "nfp5_iota15")

    def _patch(self, obj, name, value):
        original = getattr(obj, name)
        self.addCleanup(setattr, obj, name, original)
        setattr(obj, name, value)


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


class TestClassify(unittest.TestCase):
    """The banana adapter classifies canonical metrics into pass/fail."""

    def test_self_intersecting_fails(self):
        status, reason = banana._classify({"self_intersecting": True}, "stage2")
        self.assertEqual((status, reason), ("fail", "self_intersecting"))

    def test_missing_required_metric_fails(self):
        status, reason = banana._classify({"field_error": 0.01}, "stage2")
        self.assertEqual((status, reason), ("fail", "incomplete_metrics"))

    def test_complete_stage2_passes(self):
        status, reason = banana._classify(
            {"field_error": 0.01, "max_curvature": 40.0}, "stage2"
        )
        self.assertEqual((status, reason), ("pass", "ok"))

    def test_single_stage_requires_iota_and_volume(self):
        status, _ = banana._classify(
            {"field_error": 0.01, "max_curvature": 40.0}, "single-stage"
        )
        self.assertEqual(status, "fail")


if __name__ == "__main__":
    unittest.main()
