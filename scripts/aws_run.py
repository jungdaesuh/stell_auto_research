#!/usr/bin/env python3
"""AWS EC2 runner for HBT autoresearch — launch, batch dispatch, stop.

Usage:
    # Launch a c7i.16xlarge instance (64 vCPU)
    python scripts/aws_run.py launch

    # Run a single experiment remotely
    python scripts/aws_run.py run --cc-weight 44 --curvature-threshold 30

    # Run a batch of experiments in parallel
    python scripts/aws_run.py batch \
        "--cc-weight 44" \
        "--cc-weight 46" \
        "--cc-weight 48" \
        "--cc-weight 50"

    # Check instance status
    python scripts/aws_run.py status

    # Stop and terminate instance
    python scripts/aws_run.py stop
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# AWS config
REGION = "us-east-1"
INSTANCE_TYPE = "c5ad.4xlarge"
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
COST_PER_HOUR = 0.69  # c5ad.4xlarge on-demand
COST_LIMIT = 10.0  # Auto-stop after this many dollars

# Parallel config: 8 threads per run on 64 vCPU = 8 parallel runs
THREADS_PER_RUN = 8
MAX_PARALLEL = 2


def aws(*args: str) -> str:
    """Run an AWS CLI command and return stdout."""
    cmd = ["aws", "--region", REGION, "--output", "json"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"AWS error: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


def ssh(ip: str, command: str, timeout: int = 600) -> str:
    """Run a command on the remote instance via SSH."""
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
    return result.stdout.strip()


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
            out = ssh(ip, "echo ok", timeout=10)
            if "ok" in out:
                break
        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
            pass
        time.sleep(5)
        print(".", end="", flush=True)
    print(" ready.")
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


def _run_remote(ip: str, extra_args: str) -> str:
    """Run one experiment on the remote instance. Returns JSON string.

    Delegates scoring to run_one.py's functions. Appends to local results.jsonl.
    """
    from scripts.run_one import score_stage2, score_single_stage, _append_jsonl  # noqa: E402

    import argparse

    # Parse --solver and --equilibrium from the args
    solver = "stage2"
    if "--solver single-stage" in extra_args or "--solver=single-stage" in extra_args:
        solver = "single-stage"

    equilibrium = "iota15"
    for token in extra_args.split():
        if token.startswith("--equilibrium"):
            if "=" in token:
                equilibrium = token.split("=", 1)[1]
    parts = extra_args.split()
    for i, p in enumerate(parts):
        if p == "--equilibrium" and i + 1 < len(parts):
            equilibrium = parts[i + 1]

    remote_solver = REMOTE_SOLVER_STAGE2 if solver == "stage2" else REMOTE_SOLVER_SS

    # Resolve equilibrium → plasma surf filename
    eq_map = {
        "iota15": "wout_nfp22ginsburg_000_014417_iota15.nc",
        "iota20": "wout_nfp22ginsburg_000_002084_iota20.nc",
        "001490": "wout_nfp22ginsburg_000_001490.nc",
    }
    plasma_surf = eq_map.get(equilibrium, equilibrium)

    # Unique run ID using pid + time to avoid collisions across parallel SSH sessions
    import random

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
        f"{extra_args} "
        f"> /tmp/{run_id}/run.log 2>&1; "
        f"cat $(find /tmp/{run_id} -name results.json -type f | head -1) 2>/dev/null; "
        f"rm -rf /tmp/{run_id}"
    )

    # Parse --timeout from extra_args, default 600 for stage2, 2400 for single-stage
    ssh_timeout = 2400 if solver == "single-stage" else 600
    for i, token in enumerate(extra_args.split()):
        if token == "--timeout" and i + 1 < len(extra_args.split()):
            ssh_timeout = (
                int(extra_args.split()[i + 1]) + 120
            )  # solver timeout + buffer

    t0 = time.monotonic()
    try:
        raw = ssh(ip, remote_cmd, timeout=ssh_timeout)
        elapsed = time.monotonic() - t0

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
                    "params": {
                        k: v
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
                        )
                    },
                }

                if solver == "single-stage":
                    output["final_iota"] = metrics.get("FINAL_IOTA")
                    output["final_volume"] = metrics.get("FINAL_VOLUME")
                    output["target_iota"] = metrics.get("TARGET_IOTA")
                    output["target_volume"] = metrics.get("TARGET_VOLUME")

                _append_jsonl(output)
                return json.dumps(output)

        error_output: dict = {
            "source": "aws",
            "solver": solver,
            "equilibrium": equilibrium,
            "status": "crash",
            "score": 0.0,
            "feedback": f"no JSON in output: {raw[-200:]}",
        }
        _append_jsonl(error_output)
        return json.dumps(error_output)
    except subprocess.TimeoutExpired:
        error_output = {
            "source": "aws",
            "solver": solver,
            "equilibrium": equilibrium,
            "status": "crash",
            "score": 0.0,
            "feedback": "timeout",
        }
        _append_jsonl(error_output)
        return json.dumps(error_output)
    except Exception as exc:
        error_output = {
            "source": "aws",
            "solver": solver,
            "equilibrium": equilibrium,
            "status": "crash",
            "score": 0.0,
            "feedback": str(exc),
        }
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
    }

    if command not in commands:
        print(f"Unknown command: {command}. Use: {', '.join(commands)}")
        sys.exit(1)

    commands[command](rest)


if __name__ == "__main__":
    main()
