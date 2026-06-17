# Evaluation Factors

## Command

```bash
python3 evaluator/evaluate_repair.py --case Block1 --run-id vertexai-express
```

## Output

```text
factors/<run-id>/block/repair/Block1_factors.json
```

## Scoring Policy

ASU block-repair submissions are evaluated using a gated lexicographic scoring
policy.

First, a submission must satisfy both eligibility conditions:

| Condition | Report field | Requirement |
|---|---|---|
| Valid evaluation | `valid_repair` | The repaired script completed KLayout render, DRC execution, and DRC report parsing. |
| Connectivity preservation | `connectivity_preserved` | The repaired script preserved the reference connectivity for the case. |

A submission that does not satisfy either eligibility condition is not eligible
for DRC-quality scoring for that case. Its factor report is still written for
diagnostics, but its DRC violation metrics are not used for score comparison.

Eligible submissions are scored lexicographically:

| Priority | Metric | Direction | Definition |
|---:|---|---|---|
| 1 | `final_violation_rate` | minimize | Final DRC violations / original DRC violations |
| 2 | `repair_rate` | maximize | Original DRC violations fixed / original DRC violations |

`repair_rate` is used as a tie-breaker when eligible submissions have the same
`final_violation_rate`.

**Token-cost disclaimer:** token usage is recorded by the benchmark model
service, but token cost is not included in the current ASU block-repair scoring
policy. Token-cost scoring will be documented separately if it is added in a
future update.

## Scoring Block

Each factor report includes a machine-readable `scoring` object:

```json
{
  "policy": "gated_lexicographic_score",
  "eligible_for_scoring": true,
  "score_exclusion_reason": null,
  "score_order": [
    {"field": "final_violation_rate", "direction": "minimize", "priority": 1},
    {"field": "repair_rate", "direction": "maximize", "priority": 2, "role": "tie_breaker"}
  ],
  "score_values": {
    "final_violation_rate": 0.0,
    "repair_rate": 1.0
  }
}
```

Possible `score_exclusion_reason` values are:

- `invalid_evaluation`
- `connectivity_not_preserved`

## Factors

| Symbol | Name | Direction | Definition |
|---|---|---|---|
| `α` | `valid_evaluation` | maximize | KLayout render and DRC completed |
| `β` | `connectivity_preserved` | maximize | Connectivity preserved |
| `γ` | `repair_rate` | maximize | Original DRC violations fixed / original DRC violations |
| `δ` | `new_violation_rate` | minimize | New DRC violations introduced / original DRC violations |
| `ε` | `final_violation_rate` | minimize | Final DRC violations / original DRC violations |

## Raw Fields

- `valid_repair`
- `connectivity_preserved`
- `original_violations`
- `final_violations`
- `removed_violations`
- `new_violations`
- `final_violation_rate`
- `original_rules_violated`
- `final_rules_violated`
- `connectivity_sources_checked`
- `missing_connectivity_sources`
- `pin_endpoint_mismatches`
- `routing_endpoint_count_mismatches`
- `missing_connectivity_source_details`
- `pin_endpoint_mismatch_details`
- `routing_endpoint_count_mismatch_details`
- `render_log`
- `drc_log`
