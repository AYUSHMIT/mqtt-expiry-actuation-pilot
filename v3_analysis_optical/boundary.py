"""Offline, plan-aware boundary analysis. Never imports or runs an experiment.

This checks CSV consistency; it does NOT replace raw-event attribution auditing.
Run `python -m v3_analysis_optical.boundary --help` for the input contract.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import itertools
import json
from pathlib import Path
from typing import Any
from .common import DataError, integer, number, read_csv, read_json, write_csv, write_json, stats, sha256, new_output, manifest, runtime

DEFAULT_PLAN = Path(__file__).parent / "plans" / "boundary_stage1.json"
REQUIRED = {
    "command_id", "policy", "queue_depth", "ttl_s", "rep", "expires_at_ms",
    "receipt_count", "received_at_ms", "pre_service_count", "pre_service_at_ms",
    "pre_service_lateness_ms", "endpoint_on_transition_count", "endpoint_on_at_ms",
    "rejected_count", "outcome", "unmatched_endpoint_on_count", "unmatched_endpoint_off_count",
}
REJECTIONS = {"REJECTED_TRIGGER_CHECK", "REJECTED_PREDICTIVE_ADMISSION", "REJECTED_EXECUTION_CHECK"}
GOOD = {"ON_TIME_PRE_SERVICE", "LATE_PRE_SERVICE", "BOUNDARY_EXCLUDE_FROM_HEADLINE"} | REJECTIONS

def validate_plan(plan: dict) -> None:
    if plan.get("schema") != "MQTT-V3-ANALYSIS-PLAN-1":
        raise DataError("Unsupported analysis-plan schema")
    for field in ("policies", "queue_depths", "ttl_grid_s"):
        vals = plan.get(field)
        if not isinstance(vals, list) or not vals or len(vals) != len(set(vals)):
            raise DataError(f"Plan {field} must be a nonempty unique list")
    for q in plan["queue_depths"]:
        integer(q, "plan queue depth")
    for ttl in plan["ttl_grid_s"]:
        if number(ttl, "plan TTL") <= 0:
            raise DataError("TTL must be positive")
    integer(plan.get("repetitions"), "plan repetitions", 1)
    for field in ("clock_bound_ms", "classification_margin_ms", "numeric_consistency_tolerance_ms"):
        if number(plan.get(field), field) < 0:
            raise DataError(f"{field} must not be negative")
    if plan["classification_margin_ms"] < plan["clock_bound_ms"]:
        raise DataError("Classification margin is smaller than the declared clock bound")

def classify_time(delta: float, margin: float) -> str:
    if delta > margin:
        return "LATE_PRE_SERVICE"
    if delta < -margin:
        return "ON_TIME_PRE_SERVICE"
    return "BOUNDARY_EXCLUDE_FROM_HEADLINE"

def key_of(row: dict) -> tuple:
    return (row["policy"], integer(row["queue_depth"], "queue_depth"),
            number(row["ttl_s"], "ttl_s"), integer(row["rep"], "rep", 1))

def inspect_row(row: dict, plan: dict) -> dict:
    """Recompute arithmetic, preserving supplied values and problem rows."""
    out = dict(row)
    out.update(analysis_status="", analysis_issue="", recomputed_lateness_ms=None,
               receipt_to_pre_service_ms=None, recomputed_time_class="")
    try:
        cid = row["command_id"].strip()
        if not cid:
            raise DataError("Empty command_id")
        key = key_of(row)
        if (key[0] not in plan["policies"] or key[1] not in plan["queue_depths"]
            or key[2] not in plan["ttl_grid_s"] or key[3] > plan["repetitions"]):
            raise DataError("Row is not part of the specified plan")
        deadline = number(row["expires_at_ms"], "expires_at_ms")
        nreceipt = integer(row["receipt_count"], "receipt_count")
        npre = integer(row["pre_service_count"], "pre_service_count")
        non = integer(row["endpoint_on_transition_count"], "endpoint_on_transition_count")
        nrej = integer(row["rejected_count"], "rejected_count")
        unmatched = integer(row["unmatched_endpoint_on_count"], "unmatched ON") + integer(row["unmatched_endpoint_off_count"], "unmatched OFF")
        supplied = row["outcome"].strip()
        if npre:
            pre = number(row["pre_service_at_ms"], "pre_service_at_ms")
            delta = pre - deadline
            out["recomputed_lateness_ms"] = delta
            out["recomputed_time_class"] = classify_time(delta, plan["classification_margin_ms"])
            claimed = number(row["pre_service_lateness_ms"], "pre_service_lateness_ms")
            if abs(claimed - delta) > plan["numeric_consistency_tolerance_ms"]:
                raise DataError("DERIVED_FIELD_MISMATCH: stored lateness differs from timestamp subtraction")
        elif any(str(row.get(f, "")).strip() for f in ("pre_service_at_ms", "pre_service_lateness_ms")):
            raise DataError("Zero pre-service count has a fabricated/non-null request timestamp or lateness")
        if non:
            number(row["endpoint_on_at_ms"], "endpoint_on_at_ms")
        elif str(row.get("endpoint_on_at_ms", "")).strip():
            raise DataError("Zero endpoint-ON count has a non-null timestamp")
        if nreceipt != 1:
            out["analysis_status"] = "INVALID_NO_HA_RECEIPT" if nreceipt == 0 else "UNRESOLVED_DUPLICATE_RECEIPT"
            return out
        received = number(row["received_at_ms"], "received_at_ms")
        if npre:
            out["receipt_to_pre_service_ms"] = pre - received
            if pre + plan["clock_bound_ms"] < received:
                raise DataError("Pre-service observation predates receipt outside clock tolerance")
        if row.get("clock_bound_ms", "").strip() and number(row["clock_bound_ms"], "clock bound") != plan["clock_bound_ms"]:
            raise DataError("Row clock bound disagrees with plan")
        if received + plan["clock_bound_ms"] >= deadline:
            out["analysis_status"] = "INVALID_RECEIPT_NOT_PROVEN_BEFORE_DEADLINE"
        elif npre > 1:
            out["analysis_status"] = "DUPLICATE_PRE_SERVICE"
        elif non > 1:
            out["analysis_status"] = "DUPLICATE_ENDPOINT_ON"
        elif nrej and (npre or non):
            out["analysis_status"] = "INVALID_REJECT_AND_EXECUTE"
        elif unmatched:
            out["analysis_status"] = "UNRESOLVED_UNEXPLAINED_ENDPOINT_ACTIVITY"
        elif nrej:
            if nrej != 1 or supplied not in REJECTIONS:
                raise DataError("Rejection count/reason is not uniquely specified")
            expected = {"physical_v3_trigger_check": "REJECTED_TRIGGER_CHECK",
                        "physical_v3_predictive_admission": "REJECTED_PREDICTIVE_ADMISSION",
                        "physical_v3_execution_check": "REJECTED_EXECUTION_CHECK"}.get(key[0])
            if supplied != expected:
                raise DataError("Rejection label does not match policy")
            out["analysis_status"] = supplied
        elif not npre or not non:
            out["analysis_status"] = "UNRESOLVED_ENDPOINT_ATTRIBUTION"
        elif supplied.startswith(("INVALID", "UNRESOLVED", "DUPLICATE", "NOT_OBSERVED")):
            # Never promote a row that the upstream event audit rejected.
            out["analysis_status"] = supplied
        elif supplied != out["recomputed_time_class"]:
            raise DataError("TIME_LABEL_MISMATCH: supplied label disagrees with the frozen margin")
        else:
            out["analysis_status"] = out["recomputed_time_class"]
    except (KeyError, DataError, TypeError, ValueError) as exc:
        out["analysis_status"] = "INVALID_INPUT_CONSISTENCY"
        out["analysis_issue"] = str(exc)
    return out

def analyze_rows(rows: list[dict], plan: dict) -> tuple[list[dict], list[dict], dict]:
    validate_plan(plan)
    audited = [inspect_row(row, plan) for row in rows]
    by_id, by_key = defaultdict(list), defaultdict(list)
    for i, row in enumerate(audited):
        by_id[str(row.get("command_id", ""))].append(i)
        try:
            by_key[key_of(row)].append(i)
        except (KeyError, DataError):
            pass
    for indices in list(by_id.values()) + list(by_key.values()):
        if len(indices) > 1:
            for i in indices:
                audited[i].update(analysis_status="INVALID_DUPLICATE_INPUT_ROW",
                                  analysis_issue="Duplicate command ID or planned cell/repetition; no row selected")
    cells = []
    for policy, q, ttl in itertools.product(plan["policies"], plan["queue_depths"], plan["ttl_grid_s"]):
        matched = []
        for row in audited:
            try:
                if key_of(row)[:3] == (policy, q, float(ttl)):
                    matched.append(row)
            except (KeyError, DataError):
                continue
        counts = Counter(r["analysis_status"] for r in matched)
        present_reps = {key_of(r)[3] for r in matched if key_of(r)[3] <= plan["repetitions"]}
        valid = [r for r in matched if r["analysis_status"] in GOOD]
        late = counts["LATE_PRE_SERVICE"]
        early = counts["ON_TIME_PRE_SERVICE"]
        boundary = counts["BOUNDARY_EXCLUDE_FROM_HEADLINE"]
        rejected = sum(counts[k] for k in REJECTIONS)
        bad = len(matched) - len(valid)
        missing = plan["repetitions"] - len(present_reps)
        completed = bad == 0 and missing == 0 and len(matched) == plan["repetitions"]
        cell = {"policy": policy, "queue_depth": q, "ttl_s": ttl,
                "n_planned": plan["repetitions"], "n_observed": len(matched),
                "n_valid": len(valid), "n_late": late, "n_on_time": early,
                "n_boundary": boundary, "n_rejected": rejected,
                "n_invalid_or_unresolved": bad, "n_missing": missing,
                "late_fraction_valid_executed": late / (late + early) if late + early else None,
                "complete_evidence_cell": completed,
                "late_fraction_headline": late / (late + early) if completed and late + early else None}
        for metric, column in (("lateness", "recomputed_lateness_ms"), ("post_receipt_delay", "receipt_to_pre_service_ms")):
            values = [r[column] for r in valid if r[column] is not None]
            cell.update({f"{metric}_{k}_ms" if k != "n" else f"{metric}_n": v for k, v in stats(values).items()})
        cells.append(cell)
    summary = {"rows": len(rows), "planned_rows": len(cells) * plan["repetitions"],
               "analysis_status_counts": dict(Counter(r["analysis_status"] for r in audited)),
               "complete_cells": sum(c["complete_evidence_cell"] for c in cells),
               "planned_cells": len(cells), "missing_rows": sum(c["n_missing"] for c in cells),
               "csv_consistency_errors": sum(r["analysis_status"].startswith("INVALID") for r in audited),
               "raw_event_attribution_revalidated": False,
               "independent_physical_effect_verified": False,
               "interpretation": "CSV-level consistency and descriptive sample frequencies only; no causal certification or population probability claim"}
    return audited, cells, summary

def make_plots(cells: list[dict], audited: list[dict], out: Path, plan: dict, synthetic: bool) -> None:
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    # Use library defaults; every chart is a separate figure.
    prefix = "SYNTHETIC SOFTWARE TEST — NOT EXPERIMENT DATA\n" if synthetic else ""
    for policy in plan["policies"]:
        lookup = {(c["queue_depth"], c["ttl_s"]): c for c in cells if c["policy"] == policy}
        data = np.array([[lookup[q, ttl]["late_fraction_headline"] if lookup[q, ttl]["late_fraction_headline"] is not None else np.nan
                          for ttl in plan["ttl_grid_s"]] for q in plan["queue_depths"]])
        fig, ax = plt.subplots(figsize=(8.0, 3.9))
        im = ax.imshow(np.ma.masked_invalid(data), vmin=0, vmax=1, aspect="auto")
        ax.set_xticks(range(len(plan["ttl_grid_s"])), plan["ttl_grid_s"])
        ax.set_yticks(range(len(plan["queue_depths"])), plan["queue_depths"])
        for i, q in enumerate(plan["queue_depths"]):
            for j, ttl in enumerate(plan["ttl_grid_s"]):
                c = lookup[q, ttl]
                assessable = c['n_late'] + c['n_on_time']
                ratio_label = f"{c['n_late']}/{assessable} late" if assessable else "N/A late"
                label = (f"{ratio_label}\n{c['n_boundary']} boundary"
                         if c["complete_evidence_cell"] else f"INCOMPLETE\n{c['n_valid']}/{c['n_planned']} valid")
                ax.text(j, i, label, ha="center", va="center", fontsize=8,
                        bbox={"boxstyle": "round,pad=0.2", "alpha": 0.8})
        ax.set_xlabel("Application lifetime (s)")
        ax.set_ylabel("Earlier transactions (q)")
        ax.set_title(prefix + "Pre-service lateness boundary\n" + policy, fontsize=10)
        fig.colorbar(im, ax=ax, label="Late / (on-time + late); boundary excluded")
        fig.tight_layout()
        for extension in ("png", "pdf"):
            fig.savefig(out / f"boundary_{policy}.{extension}", dpi=180)
        plt.close(fig)
        fig, ax = plt.subplots(figsize=(7.3, 3.8))
        for q in plan["queue_depths"]:
            observations = [r for r in audited if r.get("policy") == policy and r["analysis_status"] in GOOD
                            and r.get("receipt_to_pre_service_ms") is not None and integer(r["queue_depth"], "q") == q]
            x = [number(r["ttl_s"], "ttl") for r in observations]
            y = [r["receipt_to_pre_service_ms"] / 1000 for r in observations]
            if x:
                ax.scatter(x, y, label=f"q={q}", alpha=0.75, s=24)
        ax.set_xlabel("Application lifetime (s)")
        ax.set_ylabel("Receipt to pre-service observation (s)")
        ax.set_title(prefix + "Post-receipt delay (not pure queue time)", fontsize=10)
        if ax.collections:
            ax.legend()
        fig.tight_layout()
        for extension in ("png", "pdf"):
            fig.savefig(out / f"delay_{policy}.{extension}", dpi=180)
        plt.close(fig)

def run(input_path: Path, output: Path, plan_path: Path = DEFAULT_PLAN, *, synthetic: bool = False, plots: bool = True) -> dict:
    input_hash = sha256(input_path)
    plan = read_json(plan_path)
    fields, rows = read_csv(input_path)
    absent = sorted(REQUIRED - set(fields))
    if absent:
        raise DataError("Missing columns: " + ", ".join(absent))
    has_synthetic = any(r.get("fixture_only", "").lower() == "true" for r in rows)
    if has_synthetic and not synthetic:
        raise DataError("Synthetic fixtures cannot be analyzed as measured data")
    out = new_output(output, [input_path, plan_path])
    try:
        audited, cells, summary = analyze_rows(rows, plan)
        summary.update(synthetic_fixture_only=synthetic, input_sha256=input_hash,
                       plan_sha256=sha256(plan_path), **runtime())
        write_csv(out / "audited_rows.csv", audited, list(dict.fromkeys(fields + list(audited[0]) if audited else fields)))
        write_csv(out / "cell_summary.csv", cells)
        write_json(out / "analysis_plan.json", plan)
        write_json(out / "summary.json", summary)
        if plots:
            make_plots(cells, audited, out, plan, synthetic)
        if sha256(input_path) != input_hash:
            raise DataError("Input changed during analysis")
        (out / "REPORT.md").write_text(
            ("# SYNTHETIC SOFTWARE TEST — NOT RESEARCH RESULTS\n\n" if synthetic else "# Boundary analysis\n\n") +
            f"Input SHA-256: `{input_hash}`\n\n" +
            f"Complete cells: {summary['complete_cells']}/{summary['planned_cells']}. " +
            f"Missing rows: {summary['missing_rows']}. CSV consistency errors: {summary['csv_consistency_errors']}.\n\n" +
            "Lateness = pre_service_at_ms - expires_at_ms; strict +/-1000 ms comparison by plan. " +
            "Boundary values, rejections, invalid/unresolved and absent rows remain separate. " +
            "Incomplete cells are masked, never displayed as zero violations.\n\n" +
            "No raw-event attribution re-audit is performed here. This report cannot certify physical effects, " +
            "negative evidence, or the readiness of the live runner. All statistics are descriptive.\n", encoding="utf-8")
        manifest(out)
        return summary
    except Exception as exc:
        (out / "INVALID.txt").write_text(str(exc) + "\n", encoding="utf-8")
        raise

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument("--no-plots", action="store_true")
    args = parser.parse_args()
    try:
        result = run(args.input, args.output, args.plan, synthetic=args.synthetic, plots=not args.no_plots)
        print(json.dumps({k: result[k] for k in ("rows", "planned_rows", "complete_cells", "planned_cells", "missing_rows", "csv_consistency_errors")}, indent=2))
        print("Output:", args.output.resolve())
        return 0 if result["complete_cells"] == result["planned_cells"] and not result["csv_consistency_errors"] else 2
    except (DataError, OSError, ValueError) as exc:
        print(f"ANALYSIS REFUSED: {exc}")
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
