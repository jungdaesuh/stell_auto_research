"""Stage 2 candidate schema — validation, ranges, CLI arg generation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields, asdict
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Parameter ranges for Stage 2 banana coil search
# ---------------------------------------------------------------------------

PARAM_RANGES: dict[str, tuple[float, float]] = {
    "banana_surf_radius": (0.15, 0.30),
    "major_radius": (0.85, 1.00),
    "toroidal_flux": (0.15, 0.35),
    "order": (1, 5),
    "length_weight": (1e-6, 1e-2),
    "length_target": (1.0, 3.0),
    "cc_threshold": (0.02, 0.15),
    "cc_weight": (10.0, 1000.0),
    "curvature_weight": (1e-6, 0.01),
    "curvature_threshold": (10.0, 100.0),
    "theta_center": (0.2, 0.8),
    "phi_center": (0.01, 0.15),
    "theta_width": (0.03, 0.3),
    "phi_width": (0.01, 0.1),
}

INT_PARAMS = {"order"}


@dataclass(frozen=True)
class Stage2Candidate:
    """A single Stage 2 banana coil parameter configuration."""

    banana_surf_radius: float = 0.22
    major_radius: float = 0.915
    toroidal_flux: float = 0.24
    order: int = 2
    length_weight: float = 0.0005
    length_target: float = 1.75
    cc_threshold: float = 0.05
    cc_weight: float = 100.0
    curvature_weight: float = 0.0001
    curvature_threshold: float = 40.0
    theta_center: float = 0.5
    phi_center: float = 0.06
    theta_width: float = 0.1
    phi_width: float = 0.03

    # Metadata (not part of the physics config)
    parent_id: str | None = None

    @property
    def params_dict(self) -> dict[str, Any]:
        """Return only the physics parameters (excludes metadata)."""
        d = asdict(self)
        d.pop("parent_id", None)
        return d

    @property
    def candidate_hash(self) -> str:
        """Deterministic hash of physics parameters."""
        canonical = json.dumps(self.params_dict, sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    @property
    def candidate_id(self) -> str:
        return f"s2-{self.candidate_hash}"

    def validate(self) -> list[str]:
        """Return validation errors (empty = valid)."""
        errors: list[str] = []
        for name, (lo, hi) in PARAM_RANGES.items():
            val = getattr(self, name)
            if name in INT_PARAMS:
                if not isinstance(val, int):
                    errors.append(f"{name}: expected int, got {type(val).__name__}")
                    continue
            if val < lo or val > hi:
                errors.append(f"{name}={val} outside range [{lo}, {hi}]")
        return errors

    def to_cli_args(
        self,
        *,
        plasma_surf_filename: str | None = None,
        equilibria_dir: Path | None = None,
        equilibrium_path: Path | None = None,
        output_root: Path | None = None,
        nphi: int = 255,
        ntheta: int = 64,
        maxiter: int = 300,
        init_only: bool = False,
    ) -> list[str]:
        """Build CLI args for banana_coil_solver.py."""
        args = [
            "--banana-surf-radius",
            str(self.banana_surf_radius),
            "--major-radius",
            str(self.major_radius),
            "--toroidal-flux",
            str(self.toroidal_flux),
            "--order",
            str(self.order),
            "--length-weight",
            str(self.length_weight),
            "--length-target",
            str(self.length_target),
            "--cc-threshold",
            str(self.cc_threshold),
            "--cc-weight",
            str(self.cc_weight),
            "--curvature-weight",
            str(self.curvature_weight),
            "--curvature-threshold",
            str(self.curvature_threshold),
            "--theta-center",
            str(self.theta_center),
            "--phi-center",
            str(self.phi_center),
            "--theta-width",
            str(self.theta_width),
            "--phi-width",
            str(self.phi_width),
            "--nphi",
            str(nphi),
            "--ntheta",
            str(ntheta),
            "--maxiter",
            str(maxiter),
        ]
        if plasma_surf_filename is not None:
            args.extend(["--plasma-surf-filename", plasma_surf_filename])
        if equilibria_dir is not None:
            args.extend(["--equilibria-dir", str(equilibria_dir)])
        if equilibrium_path is not None:
            args.extend(["--equilibrium-path", str(equilibrium_path)])
        if output_root is not None:
            args.extend(["--output-root", str(output_root)])
        if init_only:
            args.append("--init-only")
        return args

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Stage2Candidate":
        """Create from a flat dict, ignoring unknown keys. Coerces int params."""
        known = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in d.items() if k in known}
        for name in INT_PARAMS:
            if name in filtered:
                filtered[name] = int(filtered[name])
        return cls(**filtered)

    @classmethod
    def baseline(cls) -> "Stage2Candidate":
        """The default baseline configuration."""
        return cls()
