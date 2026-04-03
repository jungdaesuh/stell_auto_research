#!/usr/bin/env python3
"""Generate equilibrium seed files for the autoresearch pipeline.

Produces wout_nfp{N}ginsburg_desc_iota{XX}.nc files for:
  - NFP = 5, 10, 15
  - iota = 0.10 to 0.50 in steps of 0.01

NFP=5: reuses the existing ginsburg boundary shape.
NFP=10, 15: uses a simple rotating-ellipse boundary as a starting point.

Usage:
    python scripts/generate_equilibria.py --output-dir /path/to/output
    python scripts/generate_equilibria.py --nfp 5 --iota-min 0.10 --iota-max 0.50
    python scripts/generate_equilibria.py --nfp 10 --iota 0.25   # single equilibrium
"""

import argparse
import os
import sys
import warnings

import numpy as np

warnings.filterwarnings("ignore")

# DESC must be importable
sys.path.insert(0, os.path.expanduser("~/code/opensource/DESC"))

from desc.continuation import solve_continuation_automatic
from desc.equilibrium import Equilibrium
from desc.geometry import FourierRZToroidalSurface
from desc.profiles import PowerSeriesProfile
from desc.vmec import VMECIO

# ---------------------------------------------------------------------------
# Existing ginsburg boundary (NFP=5) — loaded from a reference file
# ---------------------------------------------------------------------------
GINSBURG_REF = os.path.expanduser(
    "~/code/columbia/DATABASE/EQUILIBRIA/wout_nfp5ginsburg_desc_iota20.nc"
)

# ---------------------------------------------------------------------------
# All NFP values reuse the ginsburg boundary shape.
# The banana coils have 5-fold symmetry, so NFP=5/10/15 correspond to
# 1/2/3 field periods per coil section. The boundary Fourier coefficients
# are kept identical — only NFP is changed. The optimization pipeline
# will reshape the plasma from these seeds.
# ---------------------------------------------------------------------------


def load_ginsburg_boundary():
    """Load the ginsburg NFP=5 boundary from the reference equilibrium."""
    if not os.path.exists(GINSBURG_REF):
        raise FileNotFoundError(
            f"Reference equilibrium not found: {GINSBURG_REF}\n"
            "Ensure the ginsburg equilibria are in the DATABASE."
        )
    ref_eq = VMECIO.load(GINSBURG_REF)
    return ref_eq.surface, ref_eq.Psi


def make_surface_at_nfp(reference_surface, target_nfp):
    """Create a boundary surface at a different NFP using the ginsburg shape.

    Copies the Fourier coefficients from the reference (NFP=5) surface
    and constructs a new surface with the target NFP. The physical
    interpretation: same local cross-section shape, but with more
    field periods around the torus.
    """
    if target_nfp == reference_surface.NFP:
        return reference_surface.copy()

    surf = FourierRZToroidalSurface(
        R_lmn=reference_surface.R_lmn.copy(),
        modes_R=reference_surface.R_basis.modes[:, 1:3].astype(int).tolist(),
        Z_lmn=reference_surface.Z_lmn.copy(),
        modes_Z=reference_surface.Z_basis.modes[:, 1:3].astype(int).tolist(),
        NFP=target_nfp,
        sym=reference_surface.sym,
    )
    return surf


def generate_single(nfp, iota_target, surface, psi, M=6, N=6, verbose=1):
    """Generate a single equilibrium at the given NFP and iota.

    The iota profile is set as a flat (constant) profile at -|iota_target|
    to match the existing ginsburg sign convention (negative iota).
    """
    iota_val = -abs(iota_target)
    pressure = PowerSeriesProfile([0.0])  # vacuum
    iota = PowerSeriesProfile([iota_val])  # flat iota profile

    eq = Equilibrium(
        M=M,
        N=N,
        surface=surface.copy(),
        pressure=pressure,
        iota=iota,
        Psi=psi,
        NFP=nfp,
        sym=True,
    )

    if verbose >= 1:
        print(f"  Solving NFP={nfp} iota={iota_target:.2f} (M={M}, N={N})...")

    try:
        eqf = solve_continuation_automatic(eq, verbose=max(0, verbose - 1))
        solved_eq = eqf[-1]

        # Verify the solved iota is close to target
        actual_iota = abs(solved_eq.compute("iota")["iota"].mean())
        if abs(actual_iota - abs(iota_target)) > 0.05:
            print(
                f"  WARNING: solved iota={actual_iota:.4f} "
                f"differs from target {iota_target:.2f}"
            )
        return solved_eq
    except Exception as e:
        print(f"  FAILED: {e}")
        return None


