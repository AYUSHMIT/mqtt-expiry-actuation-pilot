"""Pure optical thresholding over HOST-RECEIVED frame observations.

No camera, HA, MQTT or actuator calls. Hysteresis is calibrated separately.
No detected timestamp is an exact exposure, relay, or electrical-effect time.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
import math
from pathlib import Path
from .common import DataError, number, integer, read_csv, read_json, write_csv, write_json, sha256, manifest, new_output, runtime

FRAME_FIELDS = ["frame_index", "read_start_monotonic_ns", "read_end_monotonic_ns", "read_end_wall_ns",
                "roi_mean", "roi_p05", "roi_p95", "saturation_fraction", "roi_sha256", "roi_file"]

def exact_int(value, name: str) -> int:
    if isinstance(value, bool):
        raise DataError(f"{name}: boolean is not an integer timestamp")
    try:
        if isinstance(value, float) or str(value).strip() == "":
            raise ValueError()
        result = int(value)
    except (ValueError, TypeError) as exc:
        raise DataError(f"{name}: expected exact integer") from exc
    if result < 0:
        raise DataError(f"{name}: negative")
    return result

@dataclass(frozen=True)
class DetectorSettings:
    low: float
    high: float
    debounce_frames: int = 3
    max_gap_ms: float = 250.0

    def validate(self) -> None:
        if not (math.isfinite(self.low) and math.isfinite(self.high) and 0 <= self.low < self.high <= 255):
            raise DataError("Require 0 <= low < high <= 255")
        if type(self.debounce_frames) is not int or self.debounce_frames < 2:
            raise DataError("debounce_frames must be an integer >= 2")
        if not math.isfinite(self.max_gap_ms) or self.max_gap_ms <= 0:
            raise DataError("max_gap_ms must be positive and finite")

class Detector:
    """Consecutive-frame confirmation; gaps force reinitialization, not guessed edges."""
    def __init__(self, settings: DetectorSettings):
        settings.validate()
        self.settings = settings
        self.state = None
        self.pending_side = None
        self.pending_frames: list[dict] = []
        self.last_definite_old = None
        self.previous = None
        self.segment = 0

    def feed(self, row: dict) -> list[dict]:
        row = dict(row)
        for name in ("frame_index", "read_start_monotonic_ns", "read_end_monotonic_ns", "read_end_wall_ns"):
            row[name] = exact_int(row[name], name)
        value = number(row["roi_mean"], "roi_mean")
        if not 0 <= value <= 255 or row["read_start_monotonic_ns"] > row["read_end_monotonic_ns"]:
            raise DataError("Bad frame intensity/read interval")
        result = []
        if self.previous:
            if row["frame_index"] <= self.previous["frame_index"] or row["read_end_monotonic_ns"] <= self.previous["read_end_monotonic_ns"]:
                raise DataError("Frame indices and receipt timestamps must increase strictly")
            gap = (row["read_end_monotonic_ns"] - self.previous["read_end_monotonic_ns"]) / 1e6
            if row["frame_index"] != self.previous["frame_index"] + 1 or gap > self.settings.max_gap_ms:
                self.segment += 1
                result.append({"kind": "optical_observation_gap", "segment": self.segment,
                               "previous_frame": self.previous["frame_index"], "frame_index": row["frame_index"],
                               "gap_ms": gap, "edge_across_gap": "UNRESOLVED_NOT_INFERRED"})
                self.state, self.last_definite_old, self.pending_side, self.pending_frames = None, None, None, []
        self.previous = row
        side = "dark" if value <= self.settings.low else "light" if value >= self.settings.high else None
        if side is None:
            self.pending_side, self.pending_frames = None, []
            return result
        if side == self.state:
            self.pending_side, self.pending_frames = None, []
            self.last_definite_old = row
            return result
        if side != self.pending_side:
            self.pending_side, self.pending_frames = side, [row]
        else:
            self.pending_frames.append(row)
        if len(self.pending_frames) < self.settings.debounce_frames:
            return result
        first = self.pending_frames[0]
        transition = {"kind": "optical_initial_state" if self.state is None else "optical_transition",
                      "segment": self.segment, "from_state": self.state, "to_state": side,
                      "last_old_side_frame": self.last_definite_old["frame_index"] if self.last_definite_old else None,
                      "last_old_side_receipt_ns": self.last_definite_old["read_end_monotonic_ns"] if self.last_definite_old else None,
                      "first_support_frame": first["frame_index"],
                      "first_support_read_start_ns": first["read_start_monotonic_ns"],
                      "first_support_receipt_ns": first["read_end_monotonic_ns"],
                      "first_support_wall_ns": first["read_end_wall_ns"],
                      "confirmation_frame": row["frame_index"],
                      "confirmation_receipt_ns": row["read_end_monotonic_ns"],
                      "physical_event_time_bound_available": False,
                      "physical_event_time_ns": None,
                      "command_attribution": "NOT_PERFORMED"}
        result.append(transition)
        self.state, self.last_definite_old, self.pending_side, self.pending_frames = side, row, None, []
        return result

def geometry(meta: dict) -> dict:
    keys = ("camera_index", "backend_actual", "frame_width", "frame_height", "roi", "host_fingerprint")
    if any(k not in meta for k in keys):
        raise DataError("Capture metadata lacks camera/ROI/host identity")
    return {k: meta[k] for k in keys}

def load_capture(directory: Path) -> tuple[dict, list[dict]]:
    meta = read_json(directory / "capture.json")
    if meta.get("schema") != "HOST-OPTICAL-CAPTURE-1":
        raise DataError("Unsupported optical capture schema")
    header, rows = read_csv(directory / "frames.csv")
    if not set(FRAME_FIELDS) <= set(header):
        raise DataError("Incomplete frame CSV schema")
    previous = None
    for row in rows:
        ix = exact_int(row["frame_index"], "frame_index")
        ts = exact_int(row["read_end_monotonic_ns"], "frame receipt")
        if previous and (ix <= previous[0] or ts <= previous[1]):
            raise DataError("Non-increasing capture indices/timestamps")
        previous = ix, ts
        for key in ("roi_mean", "roi_p05", "roi_p95"):
            if not 0 <= number(row[key], key) <= 255:
                raise DataError("Invalid brightness")
    geometry(meta)
    return meta, rows

def percentile_nearest_rank(values: list[float], p: float) -> float:
    if not values:
        raise DataError("Empty calibration segment")
    values = sorted(values)
    return values[max(0, math.ceil(p * len(values)) - 1)]

def calibrate(dark: Path, light: Path, output: Path, *, min_contrast: float = 15.0,
              debounce_frames: int = 3, max_gap_ms: float = 250.0) -> dict:
    if output.exists():
        raise DataError("Refusing to replace a frozen calibration")
    dm, dr = load_capture(dark)
    lm, lr = load_capture(light)
    if dm.get("label") != "dark" or lm.get("label") != "light":
        raise DataError("Calibration requires explicitly labeled dark and light captures")
    if dm.get("status") != "COMPLETE" or lm.get("status") != "COMPLETE":
        raise DataError("Calibration captures must be complete")
    if geometry(dm) != geometry(lm) or dm.get("session_id") != lm.get("session_id"):
        raise DataError("Calibration camera/ROI/session changed")
    if dm.get("synthetic_fixture_only") != lm.get("synthetic_fixture_only"):
        raise DataError("Cannot mix synthetic and real calibration")
    if len(dr) < 30 or len(lr) < 30:
        raise DataError("Need at least 30 received frames in each calibration segment")
    d = [number(r["roi_mean"], "dark mean") for r in dr]
    l = [number(r["roi_mean"], "light mean") for r in lr]
    du, ll = percentile_nearest_rank(d, .95), percentile_nearest_rank(l, .05)
    min_contrast = number(min_contrast, "minimum contrast")
    if min_contrast <= 0 or ll - du < min_contrast:
        raise DataError("Insufficient dark/light separation; no threshold selected")
    settings = DetectorSettings(du + (ll - du) / 3, du + 2 * (ll - du) / 3,
                                debounce_frames, max_gap_ms)
    settings.validate()
    calibration = {"schema": "HOST-OPTICAL-CALIBRATION-1", "settings": asdict(settings),
                   "geometry": geometry(dm), "session_id": dm["session_id"],
                   "synthetic_fixture_only": bool(dm.get("synthetic_fixture_only")),
                   "dark_p95": du, "light_p05": ll, "minimum_contrast": min_contrast,
                   "source_hashes": {"dark_frames": sha256(dark / "frames.csv"), "light_frames": sha256(light / "frames.csv"),
                                     "dark_metadata": sha256(dark / "capture.json"), "light_metadata": sha256(light / "capture.json")},
                   "physical_timing_calibrated": False, "camera_buffer_latency_bound_ms": None,
                   "threshold_rule": "dark p95/light p05 gap split at one-third and two-thirds; frozen before measurement",
                   "operator_requirements": "Keep ROI, placement, lamp, background and camera settings fixed; verify exposure behavior separately",
                   **runtime()}
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, calibration)
    return calibration

def analyze_capture(capture: Path, calibration_path: Path, output: Path, *, allow_partial: bool = False) -> dict:
    meta, rows = load_capture(capture)
    calibration = read_json(calibration_path)
    if calibration.get("schema") != "HOST-OPTICAL-CALIBRATION-1":
        raise DataError("Unsupported calibration")
    if calibration["geometry"] != geometry(meta) or calibration["session_id"] != meta.get("session_id"):
        raise DataError("Camera geometry/session differs from frozen calibration")
    if calibration["synthetic_fixture_only"] != bool(meta.get("synthetic_fixture_only")):
        raise DataError("Synthetic/real source mismatch")
    if meta.get("label") != "measurement":
        raise DataError("Analyze an explicitly labeled measurement capture, not a calibration segment")
    if meta.get("status") != "COMPLETE" and not allow_partial:
        raise DataError("Capture incomplete; use --allow-partial for an explicitly qualified report")
    if not rows:
        raise DataError("No frames received")
    detector = Detector(DetectorSettings(**calibration["settings"]))
    events = [e for row in rows for e in detector.feed(row)]
    edges = [e for e in events if e["kind"] == "optical_transition"]
    out = new_output(output, [capture, calibration_path])
    import json
    (out / "optical_events.jsonl").write_text("".join(json.dumps(e, allow_nan=False) + "\n" for e in events), encoding="utf-8")
    write_csv(out / "optical_transitions.csv", edges, list(edges[0]) if edges else ["kind", "to_state", "first_support_receipt_ns", "confirmation_receipt_ns"])
    result = {"schema": "HOST-OPTICAL-ANALYSIS-1", "received_frames": len(rows),
              "observed_dark_to_light": sum(e["to_state"] == "light" for e in edges),
              "observed_light_to_dark": sum(e["to_state"] == "dark" for e in edges),
              "observation_gaps": sum(e["kind"] == "optical_observation_gap" for e in events),
              "partial_capture": meta.get("status") != "COMPLETE",
              "frame_side_brackets_are_physical_time_bounds": False,
              "independent_optical_channel_recorded": not bool(meta.get("synthetic_fixture_only")),
              "independent_physical_effect_deadline_verified": False,
              "command_attribution_performed": False, "candidate_experiment": False,
              "session_id": meta["session_id"], "synthetic_fixture_only": bool(meta.get("synthetic_fixture_only")),
              "capture_csv_sha256": sha256(capture / "frames.csv"), "calibration_sha256": sha256(calibration_path),
              "timestamp_meaning": "Host frame receipt, not camera exposure or contact closure; buffer latency unknown",
              **runtime()}
    write_json(out / "summary.json", result)
    (out / "REPORT.md").write_text(
        "# Optical observation report\n\n" + ("**SYNTHETIC TEST ONLY.**\n\n" if result["synthetic_fixture_only"] else "") +
        f"Observed transitions: {len(edges)}. Gaps: {result['observation_gaps']}.\n\n" +
        "Initialization after start/gaps is not a transition. No command IDs are assigned. " +
        "First-support and confirmation times are retained separately. Host receipt intervals bracket " +
        "observed frame changes, NOT the physical illumination instant. Unknown camera buffering/" +
        "exposure delay prevents a physical deadline claim. Zero detections do not prove zero physical activity.\n", encoding="utf-8")
    manifest(out)
    return result
