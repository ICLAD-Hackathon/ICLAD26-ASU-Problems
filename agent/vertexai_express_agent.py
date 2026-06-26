#!/usr/bin/env python3
import argparse
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path


TEXT_INPUTS = [
    ("Layout script excerpt", "path_to_layout_script"),
]

MAX_LAYOUT_CHARS = 24000
MAX_RULE_CHARS = 8000
MAX_RULES_IN_REPORT = 16
RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}


def read_required_text(path_value, label):
    path = Path(path_value)
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    content = path.read_text(encoding="utf-8", errors="replace")
    print(f"[INFO] Read {label}: {path} ({len(content):,} chars)", file=sys.stderr, flush=True)
    return path, content


def read_text_excerpt(path_value, label, max_chars):
    path, content = read_required_text(path_value, label)
    if len(content) <= max_chars:
        return path, content
    excerpt = content[:max_chars]
    print(
        f"[INFO] Using {label} excerpt: {len(excerpt):,}/{len(content):,} chars",
        file=sys.stderr,
        flush=True,
    )
    return path, excerpt


def summarize_drc_report(path_value):
    path = Path(path_value)
    if not path.is_file():
        raise FileNotFoundError(f"DRC report not found: {path}")
    with path.open(encoding="utf-8") as f:
        data = json.load(f)

    rules = []
    for rule, info in sorted(data.get("rules", {}).items()):
        count = int(info.get("violation_count", 0) or 0)
        if count > 0:
            rules.append({
                "rule": rule,
                "violation_count": count,
            })

    summary = {
        "path": str(path),
        "case": data.get("case"),
        "design_type": data.get("design_type"),
        "total_violations": sum(item["violation_count"] for item in rules),
        "violated_rule_count": len(rules),
        "violated_rules": rules[:MAX_RULES_IN_REPORT],
    }
    text = json.dumps(summary, indent=2)
    print(
        f"[INFO] Read DRC report: {path} ({path.stat().st_size:,} bytes, summarized to {len(text):,} chars)",
        file=sys.stderr,
        flush=True,
    )
    return path, text


def summarize_design_rule(path_value):
    path, content = read_text_excerpt(path_value, "DRC rule file", MAX_RULE_CHARS)
    return path, content


def bbox(points):
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def summarize_connectivity(path_value):
    path = Path(path_value)
    if not path.is_file():
        raise FileNotFoundError(f"Connectivity file not found: {path}")
    with path.open(encoding="utf-8") as f:
        data = json.load(f)

    paths = data.get("paths", [])
    source_layers = {}
    endpoint_layers = {}
    source_to_endpoint_layers = {}
    pin_endpoint_layers = {}
    samples = []

    for entry in paths:
        start = entry.get("start", {})
        end = entry.get("end", {})
        start_layer = str(start.get("layer"))
        end_layer = str(end.get("layer"))
        endpoint_key = f"{start_layer}->{end_layer}"

        source_layers[start_layer] = source_layers.get(start_layer, 0) + 1
        endpoint_layers[end_layer] = endpoint_layers.get(end_layer, 0) + 1
        source_to_endpoint_layers[endpoint_key] = source_to_endpoint_layers.get(endpoint_key, 0) + 1
        if end.get("pin"):
            pin_endpoint_layers[end_layer] = pin_endpoint_layers.get(end_layer, 0) + 1

        if len(samples) < 20:
            start_points = start.get("points", [])
            end_points = end.get("points", [])
            samples.append({
                "start_layer": start.get("layer"),
                "start_bbox": bbox(start_points) if start_points else None,
                "end_layer": end.get("layer"),
                "end_bbox": bbox(end_points) if end_points else None,
                "pin": bool(end.get("pin", False)),
            })

    summary = {
        "path": str(path),
        "case": data.get("case"),
        "design_type": data.get("design_type"),
        "connectivity_path_count": len(paths),
        "source_layer_counts": dict(sorted(source_layers.items())),
        "endpoint_layer_counts": dict(sorted(endpoint_layers.items())),
        "source_to_endpoint_layer_counts": dict(sorted(source_to_endpoint_layers.items())),
        "pin_endpoint_layer_counts": dict(sorted(pin_endpoint_layers.items())),
        "sample_paths": samples,
    }
    text = json.dumps(summary, indent=2)
    print(
        f"[INFO] Read Connectivity file: {path} ({path.stat().st_size:,} bytes, summarized to {len(text):,} chars)",
        file=sys.stderr,
        flush=True,
    )
    return path, text


