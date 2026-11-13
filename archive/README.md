# Archive

**SurfaceTouch V1** (4 Nov 2026), git tag `surfacetouch-v1-archive`.

Its experiments, menus and modes are retired from the app: Experiment T/A/B/H/Y, the side mode, pinch click, the demo wall and Live slide control.
The new app is **Touch Stage** (`run_touchstage.py`, `touchstage/`).

The `surfacetouch/` package stays, because Touch Stage reuses its calibration, phone stream, Mac camera, recorder and replay code.

To run the old app: `.venv/bin/python archive/run_surfacetouch_v1.py --host 127.0.0.1 --token <code> [--zoom]`
