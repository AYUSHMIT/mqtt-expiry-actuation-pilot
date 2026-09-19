"""Explicitly opt-in, read-only local camera capture. NEVER controls a plug.

Capture is isolated in a spawned process so a stuck camera read can be stopped.
Only an operator-selected grayscale ROI is saved. No full-frame video or audio.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import math
import multiprocessing as mp
import os
from pathlib import Path
import platform
import queue
import time
from .common import DataError, write_json, read_json, manifest, new_output, runtime, number
from .optical_core import FRAME_FIELDS, calibrate, analyze_capture


def parse_roi(value: str) -> tuple[int, int, int, int]:
    try:
        vals = tuple(int(x) for x in value.split(","))
        if len(vals) != 4 or min(vals[:2]) < 0 or min(vals[2:]) <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        raise argparse.ArgumentTypeError("ROI must be x,y,width,height; positive size") from None
    return vals


def roi_pixels(frame, roi):
    x, y, w, h = roi
    height, width = frame.shape[:2]
    if x + w > width or y + h > height:
        raise DataError(f"ROI {roi} exceeds actual frame size {width}x{height}")
    return frame[y:y+h, x:x+w]


def _worker(options: dict, output: str, messages) -> None:
    """Worker imports OpenCV lazily. Parent supplies hard wall-clock watchdog."""
    out = Path(output)
    meta = {"schema": "HOST-OPTICAL-CAPTURE-1", "status": "STARTING",
            "camera_index": options["camera"], "backend_requested": options["backend"],
            "roi": list(options.get("roi") or ()), "label": options["label"], "session_id": options["session_id"],
            "synthetic_fixture_only": False, "candidate_experiment": False,
            "actuator_control": False, "full_frames_saved": False, "audio_recorded": False,
            "raw_roi_frames_saved": not options["no_save_roi"],
            "host_fingerprint": hashlib.sha256(platform.node().encode()).hexdigest(),
            "clock": vars(time.get_clock_info("monotonic")),
            "timestamp_basis": "time.monotonic_ns at read return; same-host session only",
            "camera_exposure_time_known": False, "camera_buffer_latency_bound_ms": None,
            "frame_age_known": False, "requested_fps": options["fps"],
            "warmup_s": options["warmup_s"], "requested_duration_s": options["duration_s"], **runtime()}
    cap = None
    try:
        import cv2
        import numpy as np
        meta["opencv_version"] = cv2.__version__
        meta["numpy_version"] = np.__version__
        backend = {"auto": cv2.CAP_ANY, "dshow": cv2.CAP_DSHOW, "msmf": cv2.CAP_MSMF}[options["backend"]]
        cap = cv2.VideoCapture(options["camera"], backend)
        if not cap.isOpened():
            raise DataError("Camera did not open. Check Windows camera permission and camera use by other apps")
        meta["backend_actual"] = cap.getBackendName()
        def read_property(prop):
            value = cap.get(prop)
            return float(value) if math.isfinite(value) else None
        attempts = {}
        for name, value in (("CAP_PROP_FRAME_WIDTH", options["width"]), ("CAP_PROP_FRAME_HEIGHT", options["height"]),
                            ("CAP_PROP_FPS", options["fps"]), ("CAP_PROP_BUFFERSIZE", 1)):
            prop = getattr(cv2, name)
            attempts[name] = {"requested": value, "set_returned": bool(cap.set(prop, value)), "readback": read_property(prop)}
        meta["property_attempts"] = attempts
        meta["property_caveat"] = "Readbacks/set success are not proof of hardware settings or buffering"
        meta["camera_properties"] = {name: read_property(getattr(cv2, name)) for name in
            ("CAP_PROP_FPS", "CAP_PROP_EXPOSURE", "CAP_PROP_AUTO_EXPOSURE", "CAP_PROP_GAIN", "CAP_PROP_AUTO_WB", "CAP_PROP_AUTOFOCUS")}
        messages.put(("opened", time.monotonic_ns()))
        warmup_end = time.monotonic() + options["warmup_s"]
        while time.monotonic() < warmup_end:
            ok, _ = cap.read()
            if not ok:
                raise DataError("Camera failed during warmup")
            messages.put(("heartbeat", time.monotonic_ns()))
        if options.get("select_roi"):
            ok, frame = cap.read()
            if not ok or frame is None:
                raise DataError("No frame available for ROI selection")
            messages.put(("selecting", time.monotonic_ns()))
            chosen = tuple(int(v) for v in cv2.selectROI(
                "Select lamp only; ENTER accepts; ESC cancels (no frame saved)",
                frame, showCrosshair=True, fromCenter=False))
            cv2.destroyAllWindows()
            if chosen[2] == 0 or chosen[3] == 0:
                raise DataError("ROI selection cancelled")
            meta.update(status="COMPLETE", roi=list(chosen), frame_width=frame.shape[1],
                        frame_height=frame.shape[0], selection_only=True,
                        raw_roi_frames_saved=False)
            write_json(out / "capture.json", meta)
            write_json(out / "roi_choice.json", {"roi": list(chosen),
                "camera": options["camera"], "width": frame.shape[1], "height": frame.shape[0],
                "backend": options["backend"], "session_id": options["session_id"]})
            messages.put(("complete", time.monotonic_ns()))
            return
        crops = out / "roi_frames"
        if not options["no_save_roi"]:
            crops.mkdir()
        start = time.monotonic()
        meta["started_monotonic_ns"] = time.monotonic_ns()
        meta["started_wall_ns"] = time.time_ns()
        meta["status"] = "RECORDING"
        write_json(out / "capture.json", meta)
        messages.put(("ready", time.monotonic_ns()))
        count, previous_end, max_gap, max_read, identical, previous_hash = 0, None, 0.0, 0.0, 0, None
        with (out / "frames.csv").open("x", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FRAME_FIELDS)
            writer.writeheader()
            while time.monotonic() - start < options["duration_s"]:
                before = time.monotonic_ns()
                ok, frame = cap.read()
                after = time.monotonic_ns()
                wall = time.time_ns()
                if not ok or frame is None or not frame.size:
                    raise DataError("Missing camera frame; raw observations retained, no retry")
                height, width = frame.shape[:2]
                if "frame_width" in meta and (width != meta["frame_width"] or height != meta["frame_height"]):
                    raise DataError("Camera frame dimensions changed mid-capture")
                meta["frame_width"], meta["frame_height"] = width, height
                cropped = roi_pixels(frame, options["roi"])
                gray = cv2.cvtColor(cropped, cv2.COLOR_BGR2GRAY) if cropped.ndim == 3 else cropped
                content_hash = hashlib.sha256(gray.tobytes()).hexdigest()
                identical += int(content_hash == previous_hash)
                previous_hash = content_hash
                filename = ""
                if not options["no_save_roi"]:
                    filename = f"roi_frames/frame-{count:06d}.png"
                    success, buffer = cv2.imencode(".png", gray)
                    if not success:
                        raise DataError("Failed to encode ROI frame")
                    (out / filename).write_bytes(buffer.tobytes())
                writer.writerow({"frame_index": count, "read_start_monotonic_ns": before,
                                 "read_end_monotonic_ns": after, "read_end_wall_ns": wall,
                                 "roi_mean": float(np.mean(gray)), "roi_p05": float(np.percentile(gray, 5)),
                                 "roi_p95": float(np.percentile(gray, 95)),
                                 "saturation_fraction": float(np.mean(gray >= 254)),
                                 "roi_sha256": content_hash, "roi_file": filename})
                handle.flush()
                if previous_end is not None:
                    max_gap = max(max_gap, (after - previous_end) / 1e6)
                max_read = max(max_read, (after - before) / 1e6)
                previous_end = after
                count += 1
                messages.put(("heartbeat", after))
        meta.update(status="COMPLETE", received_frames=count,
                    received_fps=count / max(time.monotonic() - start, .001),
                    max_observed_frame_gap_ms=max_gap, max_read_call_ms=max_read,
                    identical_consecutive_roi_frames=identical,
                    identical_frames_interpretation="Can be a static scene or buffered content; not enough to prove freshness",
                    ended_monotonic_ns=time.monotonic_ns(), ended_wall_ns=time.time_ns())
        write_json(out / "capture.json", meta)
        messages.put(("complete", time.monotonic_ns()))
    except BaseException as exc:
        meta.update(status="INVALID", error=f"{type(exc).__name__}: {exc}")
        write_json(out / "capture.json", meta)
        (out / "INVALID.txt").write_text(meta["error"] + "\n", encoding="utf-8")
        messages.put(("invalid", time.monotonic_ns()))
    finally:
        if cap is not None:
            cap.release()


def capture(options: dict, output: Path) -> dict:
    if not options.get("confirm_camera_access"):
        raise DataError("Camera access requires --confirm-camera-access; no camera opened")
    for name in ("duration_s", "warmup_s", "fps"):
        options[name] = number(options[name], name)
    if options["duration_s"] <= 0 or options["duration_s"] > 1800 or options["warmup_s"] < 0 or options["warmup_s"] > 30:
        raise DataError("Duration must be 0 < duration <= 1800 s; warmup 0..30 s")
    if options["camera"] < 0 or options["width"] <= 0 or options["height"] <= 0 or not 1 <= options["fps"] <= 120:
        raise DataError("Invalid camera index/resolution/frame-rate request")
    if not options["session_id"].strip():
        raise DataError("Explicit same-host acquisition session label is required")
    out = new_output(output)
    write_json(out / "capture.json", {"schema": "HOST-OPTICAL-CAPTURE-1", "status": "STARTING", "options": options})
    ctx = mp.get_context("spawn")
    messages = ctx.Queue()
    process = ctx.Process(target=_worker, args=(options, str(out), messages))
    process.start()
    last_activity = time.monotonic()
    began = last_activity
    opened = False
    selecting = False
    terminal = None
    try:
        while process.is_alive() and terminal is None:
            try:
                event, _ = messages.get(timeout=.2)
                last_activity = time.monotonic()
                if event == "opened":
                    opened = True
                if event == "selecting":
                    selecting = True
                    print("Select the lamp-only rectangle in the camera window; ENTER accepts.", flush=True)
                if event == "ready":
                    print("OPTICAL OBSERVER READY. No device commands are issued.", flush=True)
                if event in ("complete", "invalid"):
                    terminal = event
            except queue.Empty:
                pass
            timeout = 180 if selecting else 5 if opened else 20
            hard_limit = 210 if options.get("select_roi") else options["duration_s"] + options["warmup_s"] + 30
            if time.monotonic() - last_activity > timeout or time.monotonic() - began > hard_limit:
                raise DataError("Camera worker stalled; capture stopped by watchdog")
        process.join(timeout=3)
        if process.is_alive():
            raise DataError("Camera worker did not release after completion")
        meta = read_json(out / "capture.json")
        if process.exitcode != 0:
            raise DataError(f"Camera worker exited with status {process.exitcode}")
        if meta.get("status") != "COMPLETE":
            raise DataError(meta.get("error", "Camera worker ended without a complete capture"))
    except (BaseException,) as exc:
        if process.is_alive():
            process.terminate()
            process.join(timeout=3)
        meta = read_json(out / "capture.json")
        meta.update(status="INVALID", error=f"{type(exc).__name__}: {exc}")
        write_json(out / "capture.json", meta)
        (out / "INVALID.txt").write_text(meta["error"] + "\nPartial observations retained. No actuator was controlled.\n", encoding="utf-8")
    finally:
        messages.close()
        manifest(out)
    return read_json(out / "capture.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    cap = sub.add_parser("capture", help="Opt-in local webcam recording; NEVER actuates devices")
    cap.add_argument("--camera", type=int, default=0)
    cap.add_argument("--backend", choices=("auto", "dshow", "msmf"), default="auto")
    roi_group = cap.add_mutually_exclusive_group(required=True)
    roi_group.add_argument("--roi", type=parse_roi)
    roi_group.add_argument("--roi-file", type=Path, help="roi_choice.json from select-roi")
    cap.add_argument("--width", type=int, default=640)
    cap.add_argument("--height", type=int, default=480)
    cap.add_argument("--fps", type=int, default=30)
    cap.add_argument("--warmup-s", type=float, default=2.0)
    cap.add_argument("--duration-s", type=float, required=True)
    cap.add_argument("--label", choices=("dark", "light", "measurement"), required=True)
    cap.add_argument("--session-id", required=True)
    cap.add_argument("--output", type=Path, required=True)
    cap.add_argument("--no-save-roi", action="store_true", help="Brightness-only capture; weaker reanalysis evidence")
    cap.add_argument("--confirm-camera-access", action="store_true")
    select = sub.add_parser("select-roi", help="Opt-in camera view to pick a lamp-only ROI; no full frame is saved")
    select.add_argument("--camera", type=int, default=0)
    select.add_argument("--backend", choices=("auto", "dshow", "msmf"), default="auto")
    select.add_argument("--session-id", required=True)
    select.add_argument("--output", type=Path, required=True)
    select.add_argument("--confirm-camera-access", action="store_true")
    cal = sub.add_parser("calibrate", help="OFFLINE threshold calibration, no camera access")
    cal.add_argument("--dark", type=Path, required=True)
    cal.add_argument("--light", type=Path, required=True)
    cal.add_argument("--output", type=Path, required=True)
    cal.add_argument("--min-contrast", type=float, default=15)
    cal.add_argument("--debounce-frames", type=int, default=3)
    cal.add_argument("--max-gap-ms", type=float, default=250)
    ana = sub.add_parser("analyze", help="OFFLINE detection from frozen captures, no camera access")
    ana.add_argument("--capture", type=Path, required=True)
    ana.add_argument("--calibration", type=Path, required=True)
    ana.add_argument("--output", type=Path, required=True)
    ana.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    try:
        if args.mode == "capture":
            options = vars(args).copy()
            output = options.pop("output")
            options.pop("mode")
            roi_file = options.pop("roi_file")
            if roi_file is not None:
                selected = read_json(roi_file)
                if selected.get("session_id") != options["session_id"] or selected.get("camera") != options["camera"]:
                    raise DataError("ROI selection session/camera differs")
                if selected.get("backend") != options["backend"]:
                    raise DataError("Use the same requested backend as ROI selection")
                options["roi"] = parse_roi(",".join(str(x) for x in selected["roi"]))
                options["width"], options["height"] = selected["width"], selected["height"]
            result = capture(options, output)
        elif args.mode == "select-roi":
            options = dict(camera=args.camera, backend=args.backend, session_id=args.session_id,
                           roi=None, width=640, height=480, fps=30, duration_s=120,
                           warmup_s=2, label="roi_selection", no_save_roi=True,
                           confirm_camera_access=args.confirm_camera_access, select_roi=True)
            result = capture(options, args.output)
        elif args.mode == "calibrate":
            result = calibrate(args.dark, args.light, args.output, min_contrast=args.min_contrast,
                               debounce_frames=args.debounce_frames, max_gap_ms=args.max_gap_ms)
        else:
            result = analyze_capture(args.capture, args.calibration, args.output, allow_partial=args.allow_partial)
        print(json.dumps(result, indent=2))
        return 2 if result.get("status") == "INVALID" else 0
    except (DataError, OSError, ValueError) as exc:
        print("OPTICAL TOOL REFUSED:", exc)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
