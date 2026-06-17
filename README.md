# ICLAD 2026 ASU Benchmark Problems

This repository contains the ASU block-repair benchmark package for the ICLAD 2026 contest. The benchmark asks an agent to repair ASAP7 KLayout Python layout scripts using the provided DRC reports, design rules, screenshots, and connectivity references.

This GitHub repository is the official read-only distribution for participants. Clone or download the repository, run it in your local copy, and follow the contest submission instructions for final entries.

## Repository Contents

```text
agent/
  vertexai_express_agent.py         Vertex AI Express Mode starter agent
scripts/
  run_block_benchmark.py            Case generation and agent runner
  model_service.py                  Benchmark model service and token recorder
evaluator/
  evaluate_repair.py                Block repair factor calculator
  check_connectivity.py             Connectivity preservation checker
  README.md                         Evaluation factor definitions
testcase/asap7/
  asap7.lydrc                       ASAP7 block DRC rule deck
  asap7.lyp                         KLayout layer properties
  block/
    layout_script/BlockN.py
    drc_report/BlockN.drc.json
    connectivity/BlockN.json
    layout_screenshot/BlockN/BlockN.png
requirements.txt                    Python dependencies
DEPENDENCIES.md                     System and Python dependency list
```

The runner discovers complete benchmark cases from the files present under `testcase/asap7/block/`.

## Setup

Install the system and Python dependencies listed in [DEPENDENCIES.md](./DEPENDENCIES.md).

Create a Python environment and install the Python packages:

```bash
cd ASU-Problems
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Set your Vertex AI Express Mode API key:

```bash
export EXPRESS_MODE_KEY="your_actual_api_key_here"
```

Keep API keys and credentials outside the repository. Local `.env` files and common credential filenames are ignored by default.

## Prepare A Benchmark Case

Prepare one benchmark case without calling an agent:

```bash
python3 scripts/run_block_benchmark.py --case Block1 --prepare-only
```

The runner writes a case metadata JSON under:

```text
task/vertexai-express/block/repair/
```

The case metadata includes benchmark input paths, the agent output path, and the token-usage output path. During a benchmark run, the runner also provides a local model endpoint.

## Run The Starter Agent

Run one block:

```bash
python3 scripts/run_block_benchmark.py --case Block1
```

Run all available blocks:

```bash
python3 scripts/run_block_benchmark.py
```

By default, outputs are written under:

```text
result/vertexai-express/block/repair/BlockN/BlockN_repaired.py
```

Token usage is written under:

```text
usage/vertexai-express/block/repair/BlockN_usage.json
```

The usage report includes `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `thoughts_tokens`, `tool_use_prompt_tokens`, `total_tokens`, and `num_calls`.

A basic successful run produces a non-empty repaired script. For `Block1`, check:

```bash
test -s result/vertexai-express/block/repair/Block1/Block1_repaired.py
python3 -m py_compile result/vertexai-express/block/repair/Block1/Block1_repaired.py
```

Use `--model` to select a different Express Mode model:

```bash
python3 scripts/run_block_benchmark.py --case Block1 --model gemini-3-flash-preview
```

Use `--run-id` to keep outputs from different agents or runs separate:

```bash
python3 scripts/run_block_benchmark.py --case Block1 --run-id my-agent-v1
```

Use `--agent-path` to run a custom agent from your local copy:

```bash
python3 scripts/run_block_benchmark.py --case Block1 --agent-path agent/my_agent.py --run-id my-agent-v1
```

## Calculate Evaluation Factors

Repair evaluation requires KLayout `0.30.1` on `PATH`.

Calculate factors for one repaired block:

```bash
python3 evaluator/evaluate_repair.py --case Block1 --run-id vertexai-express
```

Calculate factors for all available repaired blocks:

```bash
python3 evaluator/evaluate_repair.py --run-id vertexai-express
```

Factor reports are written under:

```text
factors/<run-id>/block/repair/
```

Each report includes raw DRC/connectivity metrics and Greek-letter factors:

- `α`: valid evaluation, meaning KLayout render and DRC completed
- `β`: connectivity preserved
- `γ`: repair rate, original violations fixed divided by original violations
- `δ`: new violation rate, new violations introduced divided by original violations
- `ε`: final violation rate, final violations divided by original violations

## Scoring Policy

Submissions are scored using a gated lexicographic policy. A repaired script is
eligible for DRC-quality scoring for a case only if it satisfies both
eligibility conditions:

1. **Valid evaluation**: the evaluator must be able to render the repaired script
   with KLayout, run the ASAP7 DRC deck, and parse the resulting DRC report.
2. **Connectivity preservation**: the connectivity traced from the repaired
   script must preserve the reference connectivity provided for the case.

Submissions that do not satisfy either eligibility condition are not eligible
for DRC-quality scoring for that case, regardless of their DRC violation
metrics.

Among eligible submissions, score comparison is determined lexicographically:

1. Lower `final_violation_rate` is better.
2. If `final_violation_rate` is equal, higher `repair_rate` is better.

**Token-cost disclaimer:** token usage is recorded by the benchmark model
service, but token cost is not included in the current ASU block-repair scoring
policy. Token-cost scoring will be documented separately if it is added in a
future update.

See [evaluator/README.md](./evaluator/README.md) for factor definitions.

## Build Your Own Agent

The included Vertex AI Express Mode starter agent demonstrates the required interface. In a local copy of the benchmark, participants can point the runner at their own agent as long as it reads the case `info.json`, uses the benchmark model endpoint listed there, uses the file paths listed there, and writes the repaired Python script to the specified `output_path`.

See [AGENT_GUIDE.md](./AGENT_GUIDE.md) for the expected agent interface, an explanation of how file access works, and Vertex AI Express Mode setup steps.
