# Official Evaluation

This path is for organizers. Participants can keep using the normal development
runner in `scripts/run_block_benchmark.py`.

## Setup

Install Docker on the evaluation machine.

Install KLayout `0.30.1` on the host if you want to run the evaluator after the
agent finishes.

Set the model-service key in the organizer shell:

```bash
export EXPRESS_MODE_KEY="your_actual_api_key_here"
```

## Run A Sample Evaluation

From `problem-categories/ICLAD26-ASU-Problems`:

```bash
python3 official_eval/run_official_eval.py \
  --case Block1 \
  --run-id sample-official \
  --agent-path agent/vertexai_express_agent.py
```

Omit `--case` to run every complete block:

```bash
python3 official_eval/run_official_eval.py \
  --run-id sample-official \
  --agent-path agent/vertexai_express_agent.py
```

The script builds a small Docker image, starts the model wrapper with the key,
runs the agent on an internal Docker network, and then runs the existing
evaluator on the host.

Use `--skip-eval` only when debugging agent execution. Without that flag, this
is the full organizer flow for one case.

## Submission Format

Ask teams to submit a directory with this shape:

```text
submission/
  agent.py
  requirements.txt    optional
  README.md           optional
```

The agent must support the benchmark invocation:

```bash
python3 agent.py <info_json> --model <model_name>
```

If `requirements.txt` exists, the official runner builds a derived Docker image
and installs those Python dependencies before running the agent.

Submissions should not include API keys, local credentials, generated benchmark
outputs, virtual environments, or Python cache directories.

Run a submitted agent:

```bash
python3 official_eval/run_official_eval.py \
  --run-id team1 \
  --submission-dir submissions/team1
```

Add `--case Block1` when debugging one case.

Use `--agent-entrypoint` if the entry file is not named `agent.py`.

## What Is Isolated

The agent container does not receive `EXPRESS_MODE_KEY`.

The agent container is attached only to an internal Docker network. It can reach
the model wrapper, but it should not have general internet access.

Usage is written to `/tmp/iclad26_asu_official/<run-id>/usage`, which is not
mounted into the agent container.
