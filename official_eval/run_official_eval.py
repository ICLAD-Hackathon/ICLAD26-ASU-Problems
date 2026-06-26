#!/usr/bin/env python3
import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from benchmark_common import available_blocks, repo_root, validate_safe_id

DEFAULT_MODEL = "gemini-3.5-flash"
DEFAULT_AGENT_PATH = "agent/vertexai_express_agent.py"
DEFAULT_IMAGE = "iclad26-asu-official:latest"
DEFAULT_SUBMISSION_AGENT = "agent.py"
WRAPPER_NAME = "model-wrapper"
WRAPPER_PORT = "8080"


def run(cmd, **kwargs):
    print(f"[OFFICIAL] {' '.join(str(part) for part in cmd)}", flush=True)
    return subprocess.run(cmd, check=True, **kwargs)


def docker(*args, **kwargs):
    return run(["docker", *args], **kwargs)


def docker_quiet(*args):
    return subprocess.run(["docker", *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def docker_mount(host_path, container_path=None, mode="ro"):
    container_path = container_path or host_path
    return ["-v", f"{host_path}:{container_path}:{mode}"]


def container_name(role, run_id, case_name):
    return f"iclad-{role}-{run_id}-{case_name}"


def require_docker():
    if shutil.which("docker") is None:
        raise SystemExit("docker was not found on PATH.")
    docker("version")


def require_model_key():
    if not os.environ.get("EXPRESS_MODE_KEY"):
        raise SystemExit("EXPRESS_MODE_KEY is not set in the organizer shell.")


def build_image(root, image):
    docker("build", "-f", str(root / "official_eval" / "Dockerfile"), "-t", image, str(root))


def build_submission_image(base_image, submission_dir, image):
    requirements = submission_dir / "requirements.txt"
    if not requirements.is_file():
        return base_image

    with tempfile.TemporaryDirectory(prefix="iclad-submission-build-") as tmp:
        dockerfile = Path(tmp) / "Dockerfile"
        dockerfile.write_text(
            "\n".join([
                f"FROM {base_image}",
                "COPY requirements.txt /tmp/submission-requirements.txt",
                "RUN python3 -m pip install --no-cache-dir -r /tmp/submission-requirements.txt",
                "",
            ]),
            encoding="utf-8",
        )
        docker("build", "-f", str(dockerfile), "-t", image, str(submission_dir))

    return image


def write_info(root, run_id, case_name, model, endpoint, usage_path):
    block_dir = root / "testcase" / "asap7" / "block"
    result_dir = root / "result" / run_id / "block" / "repair" / case_name
    task_dir = root / "task" / run_id / "block" / "repair"
    temp_dir = root / "temp" / run_id / "block" / "repair" / case_name
    result_dir.mkdir(parents=True, exist_ok=True)
    task_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    info = {
        "model_name": run_id,
        "model": model,
        "model_endpoint": endpoint,
        "case_name": case_name,
        "design_type": "block",
        "task_type": "repair",
        "path_to_layout_script": str(block_dir / "layout_script" / f"{case_name}.py"),
        "path_to_layout_screenshot": str(block_dir / "layout_screenshot" / case_name / f"{case_name}.png"),
        "path_to_drc_report": str(block_dir / "drc_report" / f"{case_name}.drc.json"),
        "path_to_design_rule": str(root / "testcase" / "asap7" / "asap7.lydrc"),
        "path_to_connectivity_file": str(block_dir / "connectivity" / f"{case_name}.json"),
        "output_path": str(result_dir / f"{case_name}_repaired.py"),
        "temp_dir": str(temp_dir),
        "usage_path": usage_path,
    }

    info_path = task_dir / f"{case_name}_info.json"
    info_path.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    return info_path


def wait_for_wrapper(container):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        result = subprocess.run(
            [
                "docker",
                "exec",
                container,
                "python3",
                "-c",
                (
                    "import urllib.request; "
                    f"urllib.request.urlopen('http://127.0.0.1:{WRAPPER_PORT}/health', timeout=1)"
                ),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            return
        time.sleep(0.5)
    logs = subprocess.run(
        ["docker", "logs", container],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    ).stdout
    if logs:
        print(f"[OFFICIAL] Wrapper logs for {container}:\n{logs}", file=sys.stderr, flush=True)
    raise RuntimeError("Model wrapper did not become healthy.")


@contextlib.contextmanager
def docker_network(name):
    docker("network", "create", "--internal", name)
    try:
        yield
    finally:
        docker_quiet("network", "rm", name)


@contextlib.contextmanager
def wrapper_container(root, image, network, run_id, case_name, model, usage_host_dir):
    container = container_name("wrapper", run_id, case_name)
    usage_host_dir.mkdir(parents=True, exist_ok=True)
    docker_quiet("rm", "-f", container)
    docker(
        "run",
        "-d",
        "--name",
        container,
        "--network",
        "bridge",
        "-e",
        "EXPRESS_MODE_KEY",
        *docker_mount(root),
        *docker_mount(usage_host_dir, "/secure/usage", "rw"),
        image,
        "python3",
        str(root / "scripts" / "model_service.py"),
        "--host",
        "0.0.0.0",
        "--port",
        WRAPPER_PORT,
        "--model",
        model,
        "--run-id",
        run_id,
        "--case-name",
        case_name,
        "--design-type",
        "block",
        "--task-type",
        "repair",
        "--usage-path",
        f"/secure/usage/{case_name}_usage.json",
    )
    try:
        docker("network", "connect", "--alias", WRAPPER_NAME, network, container)
        wait_for_wrapper(container)
        yield container
    finally:
        docker_quiet("rm", "-f", container)


def resolve_agent_path(root, agent_path):
    path = Path(agent_path)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if not path.is_file():
        raise SystemExit(f"Agent not found: {path}")
    return path


def resolve_submission_agent(root, submission_dir, agent_entrypoint):
    submission = Path(submission_dir)
    if not submission.is_absolute():
        submission = root / submission
    submission = submission.resolve()
    if not submission.is_dir():
        raise SystemExit(f"Submission directory not found: {submission}")

    entry = submission / agent_entrypoint
    entry = entry.resolve()
    try:
        entry.relative_to(submission)
    except ValueError:
        raise SystemExit("agent entrypoint must stay inside the submission directory")
    if not entry.is_file():
        raise SystemExit(f"Agent entrypoint not found: {entry}")
    return submission, entry


def agent_mount_args(root, agent_path):
    mounts = docker_mount(root)
    if not agent_path.is_relative_to(root):
        mounts.extend(docker_mount(agent_path.parent))
    return mounts


def run_agent_container(root, image, network, run_id, case_name, agent_path, info_path, model):
    container = container_name("agent", run_id, case_name)
    result_root = root / "result" / run_id
    temp_root = root / "temp" / run_id
    result_root.mkdir(parents=True, exist_ok=True)
    temp_root.mkdir(parents=True, exist_ok=True)
    docker_quiet("rm", "-f", container)

    docker(
        "run",
        "--rm",
        "--name",
        container,
        "--network",
        network,
        "--read-only",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=512m",
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
        *agent_mount_args(root, agent_path),
        *docker_mount(result_root, mode="rw"),
        *docker_mount(temp_root, mode="rw"),
        image,
        "python3",
        str(agent_path),
        str(info_path),
        "--model",
        model,
    )


def run_evaluator(root, run_id, case_name):
    cmd = [
        sys.executable,
        str(root / "evaluator" / "evaluate_repair.py"),
        "--case",
        case_name,
        "--run-id",
        run_id,
    ]
    print(f"[OFFICIAL] {' '.join(str(part) for part in cmd)}", flush=True)
    return subprocess.run(cmd, check=False).returncode == 0


def run_case(root, args, run_id, case_name, agent_path, agent_image):
    network = f"iclad-eval-{run_id}-{case_name}"
    usage_host_dir = Path("/tmp") / "iclad26_asu_official" / run_id / "usage"
    usage_path = f"/secure/usage/{case_name}_usage.json"
    info_path = write_info(
        root,
        run_id,
        case_name,
        args.model,
        f"http://{WRAPPER_NAME}:{WRAPPER_PORT}",
        usage_path,
    )

    with docker_network(network):
        with wrapper_container(root, args.image, network, run_id, case_name, args.model, usage_host_dir):
            run_agent_container(root, agent_image, network, run_id, case_name, agent_path, info_path, args.model)

    if not args.skip_eval:
        return run_evaluator(root, run_id, case_name)
    return True


def main():
    root = repo_root()
    blocks = available_blocks(root)
    parser = argparse.ArgumentParser(description="Run official ASU evaluation with Docker-isolated agent execution.")
    parser.add_argument("--case", choices=blocks, help="Run one available block. Defaults to all available blocks.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--submission-dir", help="Submission directory containing agent.py and optional requirements.txt.")
    parser.add_argument("--agent-entrypoint", default=DEFAULT_SUBMISSION_AGENT)
    parser.add_argument("--agent-path", default=DEFAULT_AGENT_PATH, help="Direct agent path for local/sample runs.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--submission-image", default="")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-eval", action="store_true")
    args = parser.parse_args()

    if not blocks:
        raise SystemExit("No complete benchmark blocks found under testcase/asap7/block.")

    run_id = validate_safe_id(args.run_id, "run-id")
    submission_dir = None
    if args.submission_dir:
        submission_dir, agent_path = resolve_submission_agent(root, args.submission_dir, args.agent_entrypoint)
    else:
        agent_path = resolve_agent_path(root, args.agent_path)

    require_docker()
    require_model_key()
    if not args.skip_build:
        build_image(root, args.image)
    agent_image = args.image
    if submission_dir:
        submission_image = args.submission_image or f"iclad26-asu-submission-{run_id}:latest"
        agent_image = build_submission_image(args.image, submission_dir, submission_image)

    cases = [args.case] if args.case else blocks
    ok = True
    for case_name in cases:
        ok = run_case(root, args, run_id, case_name, agent_path, agent_image) and ok

    print(f"[OFFICIAL] Usage artifacts: {Path('/tmp') / 'iclad26_asu_official' / run_id / 'usage'}")
    print(f"[OFFICIAL] Result artifacts: {root / 'result' / run_id}")
    print(f"[OFFICIAL] Factor artifacts: {root / 'factors' / run_id}")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
