# Build validation — 19 September 2026

## Completed here

- 52 new unittest cases passed; 0 skipped in the build environment.
- The tests use synthetic CSV/event/frame data and mocked camera objects. No
  physical device, Home Assistant instance, MQTT broker or real webcam was accessed.
- A 75-row synthetic boundary input exercised all 15 planned cells.
- Independent synthetic dark/light calibration followed by measurement detection
  produced one dark-to-light and one light-to-dark observation.
- The demo wrote PNG/PDF charts; a generated PDF was rendered and visually checked.
- Python 3.12 grammar parsing passed for all tool source files.
- Source whitespace checks passed.
- Archive member hashes were checked after ZIP creation.

Exact test invocation:

```
python -m unittest discover -s v3_analysis_optical/tests -v
```

Build runtime: Linux, CPython 3.13.5, numpy 2.3.5, matplotlib 3.10.8,
opencv-python 4.13.0.92. See BUILD_TEST_LOG.txt for the new test suite output.
The existing repository's 85 tests were not rerun in this isolated package build.
No assertion is made that the real Windows camera or the PowerShell installer was
executed here. The installer requires local review and use on the named branch.

## Not completed or claimed

- No characterization or candidate trial was run.
- No live camera latency, exposure, frame freshness or dark/light setup was calibrated.
- No physical-effect deadline or command attribution was verified.
- No existing source/evidence file or remote Git branch was modified.
- No Git commit/push or Overleaf change was performed.

This package is an additive offline-tool checkpoint. It is not authorization to
run the current v3 candidate scaffolds. Known source blockers are listed separately.
