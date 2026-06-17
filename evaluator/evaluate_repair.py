#!/usr/bin/env python3
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

DEFAULT_RUN_ID = "vertexai-express"
REQUIRED_KLAYOUT_VERSION = "0.30.1"


def repo_root():
    return Path(__file__).resolve().parents[1]


def available_blocks(root):
    block_dir = root / "testcase" / "asap7" / "block"
    blocks = []
    for layout_path in sorted((block_dir / "layout_script").glob("Block*.py"), key=block_sort_key):
        case_name = layout_path.stem
        required_paths = [
            layout_path,
            block_dir / "drc_report" / f"{case_name}.drc.json",
            block_dir / "connectivity" / f"{case_name}.json",
        ]
        if all(path.is_file() for path in required_paths):
            blocks.append(case_name)
    return blocks


def block_sort_key(path):
    suffix = path.stem.removeprefix("Block")
    return (0, int(suffix)) if suffix.isdigit() else (1, path.stem)


def read_original_counts(path):
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    return {
        rule: int(info.get("violation_count", 0) or 0)
        for rule, info in data.get("rules", {}).items()
    }


def read_lyrpt_counts(path):
    tree = ET.parse(path)
    root = tree.getroot()
    counts = {}
    items = root.find("items")
    if items is None:
        return counts

    for item in items.findall("item"):
        category = item.find("category")
        if category is None or not category.text:
            continue
        rule = category.text.strip().strip("'")
        values = item.find("values")
        count = len(values.findall("value")) if values is not None else 0
        counts[rule] = counts.get(rule, 0) + count
    return counts


