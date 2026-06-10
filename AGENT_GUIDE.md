# Agent Guide

This guide explains what an agent is in this benchmark and how to connect one to the ICLAD 2026 ASU runner.

## Benchmark Interface

The runner-agent interface is defined by this benchmark. It is not a Vertex AI API standard.

The runner creates an `info.json` file, starts or provides a local model endpoint, invokes the agent with the path to `info.json`, and evaluates the repaired script written by the agent.

```text
runner -> info.json -> agent -> model_endpoint -> agent -> output_path -> evaluator
```

## Agent Invocation

The runner invokes an agent as:

```bash
python3 agent/your_agent.py <info_json> --model <model_name>
```

Arguments:

| Argument | Meaning |
|---|---|
| `<info_json>` | Path to the benchmark case metadata JSON |
| `--model` | Model name to request through the benchmark model endpoint |

The agent may create intermediate files under `temp_dir`.

## `info.json` Template

The runner creates one case metadata file:

```bash
task/<run-id>/block/repair/BlockN_info.json
```

Template:

```json
{
  "model_name": "<run-id>",
  "model": "<model-name>",
  "model_endpoint": "http://127.0.0.1:<port>",
  "case_name": "Block1",
  "design_type": "block",
  "task_type": "repair",
  "path_to_layout_script": "testcase/asap7/block/layout_script/Block1.py",
  "path_to_layout_screenshot": "testcase/asap7/block/layout_screenshot/Block1/Block1.png",
  "path_to_drc_report": "testcase/asap7/block/drc_report/Block1.drc.json",
  "path_to_design_rule": "testcase/asap7/asap7.lydrc",
  "path_to_connectivity_file": "testcase/asap7/block/connectivity/Block1.json",
  "output_path": "result/<run-id>/block/repair/Block1/Block1_repaired.py",
  "temp_dir": "temp/<run-id>/block/repair/Block1",
  "usage_path": "usage/<run-id>/block/repair/Block1_usage.json"
}
```

Fields:

| Field | Meaning |
|---|---|
| `model_name` | Run identifier used for output folders |
| `model` | Default model name for model endpoint requests |
| `model_endpoint` | Local benchmark model service base URL |
| `case_name` | Benchmark block name |
| `design_type` | Design category, currently `block` |
| `task_type` | Task category, currently `repair` |
| `path_to_layout_script` | Original KLayout Python layout script |
| `path_to_layout_screenshot` | Reference screenshot for the original block |
| `path_to_drc_report` | Original DRC report JSON |
| `path_to_design_rule` | KLayout DRC rule deck |
| `path_to_connectivity_file` | Reference connectivity JSON |
| `output_path` | Required repaired Python script output path |
| `temp_dir` | Scratch directory for agent-generated intermediate files |
| `usage_path` | Token usage report written by the benchmark model service |

## Model Endpoint Template

Agents must send model calls to:

```text
POST <model_endpoint>/generate
Content-Type: application/json
```

Request JSON:

```json
{
  "model": "gemini-3-flash-preview",
  "prompt": "model prompt text",
  "max_output_tokens": 8192
}
```

Request fields:

| Field | Required | Meaning |
|---|---:|---|
| `model` | yes | Model name passed through to Vertex AI Express Mode |
| `prompt` | yes | Prompt text prepared by the agent |
| `max_output_tokens` | no | Maximum model response tokens |

Response JSON:

```json
{
  "text": "model response text",
  "diagnostics": {}
}
```

Response fields:

| Field | Meaning |
|---|---|
| `text` | Model response text |
| `diagnostics` | Endpoint diagnostics for the model call |

Token usage is recorded by the benchmark model service under `usage/<run-id>/block/repair/`.

Error response JSON:

```json
{
  "error": "error message",
  "retryable": true,
  "provider": "vertexai",
  "provider_status": 429
}
```

Error fields:

| Field | Meaning |
|---|---|
| `error` | Error message |
| `retryable` | Whether the agent should retry the request |
| `provider` | Upstream provider name when the error came from a model provider |
| `provider_status` | Upstream provider status code when available |

The benchmark model service returns retryable Vertex AI errors, including rate-limit responses, to the agent. Agents are responsible for retry policy and backoff behavior.

## Agent Responsibilities

The agent must:

1. Read `<info_json>`.
2. Use the file paths in that JSON as benchmark inputs.
3. Send model requests to `model_endpoint`.
4. Write the complete repaired script to `output_path`.

The output file must contain valid Python only. Do not write markdown fences, explanations, logs, or JSON wrappers into the repaired script.

## Custom Agent Tools

The provided starter agent is intentionally minimal and demonstrates a
chat-style interaction with the benchmark model endpoint. Participants may
customize their agents beyond this example.

Custom agents may add local tools that help inspect, modify, and validate the
layout repair, including:

- KLayout execution tools for rendering layouts, running DRC, or checking
  intermediate repaired scripts.
- File read tools for loading the layout script, DRC report, design rules,
  screenshot metadata, connectivity file, or intermediate artifacts.
- File write tools for creating repaired scripts, temporary candidate scripts,
  logs, summaries, or other files under `temp_dir`.

These tools are part of the participant's agent implementation. They do not
change the required benchmark interface: the agent must still read `info.json`,
send model requests through `model_endpoint`, and write the final repaired
Python script to `output_path`.

## Vertex AI Express Mode Setup

1. Open the contest-provided Vertex AI Express Mode onboarding page.
2. Launch the sandbox.
3. On the Agent Platform dashboard, select `Get API key`.
4. Select `Create API Key`.
5. Copy the generated key and store it securely.

Install dependencies:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Set your key in the shell that will run the benchmark:

```bash
export EXPRESS_MODE_KEY="your_actual_api_key_here"
```

## Starter Agent

The included starter agent is:

```bash
agent/vertexai_express_agent.py
```

Run it through the benchmark runner:

```bash
python3 scripts/run_block_benchmark.py --case Block1
```

The starter agent reads the layout script, DRC report, DRC rule file, and connectivity reference listed in `info.json`, sends a repair prompt to `model_endpoint`, applies valid local edits, and writes the repaired script to `output_path`.

The starter agent includes a minimal endpoint retry loop for `429`, `500`, `502`, `503`, and `504` responses, and for error responses with `"retryable": true`.

## Run A Custom Agent

The contest repository is read-only for participants. Create and run your agent in a local copy.

Example:

```bash
python3 scripts/run_block_benchmark.py \
  --case Block1 \
  --agent-path agent/my_agent.py \
  --run-id my-agent-v1
```

Your `agent/my_agent.py` should implement the required interface described above.
