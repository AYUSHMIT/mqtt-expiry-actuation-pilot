# Lamp-only optical observation protocol

**Not a physical-actuation timing guarantee. Not a candidate experiment.**

## Boundary that is actually observed

The camera records lamp-region pixel intensities independently of Home Assistant
and TP-Link telemetry. Each frame has host `time.monotonic_ns()` stamps before
and after `VideoCapture.read()` and a host UTC reading immediately afterward.
These timestamps describe delivery of decoded images to Python, not camera
exposure, relay closure, current onset or exact light onset.

OpenCV documents backend/driver-dependent capture behavior and property support.
Setting a small buffer or requesting 30 frames/s does not prove that old images
are absent or that capture latency is bounded. The tool records request/readback
properties, backend, measured receipt cadence and gaps; unknown sensor latency
remains unknown. It does not turn nanosecond timestamp units into nanosecond
accuracy.

Python's monotonic clock can support comparisons across processes on the same
host/boot. It must not be directly mixed with container/VM/phone monotonic clocks
or compared across reboots. Use the same Windows laptop, one continuous session,
and an explicit shared session label. That label is an operator declaration,
not cryptographic proof of clock identity. No automatic command matching is
implemented in this package.

## Privacy and hardware safety

Only use a benign lamp, with a mechanically persistent ON setting where possible.
The observer cannot identify the connected appliance and has no actuator commands.
For any unsafe condition, make the hardware safe immediately and record the
intervention afterward; evidence collection never takes priority over safety.

Select a tight lamp-only ROI. Full images appear temporarily during ROI selection
but are not saved. Capture saves grayscale ROI PNGs by default, plus per-frame
mean/p05/p95/saturation/hash. Camera captures stay in ignored local directories;
do not publish them until inspected for sensitive content. No audio is captured.

Keep the camera/lamp geometry, background, camera settings and exposure behavior
fixed. Auto-exposure, mains flicker, ambient changes, buffering and a saturated
ROI can distort edges. Manual exposure where supported is preferable, but the
tool does not pretend backend-specific settings are portable. Recalibrate before
measurement when the setup changes, preserving all earlier records.

## Tonight: camera setup, separate from plug experiments

The commands below open only the laptop's local webcam. They do not switch the
plug. Use them only when ready to point the camera at the home lamp. Use the same
PowerShell session and session label. Replace the output suffix for a new setup;
do not overwrite a prior capture/calibration.

```powershell
$python = '.\v3_analysis_optical\.venv\Scripts\python.exe'
$session = 'lamp-setup-01'
& $python -m v3_analysis_optical.camera select-roi --camera 0 --session-id $session --output v3_analysis_optical/_local/roi-01 --confirm-camera-access
```

Drag a small rectangle over the lamp/lighted surface and accept with Enter. No
full frame is saved. The selection times out rather than waiting indefinitely.
Windows camera permissions or another camera application can prevent opening;
that is a setup issue, not a research outcome. The camera index is explicit;
there is no automatic scan of other cameras.

With the lamp dark, record a labeled baseline:

```powershell
& $python -m v3_analysis_optical.camera capture --camera 0 --roi-file v3_analysis_optical/_local/roi-01/roi_choice.json --duration-s 8 --label dark --session-id $session --output v3_analysis_optical/_local/dark-01 --confirm-camera-access
```

After an explicit manual lamp setup change, record a stable illuminated baseline:

```powershell
& $python -m v3_analysis_optical.camera capture --camera 0 --roi-file v3_analysis_optical/_local/roi-01/roi_choice.json --duration-s 8 --label light --session-id $session --output v3_analysis_optical/_local/light-01 --confirm-camera-access
& $python -m v3_analysis_optical.camera calibrate --dark v3_analysis_optical/_local/dark-01 --light v3_analysis_optical/_local/light-01 --output v3_analysis_optical/_local/calibration-01.json
```

The calibration is offline and requires at least 30 frames per labeled segment.
Dark p95 must be at least 15 grayscale units below light p05. The gap is split at
one-third and two-thirds to produce low/high hysteresis thresholds. These
engineering acquisition defaults are not research findings. Do not tune them on
candidate outcomes.

A later explicitly authorized observation can record the lamp without controlling
it; begin with the lamp in the known starting state and wait for the READY message
before an external workflow starts. Record several seconds of known baseline first;
READY indicates acquisition startup, not independently confirmed lamp state:

```powershell
& $python -m v3_analysis_optical.camera capture --camera 0 --roi-file v3_analysis_optical/_local/roi-01/roi_choice.json --duration-s 600 --label measurement --session-id $session --output v3_analysis_optical/_local/observation-01 --confirm-camera-access
```

This observer does not launch the 20-cycle power characterization or any candidate
study. Camera/characterization joint run IDs and clock declarations must be recorded
before combining their records. The observation duration is not a plug service
interval. When finished, offline detection uses:

```powershell
& $python -m v3_analysis_optical.camera analyze --capture v3_analysis_optical/_local/observation-01 --calibration v3_analysis_optical/_local/calibration-01.json --output v3_analysis_optical/_output/optical-analysis-01
```

## Detection and missing data

Three consecutive strong-side frames confirm an edge. Hysteresis-band frames
reset pending confirmation; single bright flickers do not count. Initial state
establishment is not an edge. Gaps over 250 ms or missing frame indices reset the
detector to unknown, and no edge is guessed across the gap. First-support frame,
last old-side frame and confirmation frame are all retained.

On this Windows host, the frozen 20 September 2026 setup captures recorded
`GetTickCount64()` with 15.625 ms monotonic clock resolution and isolated equal
adjacent receipt timestamps. Frame indices must strictly increase; decreasing
monotonic receipts remain invalid. Equal receipts are preserved and counted in
capture-validation metadata, and create an explicit observation gap with reason
`equal_quantized_receipt_timestamp`. The detector clears its state and pending
confirmation, then uses the current frame to begin a new segment. No edge is
inferred across equality. Wall time does not repair or order these observations,
and no sub-quantum timestamps are invented. This host observation establishes
neither camera exposure latency nor physical-event timing; it is not a research
result. The brightness calibration rule and frozen defaults remain unchanged.

The interval between last dark-side host receipt and first bright-side host
receipt brackets observed images only. Unknown capture buffering means it is not
a certified physical-effect interval. Repeated identical frame hashes can mean a
static scene or stale buffering; they are logged, not silently discarded.

A failed/stalled capture preserves partial observations and INVALID.txt. Partial
analysis requires explicit `--allow-partial` and remains marked partial. A valid
capture can still contain optical gaps; the summary reports them. Zero detections
never become a proof of zero device activity.

## What is still required for an effect-deadline claim

Before using camera observations in a paper's physical-deadline result, establish
an appropriate capture-latency/synchronization bound and a command-to-observation
association rule. Do not invent optical command IDs from nearest timestamps.
Until then, report **independent optical observations with host-receipt timing**,
not exact or independently verified electrical actuation timestamps.
