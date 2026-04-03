#!/usr/bin/env python3
"""AWS EC2 runner for HBT autoresearch — launch, batch dispatch, download, stop.

Usage:
    # Launch an EC2 instance
    python scripts/aws_run.py launch

    # Run a single experiment remotely
    python scripts/aws_run.py run --cc-weight 44 --curvature-threshold 30

    # Run a batch of experiments in parallel
    python scripts/aws_run.py batch \
        "--cc-weight 44" \
        "--cc-weight 46" \
        "--cc-weight 48" \
        "--cc-weight 50"

    # Download mpol ramp results and logs from EC2
    python scripts/aws_run.py download

    # Check instance status
    python scripts/aws_run.py status

    # Stop and terminate instance
    python scripts/aws_run.py stop
"""

from __future__ import annotations

import datetime
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# AWS config
REGION = "us-east-1"
INSTANCE_TYPE = "c5ad.8xlarge"
KEY_NAME = "columbia-profile"
KEY_PATH = Path.home() / ".ssh" / "columbia-profile.pem"
SECURITY_GROUP = "sg-0e893f0974d736f2b"
AMI_ID = None  # Set after baking AMI — or use Ubuntu base

# Remote paths
REMOTE_USER = "ubuntu"
REMOTE_SIMSOPT = "/home/ubuntu/simsopt"
REMOTE_VENV = "/home/ubuntu/simsopt/.venv/bin/python"
REMOTE_EQUILIBRIA = "/home/ubuntu/equilibria"
REMOTE_SOLVER_STAGE2 = (
    f"{REMOTE_SIMSOPT}/examples/single_stage_optimization/STAGE_2/banana_coil_solver.py"
)
REMOTE_SOLVER_SS = f"{REMOTE_SIMSOPT}/examples/single_stage_optimization/SINGLE_STAGE/single_stage_banana_example.py"

# Local state
STATE_FILE = Path("/tmp/hbt_autoresearch/aws_instance.json")
COST_PER_HOUR = 1.38  # c5ad.8xlarge on-demand
COST_LIMIT = 50.0  # Auto-stop after this many dollars

# Parallel config: 32 vCPU (c5ad.8xlarge) — use all cores for one mpol=18 run
THREADS_PER_RUN = 30
MAX_PARALLEL = 1