def output_filename(nfp, iota_target):
    """Generate the output filename following the ginsburg convention."""
    iota_int = int(round(iota_target * 100))
    return f"wout_nfp{nfp}ginsburg_desc_iota{iota_int:02d}.nc"


def main():
    parser = argparse.ArgumentParser(
        description="Generate stellarator equilibrium seeds."
    )
    parser.add_argument(
        "--output-dir",
        default=os.path.expanduser(
            "~/code/columbia/DATABASE/EQUILIBRIA"
        ),
        help="Directory to write wout files.",
    )
    parser.add_argument(
        "--nfp",
        type=int,
        nargs="+",
        default=[5, 10, 15],
        help="NFP values to generate (default: 5 10 15).",
    )
    parser.add_argument(
        "--iota-min",
        type=float,
        default=0.10,
        help="Minimum iota target (default: 0.10).",
    )
    parser.add_argument(
        "--iota-max",
        type=float,
        default=0.50,
        help="Maximum iota target (default: 0.50).",
    )
    parser.add_argument(
        "--iota-step",
        type=float,
        default=0.01,
        help="Iota step size (default: 0.01).",
    )
    parser.add_argument(
        "--iota",
        type=float,
        nargs="+",
        default=None,
        help="Explicit iota values (overrides min/max/step).",
    )
    parser.add_argument(
        "-M", type=int, default=6, help="Poloidal resolution (default: 6)."
    )
    parser.add_argument(
        "-N", type=int, default=6, help="Toroidal resolution (default: 6)."
    )
    parser.add_argument(
        "--verbose", "-v", type=int, default=1, help="Verbosity level."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print what would be generated."
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip if output file already exists.",
    )
    args = parser.parse_args()

    # Build iota list
    if args.iota is not None:
        iota_targets = args.iota
    else:
        iota_targets = np.arange(
            args.iota_min, args.iota_max + args.iota_step / 2, args.iota_step
        )
        iota_targets = [round(x, 2) for x in iota_targets]

    os.makedirs(args.output_dir, exist_ok=True)

    # Load ginsburg boundary once — reused for all NFP values
    print("Loading ginsburg NFP=5 boundary...")
    ginsburg_surface, ginsburg_psi = load_ginsburg_boundary()

    total = len(args.nfp) * len(iota_targets)
    generated = 0
    skipped = 0
    failed = 0

    print(f"Generating {total} equilibria: NFP={args.nfp}, "
          f"iota={iota_targets[0]:.2f}–{iota_targets[-1]:.2f}")
    print(f"Output: {args.output_dir}")
    print()

    for nfp in args.nfp:
        # Ginsburg boundary at the target NFP
        surface = make_surface_at_nfp(ginsburg_surface, nfp)
        psi = ginsburg_psi
        print(f"--- NFP={nfp} (ginsburg boundary) ---")

        for iota_target in iota_targets:
            fname = output_filename(nfp, iota_target)
            fpath = os.path.join(args.output_dir, fname)

            if args.skip_existing and os.path.exists(fpath):
                skipped += 1
                if args.verbose >= 1:
                    print(f"  SKIP (exists): {fname}")
                continue

            if args.dry_run:
                print(f"  WOULD GENERATE: {fname}")
                continue

            eq = generate_single(
                nfp, iota_target, surface, psi,
                M=args.M, N=args.N, verbose=args.verbose,
            )
            if eq is not None:
                VMECIO.save(eq, fpath, verbose=0)
                generated += 1
                if args.verbose >= 1:
                    print(f"  SAVED: {fname}")
            else:
                failed += 1

    print()
    print(f"Done. Generated: {generated}, Skipped: {skipped}, Failed: {failed}")


if __name__ == "__main__":
    main()
