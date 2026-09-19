# V3 offline analysis + optical observer

**Version 0.1.0 — 19 September 2026. Additive tools, not a live experiment runner.**

Prepared against `AYUSHMIT/mqtt-expiry-actuation-pilot` at
`57db3187c3f1c8e79a40091c21ba03cc9a7aebbc` on `physical-v3-hardening`.
No existing repository file or frozen evidence needs to change to use this folder.
Nothing here publishes MQTT messages, calls Home Assistant, or operates a plug.
The camera opens only through the explicit opt-in camera commands.

## Do this in VS Code today — PowerShell

From the existing experiment repository, after installing this folder:

```powershell
py -3 -m unittest discover -s v3_analysis_optical/tests -v
py -3 -m v3_analysis_optical.demo --output v3_analysis_optical/_output/selftest-01 --no-plots
```

This needs no camera, lamp, hotspot, Home Assistant token, Docker, or network. The
image-library tests skip when optional dependencies are absent; check the report
for skips. All demo input/output is explicitly **synthetic test data**, not v3
results. Existing output directories are never overwritten; use a new suffix on
later software self-tests.

For plots and eventual camera capture, use a **separate environment**:

```powershell
py -3 -m venv v3_analysis_optical/.venv
& ./v3_analysis_optical/.venv/Scripts/python.exe -m pip install -r v3_analysis_optical/requirements-tools.txt
& ./v3_analysis_optical/.venv/Scripts/python.exe -m unittest discover -s v3_analysis_optical/tests -v
& ./v3_analysis_optical/.venv/Scripts/python.exe -m v3_analysis_optical.demo --output v3_analysis_optical/_output/selftest-plots-01
```

Do not change the existing experiment `requirements.txt`. No activation script,
Bash heredoc, or global package upgrade is needed. Only installing dependencies
requires Internet access; the optical observer uses the local webcam, not Wi-Fi.

## What is implemented

| Tool | Input | Output / meaning |
|---|---|---|
| `boundary.py` | Future v3 trial CSV + frozen analysis plan | Row consistency audit; cell counts; missing/invalid/boundary reporting; separate boundary heatmap and post-receipt-delay plot |
| `camera.py select-roi` | Explicit local camera access | User-selected lamp-only ROI file; full camera frame is displayed but not saved |
| `camera.py capture` | Fixed ROI; local camera | Every received ROI frame by default, brightness CSV, host timestamps, acquisition metadata, hashes |
| `camera.py calibrate` | Separately labeled dark/light captures | Offline fixed hysteresis thresholds; rejects inadequate contrast |
| `camera.py analyze` | Measurement capture + frozen calibration | Initial states, confirmed light/dark edges, gaps, first-support and confirmation timestamps |
| `demo.py` | Generated synthetic fixtures only | End-to-end software exercise without hardware/network |

## Read before live work

1. `INTEGRATION_BLOCKERS.md`: the committed candidate runner/classifier is **not
   ready**. This package does not silently fix it.
2. `ANALYSIS_PLAN.md`: denominators, boundary exclusions, input schema and checks.
3. `OPTICAL_PROTOCOL.md`: calibration, privacy, sensor buffering and timing limits.

The optical observer is a separate sensor channel. It does **not** yet assign
optical transitions to command IDs, verify a physical deadline, or modify
`power_characterization.py`. A record of zero detected optical transitions is
not proof that no physical transition occurred.

## Files and Git

`_local/` is for camera captures/calibration; `_output/` for generated analysis;
`.venv/` for optional dependencies. These are ignored by this folder's own
`.gitignore`, without modifying the repository-wide ignore file. Do not publish
camera captures automatically. Review ROI privacy first.

When tests and the protocol review are complete, this entire additive folder can
be staged in a separate commit. A source commit freezes software, **not** unknown
camera exposure/buffering, an uncalibrated lamp threshold, or unrun v3 results.

## Current validation limits

Automated checks were run in an isolated Linux/Python 3.13 environment with
synthetic evidence and images. Python 3.12-compatible syntax is used, but the
installer, DirectShow/Media Foundation paths, real Windows webcam, lamp, and
physical experiment have not been exercised in that environment. Use the local
software tests first; perform camera calibration at home before live measurement.