def aws(*args: str) -> str:
    """Run an AWS CLI command and return stdout."""
    cmd = ["aws", "--region", REGION, "--output", "json"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"AWS error: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


def ssh(ip: str, command: str, timeout: int = 600) -> tuple[str, str, int]:
    """Run a command on the remote instance via SSH. Returns (stdout, stderr, returncode)."""
    cmd = [
        "ssh",
        "-i",
        str(KEY_PATH),
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "ConnectTimeout=10",
        f"{REMOTE_USER}@{ip}",
        command,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return result.stdout.strip(), result.stderr.strip(), result.returncode


def load_state() -> dict | None:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return None


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def get_ip() -> str:
    state = load_state()
    if not state:
        print(
            "No instance running. Run: python scripts/aws_run.py launch",
            file=sys.stderr,
        )
        sys.exit(1)

    # Check cost limit
    launched = state.get("launched_at", time.time())
    hours = (time.time() - launched) / 3600
    cost = hours * COST_PER_HOUR
    if cost >= COST_LIMIT:
        print(
            f"COST LIMIT REACHED: ${cost:.2f} ({hours:.1f}h x ${COST_PER_HOUR}/hr). "
            f"Limit is ${COST_LIMIT:.2f}. Stopping instance.",
            file=sys.stderr,
        )
        cmd_stop([])
        sys.exit(1)
    print(
        f"Cost so far: ${cost:.2f} / ${COST_LIMIT:.2f} ({hours:.1f}h)", file=sys.stderr
    )
    return state["ip"]


# -- Commands --


def cmd_launch(args: list[str]) -> None:
    """Launch an EC2 instance."""
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--type", default=INSTANCE_TYPE)
    p.add_argument("--ami", default=AMI_ID)
    opts = p.parse_args(args)

    if opts.ami is None:
        # Use latest Ubuntu 24.04 AMI
        ami_json = aws(
            "ec2",
            "describe-images",
            "--owners",
            "099720109477",
            "--filters",
            "Name=name,Values=ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*",
            "Name=state,Values=available",
            "--query",
            "sort_by(Images, &CreationDate)[-1].ImageId",
        )
        ami_id = json.loads(ami_json)
        print(f"Using Ubuntu 24.04 AMI: {ami_id}")
    else:
        ami_id = opts.ami
        print(f"Using custom AMI: {ami_id}")

    print(f"Launching {opts.type} in {REGION}...")
    result_json = aws(
        "ec2",
        "run-instances",
        "--image-id",
        ami_id,
        "--instance-type",
        opts.type,
        "--key-name",
        KEY_NAME,
        "--security-group-ids",
        SECURITY_GROUP,
        "--count",
        "1",
        "--block-device-mappings",
        json.dumps(
            [
                {
                    "DeviceName": "/dev/sda1",
                    "Ebs": {"VolumeSize": 50, "VolumeType": "gp3"},
                }
            ]
        ),
        "--tag-specifications",
        json.dumps(
            [
                {
                    "ResourceType": "instance",
                    "Tags": [{"Key": "Name", "Value": "hbt-autoresearch"}],
                }
            ]
        ),
    )
    result = json.loads(result_json)
    instance_id = result["Instances"][0]["InstanceId"]
    print(f"Instance: {instance_id}")

    # Wait for running
    print("Waiting for instance to start...", end="", flush=True)
    aws("ec2", "wait", "instance-running", "--instance-ids", instance_id)
    print(" running.")

    # Get public IP
    desc = json.loads(aws("ec2", "describe-instances", "--instance-ids", instance_id))
    ip = desc["Reservations"][0]["Instances"][0].get("PublicIpAddress")
    if not ip:
        print("No public IP assigned. Check VPC/subnet settings.", file=sys.stderr)
        sys.exit(1)

    save_state(
        {
            "instance_id": instance_id,
            "ip": ip,
            "type": opts.type,
            "launched_at": time.time(),
        }
    )
    print(f"IP: {ip}")

    # Wait for SSH
    print("Waiting for SSH...", end="", flush=True)
    for _ in range(30):
        try:
            out, _, _ = ssh(ip, "echo ok", timeout=10)
            if "ok" in out:
                break
        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
            pass
        time.sleep(5)
        print(".", end="", flush=True)
    print(" ready.")
    # Install idle watchdog: auto-shutdown if no python process for 30 min
    try:
        ssh(
            ip,
            "echo '#!/bin/bash\n"
            "# Auto-shutdown if no python solver process running for 30 min\n"
            "if ! pgrep -f 'single_stage_banana\\|banana_coil_solver' > /dev/null; then\n"
            "  if [ -f /tmp/idle_since ]; then\n"
            "    idle_start=$(cat /tmp/idle_since)\n"
            "    now=$(date +%s)\n"
            "    idle_sec=$((now - idle_start))\n"
            '    if [ "$idle_sec" -gt 1800 ]; then\n'
            '      echo "Idle for ${idle_sec}s, shutting down" >> /var/log/idle_watchdog.log\n'
            "      sudo shutdown -h now\n"
            "    fi\n"
            "  else\n"
            "    date +%s > /tmp/idle_since\n"
            "  fi\n"
            "else\n"
            "  rm -f /tmp/idle_since\n"
            "fi' > /tmp/idle_watchdog.sh && chmod +x /tmp/idle_watchdog.sh && "
            "(crontab -l 2>/dev/null; echo '*/5 * * * * /tmp/idle_watchdog.sh') | sort -u | crontab -",
            timeout=15,
        )
        print("Idle watchdog installed (auto-shutdown after 30 min idle).")
    except Exception:
        print("Warning: could not install idle watchdog.")

    print(f"\nSSH: ssh -i {KEY_PATH} {REMOTE_USER}@{ip}")
    print(f"Setup: ssh -i {KEY_PATH} {REMOTE_USER}@{ip} < scripts/setup_ami.sh")


def cmd_status(args: list[str]) -> None:
    """Check instance status."""
    state = load_state()
    if not state:
        print("No instance tracked.")
        return
    desc = json.loads(
        aws("ec2", "describe-instances", "--instance-ids", state["instance_id"])
    )
    inst = desc["Reservations"][0]["Instances"][0]
    print(f"Instance: {state['instance_id']}")
    print(f"Type: {state['type']}")
    print(f"IP: {state.get('ip', 'N/A')}")
    print(f"State: {inst['State']['Name']}")


def cmd_stop(args: list[str]) -> None:
    """Terminate the instance."""
    state = load_state()
    if not state:
        print("No instance tracked.")
        return
    print(f"Terminating {state['instance_id']}...")
    aws("ec2", "terminate-instances", "--instance-ids", state["instance_id"])
    STATE_FILE.unlink(missing_ok=True)
    print("Terminated.")


def cmd_download(args: list[str]) -> None:
    """Download mpol ramp results and logs from EC2 to local."""
    ip = get_ip()

    backup_dir = REPO_ROOT / "ec2_backups" / time.strftime("%Y%m%d_%H%M%S")
    backup_dir.mkdir(parents=True, exist_ok=True)

    remote_home = f"/home/{REMOTE_USER}"

    # Discover what's on the remote (match both mpol_ramp_results* and mpol_ramp_*_results*)
    listing, _, _ = ssh(
        ip,
        f"find {remote_home} -maxdepth 1 -name 'mpol_ramp*results*' -type d 2>/dev/null; "
        f"find {remote_home} -maxdepth 1 -name 'mpol_ramp*.log' -type f 2>/dev/null; "
        f"find {remote_home} -maxdepth 1 -name 'iota*_retry.log' -type f 2>/dev/null",
        timeout=15,
    )
    if not listing.strip():
        print("No ramp results found on EC2.")
        return

    print(f"Downloading to {backup_dir} ...")

    # Separate directories and files
    result_dirs = []
    log_files = []
    for line in listing.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        if line.endswith(".log"):
            log_files.append(line)
        else:
            result_dirs.append(line)

    # Download result directories
    for rdir in result_dirs:
        dirname = Path(rdir).name
        local_dest = backup_dir / dirname
        print(f"  {dirname} ...", end=" ", flush=True)
        rc = subprocess.run(
            [
                "scp", "-i", str(KEY_PATH), "-o", "StrictHostKeyChecking=no",
                "-r", f"{REMOTE_USER}@{ip}:{rdir}", str(local_dest),
            ],
            capture_output=True, text=True,
        ).returncode
        print("ok" if rc == 0 else "FAILED")

    # Download logs
    for lf in log_files:
        fname = Path(lf).name
        print(f"  {fname} ...", end=" ", flush=True)
        rc = subprocess.run(
            [
                "scp", "-i", str(KEY_PATH), "-o", "StrictHostKeyChecking=no",
                f"{REMOTE_USER}@{ip}:{lf}", str(backup_dir / fname),
            ],
            capture_output=True, text=True,
        ).returncode
        print("ok" if rc == 0 else "FAILED")

    # Show checkpoint summaries if available
    ckpt_files = list(backup_dir.rglob("checkpoints/*.json"))
    if ckpt_files:
        print("\nCheckpoint summary:")
        for cf in sorted(ckpt_files):
            cp = json.loads(cf.read_text())
            status = "FAIL" if cp.get("status") == "failed" else (
                "SI!" if cp.get("self_intersecting") else "ok"
            )
            fe = cp.get("field_error", "?")
            print(f"  mpol={cp['mpol']}: FE={fe} [{status}] @ {cp.get('completed_at', '?')}")

    total = sum(f.stat().st_size for f in backup_dir.rglob("*") if f.is_file())
    print(f"\nTotal downloaded: {total / 1024 / 1024:.1f} MB → {backup_dir}")


def cmd_run(args: list[str]) -> None:
    """Run a single experiment on the remote instance."""
    ip = get_ip()
    result = _run_remote(ip, " ".join(args))
    print(result)


def cmd_batch(args: list[str]) -> None:
    """Run multiple experiments in parallel on the remote instance."""
    ip = get_ip()
    if not args:
        print(
            'Usage: aws_run.py batch "--cc-weight 44" "--cc-weight 46" ...',
            file=sys.stderr,
        )
        sys.exit(1)

    n = min(len(args), MAX_PARALLEL)
    print(f"Dispatching {len(args)} experiments ({n} parallel) to {ip}...")

    results = []
    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = {
            pool.submit(_run_remote, ip, arg_str): i for i, arg_str in enumerate(args)
        }
        for future in as_completed(futures):
            idx = futures[future]
            try:
                result = future.result()
                results.append((idx, result))
                # Parse and show progress
                try:
                    r = json.loads(result)
                    fe = r.get("field_error", "?")
                    si = r.get("self_intersecting", "?")
                    sc = r.get("score", "?")
                    print(f"  [{idx + 1}/{len(args)}] FE={fe} SI={si} score={sc}")
                except json.JSONDecodeError:
                    print(f"  [{idx + 1}/{len(args)}] {result[:100]}")
            except Exception as exc:
                results.append(
                    (idx, json.dumps({"status": "crash", "feedback": str(exc)}))
                )
                print(f"  [{idx + 1}/{len(args)}] ERROR: {exc}")

    # Print all results in order
    results.sort(key=lambda x: x[0])
    print("\n--- Results ---")
    for idx, result in results:
        print(f"[{idx}] {args[idx]}")
        print(f"    {result}")


_JSONL_THREAD_LOCK = __import__("threading").Lock()

# Equilibrium registry: nfp{N}_iota{XX} -> wout filename
EQ_MAP = {}
for _nfp in (5, 10, 15):
    for _iota_int in range(10, 51):
        EQ_MAP[f"nfp{_nfp}_iota{_iota_int}"] = (
            f"wout_nfp{_nfp}ginsburg_desc_iota{_iota_int:02d}.nc"
        )
# Legacy aliases (NFP=5)
EQ_MAP.update({
    "iota15": "wout_nfp5ginsburg_000_014417_iota15.nc",
    "iota15p": "wout_nfp5ginsburg_desc_iota15.nc",
    "iota20": "wout_nfp5ginsburg_000_002084_iota20.nc",
    "iota20p": "wout_nfp5ginsburg_desc_iota20.nc",
    "001490": "wout_nfp5ginsburg_000_001490.nc",
})
for _i in range(15, 31):
    EQ_MAP.setdefault(f"iota{_i}", EQ_MAP[f"nfp5_iota{_i}"])

# Args consumed by _run_remote, not forwarded to the solver
_META_ARGS = {"--solver", "--equilibrium", "--timeout"}

_SAFE_ARG_PATTERN = re.compile(r"^[a-zA-Z0-9._/=\-]+$")


def _coerce_value(s: str) -> object:
    """Try int, then float, then return as string."""
    try:
        return int(s)
    except ValueError:
        try:
            return float(s)
        except ValueError:
            return s


def _parse_params_from_args(clean_args: str) -> dict:
    """Parse --flag value and --flag=value pairs into a params dict for crash records."""
    parts = clean_args.split()
    params: dict = {}
    i = 0
    while i < len(parts):
        token = parts[i]
        if token.startswith("--") and "=" in token:
            key, val_str = token[2:].split("=", 1)
            key = key.replace("-", "_")
            params[key] = _coerce_value(val_str)
            i += 1
        elif token.startswith("--") and i + 1 < len(parts):
            key = token[2:].replace("-", "_")
            params[key] = _coerce_value(parts[i + 1])
            i += 2
        else:
            i += 1
    return params


def _aws_error(
    solver: str, equilibrium: str, feedback: str,
    elapsed: float = 0.0, clean_args: str = "",
) -> dict:
    """Build a structured crash record matching run_one.py's _emit_error format."""
    return {
        "source": "aws",
        "solver": solver,
        "equilibrium": equilibrium,
        "status": "crash",
        "score": 0.0,
        "field_error": None,
        "self_intersecting": None,
        "max_curvature": None,
        "iterations": None,
        "elapsed": round(elapsed, 1),
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "feedback": feedback,
        "params": _parse_params_from_args(clean_args),
    }


def _parse_and_sanitize(extra_args: str) -> tuple[str, str, str, int, str]:
    """Parse meta-args from extra_args. Return (solver, equilibrium, plasma_surf, ssh_timeout, clean_args).

    Strips --solver, --equilibrium, --timeout from args before forwarding.
    Validates remaining args against shell injection.
    """
    parts = extra_args.split()
    solver = "stage2"
    equilibrium = "iota15"
    timeout_val = 0
    clean_parts: list[str] = []
    skip_next = False

    for i, token in enumerate(parts):
        if skip_next:
            skip_next = False
            continue
        # Handle --flag=value
        if "=" in token:
            flag = token.split("=", 1)[0]
            val = token.split("=", 1)[1]
            if flag == "--solver":
                solver = val
                continue
            elif flag == "--equilibrium":
                equilibrium = val
                continue
            elif flag == "--timeout":
                timeout_val = int(val)
                continue
        # Handle --flag value
        elif token in _META_ARGS and i + 1 < len(parts):
            if token == "--solver":
                solver = parts[i + 1]
            elif token == "--equilibrium":
                equilibrium = parts[i + 1]
            elif token == "--timeout":
                timeout_val = int(parts[i + 1])
            skip_next = True
            continue

        clean_parts.append(token)

    # Sanitize: reject args with shell metacharacters
    for part in clean_parts:
        if not _SAFE_ARG_PATTERN.match(part):
            raise ValueError(
                f"Unsafe argument rejected (shell metacharacter): {part!r}"
            )

    plasma_surf = EQ_MAP.get(equilibrium)
    if plasma_surf is None:
        raise ValueError(
            f"Unknown equilibrium '{equilibrium}'. "
            f"Use nfp{{N}}_iota{{XX}} (e.g. nfp5_iota17, nfp10_iota25) "
            f"or a legacy alias (iota15–iota30)."
        )
    ssh_timeout = (
        timeout_val + 120
        if timeout_val > 0
        else (2400 if solver == "single-stage" else 600)
    )
    clean_args = " ".join(clean_parts)

    return solver, equilibrium, plasma_surf, ssh_timeout, clean_args


def _run_remote(ip: str, extra_args: str) -> str:
    """Run one experiment on the remote instance. Returns JSON string.

    Delegates scoring to run_one.py's functions. Appends to local results.jsonl.
    """
    from scripts.run_one import score_stage2, score_single_stage, _append_jsonl  # noqa: E402

    import argparse
    import random

    solver, equilibrium, plasma_surf, ssh_timeout, clean_args = _parse_and_sanitize(
        extra_args
    )
    remote_solver = REMOTE_SOLVER_STAGE2 if solver == "stage2" else REMOTE_SOLVER_SS

    # Pre-check: for single-stage, run --init-only first to catch Boozer init failures.
    # Skip when explicit seed is provided (user knows what they're doing).
    has_explicit_seed = "--stage2-bs-path" in clean_args
    if solver == "single-stage" and not has_explicit_seed:
        precheck_id = (
            f"precheck_{int(time.time() * 1000)}_{random.randint(10000, 99999)}"
        )
        precheck_cmd = (
            f"export OMP_NUM_THREADS={THREADS_PER_RUN} && "
            f"mkdir -p /tmp/{precheck_id} && "
            f"{REMOTE_VENV} {remote_solver} "
            f"--equilibria-dir {REMOTE_EQUILIBRIA} "
            f"--plasma-surf-filename {plasma_surf} "
            f"--output-root /tmp/{precheck_id} "
            f"--init-only {clean_args} "
            f"> /tmp/{precheck_id}/precheck.log 2>&1; "
            f"echo EXIT:$?; "
            f"tail -5 /tmp/{precheck_id}/precheck.log 2>/dev/null; "
            f"rm -rf /tmp/{precheck_id}"
        )
        pre_out, pre_err, pre_rc = ssh(ip, precheck_cmd, timeout=180)
        if "EXIT:0" not in pre_out:
            feedback = "REMOTE BOOZER PRE-CHECK FAILED (saved ~10-30 min). "
            if "goes back" in pre_out or "self_intersecting" in pre_out.lower():
                feedback += "Surface folds — seed incompatible. Try a different seed."
            else:
                feedback += pre_out[-300:]
            error_output = _aws_error(solver, equilibrium, feedback, clean_args=clean_args)
            with _JSONL_THREAD_LOCK:
                _append_jsonl(error_output)
            return json.dumps(error_output)

    run_id = f"run_{int(time.time() * 1000)}_{random.randint(10000, 99999)}"
    remote_cmd = (
        f"export OMP_NUM_THREADS={THREADS_PER_RUN} && "
        f"export MKL_NUM_THREADS={THREADS_PER_RUN} && "
        f"export OPENBLAS_NUM_THREADS={THREADS_PER_RUN} && "
        f"mkdir -p /tmp/{run_id} && "
        f"{REMOTE_VENV} {remote_solver} "
        f"--equilibria-dir {REMOTE_EQUILIBRIA} "
        f"--plasma-surf-filename {plasma_surf} "
        f"--output-root /tmp/{run_id} "
        f"{clean_args} "
        f"> /tmp/{run_id}/run.log 2>&1; "
        f"cat $(find /tmp/{run_id} -name results.json -type f | head -1) 2>/dev/null; "
        f"rm -rf /tmp/{run_id}"
    )

    t0 = time.monotonic()
    try:
        raw, stderr, rc = ssh(ip, remote_cmd, timeout=ssh_timeout)
        elapsed = time.monotonic() - t0
        if rc != 0 and not raw:
            raw = stderr  # Use stderr for error diagnostics

        # Parse the JSON from output (results.json content, may be multi-line)
        # Use json.JSONDecoder to find the first valid JSON object, ignoring SSH noise
        raw_stripped = raw.strip()
        json_start = raw_stripped.find("{")
        if json_start >= 0:
            decoder = json.JSONDecoder()
            try:
                metrics, _ = decoder.raw_decode(raw_stripped, json_start)
            except json.JSONDecodeError:
                metrics = None
            if metrics is not None:
                # Build a minimal argparse.Namespace for scoring functions
                score_args = argparse.Namespace(
                    curvature_threshold=metrics.get("CURVATURE_THRESHOLD", 30.0),
                    iota_target=metrics.get("TARGET_IOTA", 0.15),
                    vol_target=metrics.get("TARGET_VOLUME", 0.10),
                )

                if solver == "stage2":
                    score = score_stage2(metrics, score_args)
                else:
                    score = score_single_stage(metrics, score_args)

                si = metrics.get("SELF_INTERSECTING", False)
                output: dict = {
                    "source": "aws",
                    "solver": solver,
                    "equilibrium": equilibrium,
                    "status": "fail" if si else "pass",
                    "score": round(score, 6),
                    "field_error": metrics.get("FIELD_ERROR"),
                    "self_intersecting": si,
                    "max_curvature": metrics.get("MAX_CURVATURE"),
                    "iterations": metrics.get("iterations"),
                    "elapsed": round(elapsed, 1),
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "params": {
                        k.lower(): v
                        for k, v in metrics.items()
                        if k
                        not in (
                            "FIELD_ERROR",
                            "SELF_INTERSECTING",
                            "MAX_CURVATURE",
                            "FINAL_VOLUME",
                            "FINAL_IOTA",
                            "INITIAL_VOLUME",
                            "INITIAL_IOTA",
                            "INITIAL_FIELD_ERROR",
                            "INITIAL_MAX_CURVATURE",
                            "PLASMA_SURF_PATH",
                            "STAGE2_BS_PATH",
                            "STAGE2_RESULTS_PATH",
                            "iterations",
                            "init_only",
                            "OBJECTIVE_J",
                            "CURVE_CURVE_MIN_DIST",
                            "NONQS_RATIO",
                            "BOOZER_RESIDUAL",
                            "TARGET_IOTA",
                            "TARGET_VOLUME",
                        )
                    },
                }

                output["objective_J"] = metrics.get("OBJECTIVE_J")
                output["curve_curve_min_dist"] = metrics.get("CURVE_CURVE_MIN_DIST")

                if solver == "single-stage":
                    output["final_iota"] = metrics.get("FINAL_IOTA")
                    output["final_volume"] = metrics.get("FINAL_VOLUME")
                    output["nonqs_ratio"] = metrics.get("NONQS_RATIO")
                    output["boozer_residual"] = metrics.get("BOOZER_RESIDUAL")
                    output["stage2_seed_path"] = metrics.get("STAGE2_BS_PATH")

                with _JSONL_THREAD_LOCK:
                    _append_jsonl(output)
                return json.dumps(output)

        error_output = _aws_error(
            solver, equilibrium, f"no JSON in output: {raw[-200:]}", elapsed, clean_args,
        )
        with _JSONL_THREAD_LOCK:
            _append_jsonl(error_output)
        return json.dumps(error_output)
    except subprocess.TimeoutExpired:
        error_output = _aws_error(
            solver, equilibrium, "timeout", time.monotonic() - t0, clean_args,
        )
        with _JSONL_THREAD_LOCK:
            _append_jsonl(error_output)
        return json.dumps(error_output)
    except Exception as exc:
        error_output = _aws_error(
            solver, equilibrium, str(exc), time.monotonic() - t0, clean_args,
        )
        with _JSONL_THREAD_LOCK:
            _append_jsonl(error_output)
        return json.dumps(error_output)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: aws_run.py <launch|status|stop|run|batch> [args]")
        sys.exit(1)

    command = sys.argv[1]
    rest = sys.argv[2:]

    commands = {
        "launch": cmd_launch,
        "status": cmd_status,
        "stop": cmd_stop,
        "run": cmd_run,
        "batch": cmd_batch,
        "download": cmd_download,
    }

    if command not in commands:
        print(f"Unknown command: {command}. Use: {', '.join(commands)}")
        sys.exit(1)

    commands[command](rest)


if __name__ == "__main__":
    main()
