"""Explicit synthetic software self-test. Not a simulation of measured device behavior."""
from __future__ import annotations
import argparse
import csv
from pathlib import Path
from .common import new_output, write_csv, write_json, read_json, manifest
from .boundary import DEFAULT_PLAN, analyze_rows, run, classify_time
from .optical_core import FRAME_FIELDS, calibrate, analyze_capture


def fixture_rows() -> list[dict]:
    plan = read_json(DEFAULT_PLAN)
    rows = []
    for q in plan["queue_depths"]:
        for ttl in plan["ttl_grid_s"]:
            for rep in range(1, 6):
                received = 1_700_000_000_000 + len(rows) * 60000
                deadline = received + ttl * 1000
                pre = received + q * 6000 + (rep - 3) * 20 + 100
                delta = pre - deadline
                rows.append({"fixture_only": "true", "command_id": f"SYNTHETIC-{q}-{ttl}-{rep}",
                    "policy": "physical_v3_broker_only", "queue_depth": q, "ttl_s": ttl, "rep": rep,
                    "expires_at_ms": deadline, "receipt_count": 1, "received_at_ms": received,
                    "pre_service_count": 1, "pre_service_at_ms": pre,
                    "pre_service_lateness_ms": delta, "endpoint_on_transition_count": 1,
                    "endpoint_on_at_ms": pre+300, "rejected_count": 0,
                    "unmatched_endpoint_on_count": 0, "unmatched_endpoint_off_count": 0,
                    "outcome": classify_time(delta, 1000)})
    return rows


def optical_fixture(directory: Path, label: str, values: list[float], *, gap_at: int | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=False)
    meta = {"schema": "HOST-OPTICAL-CAPTURE-1", "status": "COMPLETE",
            "label": label, "session_id": "SYNTHETIC-TEST-SESSION", "synthetic_fixture_only": True,
            "camera_index": 0, "backend_actual": "SYNTHETIC", "frame_width": 640,
            "frame_height": 480, "roi": [0,0,16,16], "host_fingerprint": "SYNTHETIC-HOST"}
    write_json(directory / "capture.json", meta)
    rows = []
    offset = 0
    for i, value in enumerate(values):
        if i == gap_at:
            offset += 1_000_000_000
        ts = 10_000_000_000 + i * 33_333_333 + offset
        rows.append({"frame_index": i, "read_start_monotonic_ns": ts-1_000_000,
                     "read_end_monotonic_ns": ts, "read_end_wall_ns": 1_700_000_000_000_000_000+ts,
                     "roi_mean": value, "roi_p05": value, "roi_p95": value,
                     "saturation_fraction": 0, "roi_sha256": "SYNTHETIC-NO-CAMERA", "roi_file": ""})
    write_csv(directory / "frames.csv", rows, FRAME_FIELDS)
    return directory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--no-plots", action="store_true")
    args = parser.parse_args()
    out = new_output(args.output)
    inputs = out / "synthetic_inputs"
    inputs.mkdir()
    csv_path = inputs / "SYNTHETIC_boundary.csv"
    write_csv(csv_path, fixture_rows())
    boundary = run(csv_path, out / "SYNTHETIC_boundary_analysis", synthetic=True, plots=not args.no_plots)
    dark = optical_fixture(inputs / "dark", "dark", [20.0]*60)
    light = optical_fixture(inputs / "light", "light", [180.0]*60)
    measure = optical_fixture(inputs / "measurement", "measurement", [20.0]*30+[180.0]*30+[20.0]*30)
    calibration_path = out / "SYNTHETIC_calibration.json"
    calibrate(dark, light, calibration_path)
    # Input paths and calibration live separately from this new output directory.
    optical = analyze_capture(measure, calibration_path, out / "SYNTHETIC_optical_analysis")
    write_json(out / "DEMO_STATUS.json", {"synthetic_fixture_only": True,
        "physical_device_accessed": False, "camera_opened": False,
        "boundary_complete_cells": boundary["complete_cells"],
        "optical_dark_to_light": optical["observed_dark_to_light"],
        "optical_light_to_dark": optical["observed_light_to_dark"]})
    manifest(out)
    print("SYNTHETIC SELF-TEST ONLY — no experiment, camera, network, or plug access.")
    print("Boundary cells:", boundary["complete_cells"], "/", boundary["planned_cells"])
    print("Synthetic optical edges:", optical["observed_dark_to_light"], "ON /", optical["observed_light_to_dark"], "OFF")
    print("Output:", out)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
