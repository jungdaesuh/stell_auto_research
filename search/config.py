"""Campaign-level configuration for Stage 2 search."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CampaignConfig:
    """Immutable configuration for a Stage 2 search campaign."""

    # Solver workspace — root of the candidate-fixed SIMSOPT checkout
    solver_root: Path

    # Equilibrium surface file (basename under equilibria/)
    plasma_surf_filename: str = "wout_nfp22ginsburg_000_014417_iota15.nc"

    # Resolved paths (derived from solver_root)
    @property
    def solver_script(self) -> Path:
        return (
            self.solver_root
            / "examples"
            / "single_stage_optimization"
            / "STAGE_2"
            / "banana_coil_solver.py"
        )

    @property
    def equilibria_dir(self) -> Path:
        # Prefer DATABASE/EQUILIBRIA if it exists, else fall back to examples/
        db_dir = self.solver_root / "DATABASE" / "EQUILIBRIA"
        if db_dir.is_dir():
            return db_dir
        return (
            self.solver_root / "examples" / "single_stage_optimization" / "equilibria"
        )

    @property
    def equilibrium_path(self) -> Path:
        return self.equilibria_dir / self.plasma_surf_filename

    # Python executable for running the solver (None = sys.executable)
    python_executable: str | None = None

    # Output root for run directories
    output_root: Path = Path("runs")

    # SQLite database path
    db_path: Path = Path("search.sqlite")

    # Probe tier settings
    tier1_nphi: int = 63
    tier1_ntheta: int = 32
    tier2_nphi: int = 127
    tier2_ntheta: int = 32
    tier2_maxiter: int = 100
    tier2_timeout: int = 600

    # Thread control
    omp_num_threads: int = 4
    mkl_num_threads: int = 4

    def validate(self) -> list[str]:
        """Return a list of validation errors (empty = valid)."""
        errors: list[str] = []
        if not self.solver_script.is_file():
            errors.append(f"solver script not found: {self.solver_script}")
        if not self.equilibrium_path.is_file():
            errors.append(f"equilibrium file not found: {self.equilibrium_path}")
        return errors