def write_counts_json(path, case_name, counts):
    data = {
        "case": case_name,
        "design_type": "block",
        "rules": {
            rule: {"violation_count": count}
            for rule, count in sorted(counts.items())
        },
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def calculate_drc_metrics(original, repaired):
    original_violations = sum(original.values())
    final_violations = sum(repaired.values())
    final_violation_rate = ratio(final_violations, original_violations)

    if original_violations == 0:
        removed_violations = 0
        repair_rate = 1.0
    else:
        removed_violations = sum(
            max(0, count - repaired.get(rule, 0))
            for rule, count in original.items()
        )
        repair_rate = removed_violations / original_violations

    new_violations = sum(
        max(0, count - original.get(rule, 0))
        for rule, count in repaired.items()
    )
    if original_violations == 0:
        new_violation_rate = "inf" if new_violations else 0.0
    else:
        new_violation_rate = new_violations / original_violations

    return {
        "repair_rate": repair_rate,
        "new_violation_rate": new_violation_rate,
        "final_violation_rate": final_violation_rate,
        "original_violations": original_violations,
        "final_violations": final_violations,
        "removed_violations": removed_violations,
        "new_violations": new_violations,
        "original_rules_violated": sum(1 for value in original.values() if value > 0),
        "final_rules_violated": sum(1 for value in repaired.values() if value > 0),
    }


def prepare_render_script(input_script, render_script, output_gds):
    script_text = input_script.read_text(encoding="utf-8", errors="replace")
    replacement = f"layout.write({json.dumps(str(output_gds))})"
    updated_text, count = re.subn(
        r"layout\.write\(\s*(['\"]).*?\1\s*\)",
        replacement,
        script_text,
    )
    if count == 0:
        if updated_text and not updated_text.endswith("\n"):
            updated_text += "\n"
        updated_text += f"\n{replacement}\n"
    render_script.write_text(updated_text, encoding="utf-8")


def require_klayout():
    if shutil.which("klayout") is None:
        raise RuntimeError("klayout was not found on PATH")
    completed = subprocess.run(
        ["klayout", "-v"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    version_text = completed.stdout.strip()
    match = re.search(r"KLayout\s+([0-9]+(?:\.[0-9]+)+)", version_text)
    found_version = match.group(1) if match else "unknown"
    if found_version != REQUIRED_KLAYOUT_VERSION:
        raise RuntimeError(
            f"KLayout {REQUIRED_KLAYOUT_VERSION} is required for the ASAP7 DRC rule deck; "
            f"found {found_version}."
        )


def run_command(cmd, label, log_path):
    print(f"[INFO] {label}: {' '.join(str(part) for part in cmd)}", flush=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    log_path.write_text(completed.stdout or "", encoding="utf-8")
    print(f"[INFO] {label} log: {log_path}", flush=True)
    if completed.returncode != 0:
        raise RuntimeError(f"{label} failed with exit code {completed.returncode}")


def run_klayout_drc(gds_path, rule_path, report_path, log_path):
    run_command(
        [
            "klayout",
            "-b",
            "-r",
            str(rule_path),
            "-rd",
            f"in_gds={gds_path}",
            "-rd",
            f"report_file={report_path}",
        ],
        "KLayout DRC",
        log_path,
    )


def evaluate_connectivity(conn_path, repaired_script):
    try:
        from check_connectivity import check_connectivity

        return check_connectivity(str(conn_path), str(repaired_script), "block")
    except Exception as exc:
        return {
            "connectivity_preserved": None,
            "passed": False,
            "connectivity_check_error": str(exc),
            "details": f"Connectivity check failed: {exc}",
        }


def merge_connectivity(report, connectivity):
    report["connectivity_preserved"] = connectivity.get("connectivity_preserved")
    report["connectivity_sources_checked"] = connectivity.get("connectivity_sources_checked")
    report["missing_connectivity_sources"] = connectivity.get("missing_connectivity_sources")
    report["pin_endpoint_mismatches"] = connectivity.get("pin_endpoint_mismatches")
    report["routing_endpoint_count_mismatches"] = connectivity.get("routing_endpoint_count_mismatches")
    if connectivity.get("missing_connectivity_source_details"):
        report["missing_connectivity_source_details"] = connectivity["missing_connectivity_source_details"]
    if connectivity.get("pin_endpoint_mismatch_details"):
        report["pin_endpoint_mismatch_details"] = connectivity["pin_endpoint_mismatch_details"]
    if connectivity.get("routing_endpoint_count_mismatch_details"):
        report["routing_endpoint_count_mismatch_details"] = connectivity["routing_endpoint_count_mismatch_details"]
    if connectivity.get("connectivity_check_error"):
        report["connectivity_check_error"] = connectivity["connectivity_check_error"]
    if connectivity.get("details"):
        report["connectivity_details"] = connectivity["details"]


def binary_value(value):
    if value is None:
        return None
    return 1.0 if bool(value) else 0.0


def ratio(numerator, denominator):
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def build_factors(report):
    return {
        "α": {
            "name": "valid_evaluation",
            "description": "KLayout render and DRC completed for the repaired script.",
            "raw_value": binary_value(report.get("valid_repair")),
            "direction": "maximize",
        },
        "β": {
            "name": "connectivity_preserved",
            "description": "Block connectivity was preserved by the repaired script.",
            "raw_value": binary_value(report.get("connectivity_preserved")),
            "direction": "maximize",
        },
        "γ": {
            "name": "repair_rate",
            "description": "Original DRC violations fixed divided by original DRC violations.",
            "raw_value": report.get("repair_rate"),
            "direction": "maximize",
        },
        "δ": {
            "name": "new_violation_rate",
            "description": "New DRC violations introduced divided by original DRC violations.",
            "raw_value": report.get("new_violation_rate"),
            "direction": "minimize",
        },
        "ε": {
            "name": "final_violation_rate",
            "description": "Final DRC violations divided by original DRC violations.",
            "raw_value": report.get("final_violation_rate"),
            "direction": "minimize",
        },
    }


def build_scoring(report):
    if not report.get("valid_repair"):
        eligible = False
        reason = "invalid_evaluation"
    elif report.get("connectivity_preserved") is not True:
        eligible = False
        reason = "connectivity_not_preserved"
    else:
        eligible = True
        reason = None

    return {
        "policy": "gated_lexicographic_score",
        "eligible_for_scoring": eligible,
        "score_exclusion_reason": reason,
        "eligibility_conditions": [
            {
                "field": "valid_evaluation",
                "description": "The repaired script completed KLayout render, DRC execution, and DRC report parsing.",
                "satisfied": bool(report.get("valid_repair")),
            },
            {
                "field": "connectivity_preserved",
                "description": "The repaired script preserved the reference connectivity for the case.",
                "satisfied": report.get("connectivity_preserved") is True,
            },
        ],
        "score_order": [
            {
                "field": "final_violation_rate",
                "direction": "minimize",
                "priority": 1,
                "description": "Primary score comparison metric among eligible submissions.",
            },
            {
                "field": "repair_rate",
                "direction": "maximize",
                "priority": 2,
                "role": "tie_breaker",
                "description": "Tie-breaker when final violation rates are equal.",
            },
        ],
        "score_values": {
            "final_violation_rate": report.get("final_violation_rate") if eligible else None,
            "repair_rate": report.get("repair_rate") if eligible else None,
        },
    }


def finalize_factor_report(report):
    report["factors"] = build_factors(report)
    report["scoring"] = build_scoring(report)
    return report


def write_factor_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(finalize_factor_report(report), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if report.get("invalid_reason"):
        print(f"[ERROR] {report['invalid_reason']}", flush=True)
    print(f"[INFO] Wrote factors: {path}", flush=True)


def evaluate_case(root, run_id, case_name):
    case_dir = root / "testcase" / "asap7" / "block"
    factors_path = root / "factors" / run_id / "block" / "repair" / f"{case_name}_factors.json"
    temp_dir = root / "temp" / "eval" / run_id / "block" / "repair" / case_name
    repaired_script = root / "result" / run_id / "block" / "repair" / case_name / f"{case_name}_repaired.py"
    original_drc = case_dir / "drc_report" / f"{case_name}.drc.json"
    connectivity_json = case_dir / "connectivity" / f"{case_name}.json"
    rule_path = root / "testcase" / "asap7" / "asap7.lydrc"

    report = {
        "case_name": case_name,
        "design_type": "block",
        "task_type": "repair",
        "run_id": run_id,
        "valid_repair": False,
        "repaired_script": str(repaired_script),
    }

    if not repaired_script.is_file() or repaired_script.stat().st_size == 0:
        report["invalid_reason"] = "repaired_script_missing_or_empty"
        write_factor_report(factors_path, report)
        return False

    connectivity = evaluate_connectivity(connectivity_json, repaired_script)
    merge_connectivity(report, connectivity)

    temp_dir.mkdir(parents=True, exist_ok=True)
    render_script = temp_dir / f"{case_name}_render.py"
    gds_path = temp_dir / f"{case_name}.gds"
    lyrpt_path = temp_dir / f"{case_name}.lyrpt"
    repaired_drc_json = temp_dir / f"{case_name}.drc.json"
    render_log = temp_dir / f"{case_name}_render.log"
    drc_log = temp_dir / f"{case_name}_drc.log"

    try:
        require_klayout()
        prepare_render_script(repaired_script, render_script, gds_path)
        run_command(["klayout", "-b", "-r", str(render_script)], "KLayout render", render_log)
        run_klayout_drc(gds_path, rule_path, lyrpt_path, drc_log)
        repaired_counts = read_lyrpt_counts(lyrpt_path)
        write_counts_json(repaired_drc_json, case_name, repaired_counts)
        original_counts = read_original_counts(original_drc)
        report.update(calculate_drc_metrics(original_counts, repaired_counts))
        report["valid_repair"] = True
        report["rendered_gds"] = str(gds_path)
        report["repaired_drc_report"] = str(lyrpt_path)
        report["repaired_drc_json"] = str(repaired_drc_json)
        report["render_log"] = str(render_log)
        report["drc_log"] = str(drc_log)
    except Exception as exc:
        report["invalid_reason"] = str(exc)

    write_factor_report(factors_path, report)
    return bool(report.get("valid_repair"))


def main():
    root = repo_root()
    blocks = available_blocks(root)
    parser = argparse.ArgumentParser(description="Calculate block repair evaluation factors")
    parser.add_argument("--case", choices=blocks, help="Evaluate one available block. Defaults to all available blocks.")
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    args = parser.parse_args()

    if not blocks:
        raise SystemExit("No complete benchmark blocks found under testcase/asap7/block.")

    cases = [args.case] if args.case else blocks
    ok = True
    for case_name in cases:
        print(f"[INFO] Evaluating {case_name} for run_id={args.run_id}", flush=True)
        ok = evaluate_case(root, args.run_id, case_name) and ok

    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