@contextmanager
def heartbeat(message, interval_seconds=15):
    stop_event = threading.Event()

    def run():
        start = time.monotonic()
        while not stop_event.wait(interval_seconds):
            elapsed = int(time.monotonic() - start)
            print(f"[INFO] {message} ({elapsed}s elapsed)", file=sys.stderr, flush=True)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop_event.set()
        thread.join(timeout=1)


def build_repair_prompt(info):
    sections = [
        "You are a starter ASAP7 block-repair agent for the ICLAD 2026 benchmark.",
        "",
        "Return only JSON. Do not include markdown fences, explanations, logs, or a full Python file.",
        "",
        "JSON schema:",
        "{\"edits\":[{\"old\":\"exact substring copied from the layout script\",\"new\":\"replacement substring\"}]}",
        "",
        "Edit requirements:",
        "1. Each old substring must appear exactly once in the layout script.",
        "2. Return at most two small, safe geometry edits.",
        "3. Do not rename cells, change layout.dbu, remove layout.write, or rewrite unrelated geometry.",
        "4. Return {\"edits\": []} if no safe exact edit can be identified.",
        "",
        "Case metadata:",
        f"- case_name: {info['case_name']}",
        f"- design_type: {info['design_type']}",
        f"- task_type: {info['task_type']}",
        f"- output_path: {info['output_path']}",
        f"- layout_screenshot: {info.get('path_to_layout_screenshot', '')}",
        "",
        "Repair goals:",
        "1. Prioritize one or two clear DRC fixes from the report.",
        "2. Use the DRC rule file to confirm rule semantics and numeric thresholds.",
        "3. Preserve the top cell name and layout.dbu.",
        "4. Modify only the geometry needed for the selected local fixes.",
        "5. Preserve connectivity using the connectivity reference.",
        "6. Keep all geometry inside the block outline.",
        "",
    ]

    for label, key in TEXT_INPUTS:
        path, content = read_text_excerpt(info[key], label, MAX_LAYOUT_CHARS)
        sections.extend([
            f"===== {label}: {path} =====",
            content,
            f"===== End {label} =====",
            "",
        ])

    path, content = summarize_drc_report(info["path_to_drc_report"])
    sections.extend([
        f"===== DRC report summary: {path} =====",
        content,
        f"===== End DRC report summary =====",
        "",
    ])

    path, content = summarize_design_rule(info["path_to_design_rule"])
    sections.extend([
        f"===== DRC rule file excerpt: {path} =====",
        content,
        f"===== End DRC rule file excerpt =====",
        "",
    ])

    path, content = summarize_connectivity(info["path_to_connectivity_file"])
    sections.extend([
        f"===== Connectivity summary: {path} =====",
        content,
        f"===== End Connectivity summary =====",
        "",
    ])

    return "\n".join(sections)


