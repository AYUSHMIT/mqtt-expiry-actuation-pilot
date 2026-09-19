# Sources and implementation provenance

Accessed 19 September 2026. Source-derived behavior is separated from our new
analysis/observation design conventions in ANALYSIS_PLAN.md and OPTICAL_PROTOCOL.md.

## User repository, read-only inspection

Commit: `57db3187c3f1c8e79a40091c21ba03cc9a7aebbc`

- PHYSICAL_V3_CONTRACT.md — declared grid, repetitions, margins and current characterization.
  https://github.com/AYUSHMIT/mqtt-expiry-actuation-pilot/blob/57db3187c3f1c8e79a40091c21ba03cc9a7aebbc/PHYSICAL_V3_CONTRACT.md
- measurement_v3.py — column names and the discrepancies listed in INTEGRATION_BLOCKERS.md.
  Git blob: `729f2be032e2f02707c1d5ffdb937e7f045297b7`
- run_v3.py — candidate scaffolds and separate direct-REST characterization dispatcher.
  Git blob: `67216f2e243514f847dc3f9cb72043d72d5f1bd5`
- ha/packages/expiry_physical_v3.yaml — actual location of predictive decision in queued actions.
  Git blob: `abc40e8e0c37f8f073fbfab0d5d2c67c9ce4e3fc`

No live physical observations were made while preparing this package. No private
source data or canonical trial values have been recreated as synthetic results.

## Primary documentation

- Python 3.12, time: monotonic reference point, system-wide clock, nanosecond API.
  https://docs.python.org/3.12/library/time.html
- OpenCV 4.13, VideoCapture: read returns decoded frames; backend/property caveats.
  https://docs.opencv.org/4.13.0/d8/dfe/classcv_1_1VideoCapture.html
- OpenCV 4.13, video-I/O properties: backend-dependent timing and capture options.
  https://docs.opencv.org/4.13.0/d4/d15/group__videoio__flags__base.html
- Matplotlib, imshow: matrix visualization.
  https://matplotlib.org/stable/api/_as_gen/matplotlib.pyplot.imshow.html

No new scientific novelty claim is based on these documentation sources.
