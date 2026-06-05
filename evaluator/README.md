# Evaluation Factors

## Command

```bash
python3 evaluator/evaluate_repair.py --case Block1 --run-id vertexai-express
```

## Output

```text
factors/<run-id>/block/repair/Block1_factors.json
```

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