def write_response_diagnostics(path, diagnostics, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = diagnostics or {}
    data["text_chars"] = len(text or "")
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def parse_error_payload(error_text):
    try:
        payload = json.loads(error_text)
    except json.JSONDecodeError:
        return {"error": error_text}
    return payload if isinstance(payload, dict) else {"error": error_text}


def should_retry_http_error(status_code, payload):
    if payload.get("retryable") is True:
        return True
    return status_code in RETRYABLE_HTTP_STATUS


def execute_with_retry(model_endpoint, prompt, model_name="gemini-3.5-flash", max_retries=5, max_output_tokens=8192, diagnostics_path=None):
    delay = 2
    url = model_endpoint.rstrip("/") + "/generate"
    request_body = json.dumps({
        "model": model_name,
        "prompt": prompt,
        "max_output_tokens": max_output_tokens,
    }).encode("utf-8")

    for attempt in range(1, max_retries + 1):
        try:
            print(
                f"[INFO] Model request attempt {attempt}/{max_retries} using {model_name}",
                file=sys.stderr,
                flush=True,
            )
            request = urllib.request.Request(
                url,
                data=request_body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with heartbeat("Waiting for model response"):
                with urllib.request.urlopen(request, timeout=300) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            text = payload.get("text") or ""
            diagnostics = payload.get("diagnostics") or {}
            if diagnostics_path:
                write_response_diagnostics(diagnostics_path, diagnostics, text)
            if not text.strip():
                print(
                    f"[WARN] Model endpoint returned no text. Diagnostics: {diagnostics}",
                    file=sys.stderr,
                    flush=True,
                )
            return text
        except urllib.error.HTTPError as exc:
            error_text = exc.read().decode("utf-8", errors="replace")
            payload = parse_error_payload(error_text)
            if not should_retry_http_error(exc.code, payload):
                raise RuntimeError(f"Model endpoint returned non-retryable error {exc.code}: {payload}")
            if attempt == max_retries:
                raise RuntimeError(f"Model endpoint retry limit reached after error {exc.code}: {payload}")
            print(
                f"[WARN] Model endpoint retryable error {exc.code}. Retrying in {delay}s... ({attempt}/{max_retries}) {payload}",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(delay)
            delay *= 2
        except urllib.error.URLError as exc:
            if attempt == max_retries:
                raise RuntimeError(f"Model endpoint unavailable after {max_retries} attempt(s): {exc}")
            print(
                f"[WARN] Model endpoint unavailable. Retrying in {delay}s... ({attempt}/{max_retries}) {exc}",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("Maximum retries exceeded while calling the model endpoint.")


def strip_markdown_fence(text):
    match = re.search(r"```(?:python|py)?\s*\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip() + "\n"
    return text.strip() + "\n"


def parse_json_edits(text):
    stripped = strip_markdown_fence(text).strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise
        data = json.loads(stripped[start:end + 1])

    edits = data.get("edits")
    if not isinstance(edits, list):
        raise ValueError("Model JSON did not contain an edits list.")
    return edits


def apply_exact_edits(layout_text, edits):
    updated = layout_text
    applied = 0
    for index, edit in enumerate(edits, start=1):
        old = edit.get("old") if isinstance(edit, dict) else None
        new = edit.get("new") if isinstance(edit, dict) else None
        if not isinstance(old, str) or not isinstance(new, str) or not old:
            raise ValueError(f"Edit {index} is missing a valid old/new string.")
        count = updated.count(old)
        if count != 1:
            raise ValueError(f"Edit {index} old substring matched {count} time(s); expected exactly 1.")
        updated = updated.replace(old, new, 1)
        applied += 1
    return updated, applied


def build_repaired_script(layout_text, model_output):
    if not model_output.strip():
        return layout_text, 0, "empty_model_response"

    edits = parse_json_edits(model_output)
    if not edits:
        return layout_text, 0, "no_model_edits"

    repaired_script, applied = apply_exact_edits(layout_text, edits)
    return repaired_script, applied, None


def main():
    parser = argparse.ArgumentParser(description="Vertex AI Express Mode starter benchmark agent")
    parser.add_argument("info_json", help="Benchmark case metadata JSON produced by scripts/run_block_benchmark.py")
    parser.add_argument("--model", default="gemini-3.5-flash")
    parser.add_argument("--max-retries", type=int, default=5)
    parser.add_argument("--max-output-tokens", type=int, default=8192)
    args = parser.parse_args()

    with Path(args.info_json).open(encoding="utf-8") as f:
        info = json.load(f)

    print(
        f"[INFO] Starting case {info['case_name']} ({info['design_type']} {info['task_type']})",
        file=sys.stderr,
        flush=True,
    )

    output_path = Path(info["output_path"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(info["temp_dir"])
    temp_dir.mkdir(parents=True, exist_ok=True)

    layout_path = Path(info["path_to_layout_script"])
    layout_text = layout_path.read_text(encoding="utf-8", errors="replace")
    prompt = build_repair_prompt(info)
    print(f"[INFO] Built model prompt ({len(prompt):,} chars)", file=sys.stderr, flush=True)
    model_endpoint = info.get("model_endpoint")
    if not model_endpoint:
        raise RuntimeError("model_endpoint is missing from the case metadata. Run the agent through scripts/run_block_benchmark.py.")

    print(f"[INFO] Dispatching repair prompt to model endpoint: {model_endpoint}", file=sys.stderr, flush=True)
    output = execute_with_retry(
        model_endpoint,
        prompt,
        args.model,
        args.max_retries,
        args.max_output_tokens,
        temp_dir / "vertex_response_diagnostics.json",
    )
    model_output_path = temp_dir / "vertex_model_output.txt"
    model_output_path.write_text(output, encoding="utf-8")

    try:
        repaired_script, applied, fallback_reason = build_repaired_script(layout_text, output)
    except Exception as exc:
        repaired_script = layout_text
        applied = 0
        fallback_reason = f"invalid_model_edits: {exc}"

    if fallback_reason:
        print(
            f"[WARN] No model edits were applied ({fallback_reason}). Wrote baseline copy for evaluation flow.",
            file=sys.stderr,
            flush=True,
        )
    else:
        print(f"[INFO] Applied {applied} model edit(s).", file=sys.stderr, flush=True)

    output_path.write_text(repaired_script, encoding="utf-8")
    print(
        f"[INFO] Wrote repaired script to {output_path} ({len(repaired_script):,} chars)",
        file=sys.stderr,
        flush=True,
    )


if __name__ == "__main__":
    main()
