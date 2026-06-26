#!/usr/bin/env python3
import argparse
import contextlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from benchmark_common import available_blocks, repo_root, validate_safe_id

DEFAULT_RUN_ID = "vertexai-express"
DEFAULT_AGENT_PATH = "agent/vertexai_express_agent.py"
AGENT_SECRET_ENV_KEYS = (
    "EXPRESS_MODE_KEY",
)


def write_info(root, case_name, run_id, model_name, model_endpoint=""):
    result_dir = root / "result" / run_id / "block" / "repair" / case_name
    task_dir = root / "task" / run_id / "block" / "repair"
    temp_dir = root / "temp" / run_id / "block" / "repair" / case_name
    usage_dir = root / "usage" / run_id / "block" / "repair"
    result_dir.mkdir(parents=True, exist_ok=True)
    task_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    usage_dir.mkdir(parents=True, exist_ok=True)

    info = {
        "model_name": run_id,
        "model": model_name,
        "model_endpoint": model_endpoint,
        "case_name": case_name,
        "design_type": "block",
        "task_type": "repair",
        "path_to_layout_script": str(root / "testcase" / "asap7" / "block" / "layout_script" / f"{case_name}.py"),
        "path_to_layout_screenshot": str(root / "testcase" / "asap7" / "block" / "layout_screenshot" / case_name / f"{case_name}.png"),
        "path_to_drc_report": str(root / "testcase" / "asap7" / "block" / "drc_report" / f"{case_name}.drc.json"),
        "path_to_design_rule": str(root / "testcase" / "asap7" / "asap7.lydrc"),
        "path_to_connectivity_file": str(root / "testcase" / "asap7" / "block" / "connectivity" / f"{case_name}.json"),
        "output_path": str(result_dir / f"{case_name}_repaired.py"),
        "temp_dir": str(temp_dir),
        "usage_path": str(usage_dir / f"{case_name}_usage.json"),
    }

    info_path = task_dir / f"{case_name}_info.json"
    info_path.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    return info_path, Path(info["output_path"])


def update_info_endpoint(info_path, model_endpoint):
    with info_path.open(encoding="utf-8") as f:
        info = json.load(f)
    info["model_endpoint"] = model_endpoint
    info_path.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")


def read_info(info_path):
    with info_path.open(encoding="utf-8") as f:
        return json.load(f)


def run_agent(agent_path, info_path, model):
    cmd = [
        sys.executable,
        str(agent_path),
        str(info_path),
        "--model",
        model,
    ]
    env = os.environ.copy()
    for key in AGENT_SECRET_ENV_KEYS:
        env.pop(key, None)
    subprocess.run(cmd, check=True, env=env)


def find_free_port():
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_service(endpoint, process, timeout_seconds=20):
    deadline = time.monotonic() + timeout_seconds
    url = endpoint.rstrip("/") + "/health"
    last_error = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Model service exited with code {process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except Exception as exc:
            last_error = exc
            time.sleep(0.2)
    raise RuntimeError(f"Model service did not become ready: {last_error}")


@contextlib.contextmanager
def model_service(root, info_path, model, upstream_endpoint=""):
    info = read_info(info_path)
    port = find_free_port()
    endpoint = f"http://127.0.0.1:{port}"
    cmd = [
        sys.executable,
        str(root / "scripts" / "model_service.py"),
        "--port",
        str(port),
        "--model",
        model,
        "--run-id",
        info["model_name"],
        "--case-name",
        info["case_name"],
        "--design-type",
        info["design_type"],
        "--task-type",
        info["task_type"],
        "--usage-path",
        info["usage_path"],
    ]
    if upstream_endpoint:
        cmd.extend(["--upstream-endpoint", upstream_endpoint])
    env = os.environ.copy()
    process = subprocess.Popen(cmd, env=env)
    try:
        wait_for_service(endpoint, process)
        update_info_endpoint(info_path, endpoint)
        yield endpoint
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main():
    root = repo_root()
    blocks = available_blocks(root)
    parser = argparse.ArgumentParser(description="Run the ICLAD 2026 block repair benchmark")
    parser.add_argument("--case", choices=blocks, help="Run one available block. Defaults to all available blocks.")
    parser.add_argument("--model", default="gemini-3.5-flash")
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument(
        "--agent-path",
        default=DEFAULT_AGENT_PATH,
        help="Agent executable path relative to the repository root.",
    )
    parser.add_argument(
        "--upstream-endpoint",
        default="",
        help=(
            "Use an existing model endpoint as the upstream behind the benchmark "
            "wrapper. Agents still receive and call the local wrapper endpoint."
        ),
    )
    parser.add_argument(
        "--model-endpoint",
        default="",
        help=(
            "Deprecated alias for --upstream-endpoint. The endpoint is routed "
            "through the benchmark wrapper and is not written directly to info.json."
        ),
    )
    parser.add_argument(
        "--prepare-only",
        dest="prepare_only",
        action="store_true",
        help="Create per-case info.json without running an agent.",
    )
    args = parser.parse_args()

    if not blocks:
        raise SystemExit("No complete benchmark blocks found under testcase/asap7/block.")

    run_id = validate_safe_id(args.run_id, "run-id")
    if args.model_endpoint and args.upstream_endpoint and args.model_endpoint != args.upstream_endpoint:
        raise SystemExit("--model-endpoint and --upstream-endpoint cannot point to different URLs.")
    upstream_endpoint = args.upstream_endpoint or args.model_endpoint
    if args.model_endpoint:
        print(
            "[WARN] --model-endpoint is deprecated; treating it as --upstream-endpoint "
            "so calls still go through the benchmark wrapper."
        )
    agent_path = Path(args.agent_path)
    if not agent_path.is_absolute():
        agent_path = root / agent_path
    if not agent_path.is_file() and not args.prepare_only:
        raise SystemExit(f"Agent not found: {agent_path}")
    cases = [args.case] if args.case else blocks

    for case_name in cases:
        info_path, output_path = write_info(root, case_name, run_id, args.model)
        print(f"[INFO] Wrote case info: {info_path}")
        print(f"[INFO] Expected output: {output_path}")
        if not args.prepare_only:
            with model_service(root, info_path, args.model, upstream_endpoint) as endpoint:
                print(f"[INFO] Model endpoint: {endpoint}")
                if upstream_endpoint:
                    print(f"[INFO] Upstream endpoint: {upstream_endpoint}")
                run_agent(agent_path, info_path, args.model)


if __name__ == "__main__":
    main()
