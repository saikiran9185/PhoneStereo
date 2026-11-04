# SurfaceTouch V1 — projector wall touch from one phone

Turns a projected wall into a touch surface using the phone's back cameras (main + ultra-wide, POCO M4 Pro
logical camera 4) and a Mac. Built after the [Touch Screen Study](../report/touch_study.html) showed that the
1 cm lens baseline is too weak to tell touch from hover (separation score 0.66, where 0.5 = guessing).

**Main idea:** the projected **shadow** decides contact. The phone finds the finger and maps it onto the wall.
Stereo is logged as a supporting feature, and the classifier is free to give it zero weight.

## Run

```
PhoneStereo app → Link: USB cable → SurfaceTouch (projector wall)
# or
.venv/bin/python run_surfacetouch.py --host 127.0.0.1 --token <code> --wall-cm <projected width>
```

## Projector test checklist (on the day)

1. **Projector** set to extend the display. Room lights dimmed. Projector brightness medium: the white wall
   must not be clipped. The app warns if the camera sees it as over-exposed.
2. **Phone** on a mount, low and to one side of the projected area, 1–1.5 m from the wall. Both back cameras
   must see the whole image. **Not next to the projector lens.** Projector and camera need different
   viewpoints, or the shadow hides behind the finger.
3. Measure the projected image width with a tape and pass it as `--wall-cm`, so errors come out in mm.
4. In SETUP: `N` picks the projector display, `R` rotates the phone view, `SPACE` calibrates.
   15 markers are projected, then a white flash (keep hands off the wall).
5. **Experiment A (`E`)**, the most important one. 6 wall positions × {touch, 10, 20, 30, 50 mm hover},
   2 s each, about 1.5 min per round. Run **3 rounds**, changing finger angle between rounds.
   Each round retrains the model on that round's samples. Use a ruler or finger widths for the hover heights
   (10 mm ≈ half a finger, 20 mm ≈ one finger).
6. **Experiment B (`V`)**: 25-point grid + 6 hover traps. Gives detection %, false touches, and error in mm
   (median, p95, max).
7. **Experiment C (`D`)**: demo wall. Buttons, drawing, dragging a card, slider. Note stuck touches and
   double activations.
8. **Live (`L`)**: open Keynote. Tap = click, flick = next / previous slide.
9. Optional: repeat A + B with room lights on, to test lighting robustness.

All data goes to `logs/surface_<time>_*`:
- `_samples.csv`: per-frame features + labels
- `_expA.json` / `_expB.json`: results
- `_events.jsonl`: touch events
- `research_sessions.jsonl`: a summary line per experiment

`logs/` holds camera frames of the user and is not committed.

## Pipeline

| Stage | File | What it does |
|---|---|---|
| Dual camera | `touch3d.Pair` | Synced main + ultra-wide frames; MediaPipe on both (closest hand) |
| Wall mapping | `surfacetouch/app.py` | 15 ArUco markers → camera→projector homography (RANSAC); ultra-wide→main homography |
| Photometric reference | `app.py` | Camera image of full-white projection (`white`) |
| Shadow features | `surfacetouch/shadow.py` | Expected brightness = k · white · projected content (k = auto-exposure gain, re-estimated per frame). Shadow = lit pixels below 50% of expected, outside the hand mask. |
| Contact model | `surfacetouch/contact.py` | Logistic regression on shadow + stereo features; threshold learned at ≤ 2% false alarms; leave-one-position-out validation |
| Touch states | `surfacetouch/state.py` | IDLE → HOVER → CANDIDATE → DOWN → DRAG; 100 ms confirm-down, 120 ms confirm-up, hysteresis, 150 ms tracking-loss cancel (never stuck) |
| Events | `app.py` | `{"event": "TOUCH_DOWN", "pointer_id": 1, "x", "y" (0–1), "confidence", "timestamp"}`, plus TOUCH_MOVE / TOUCH_UP / CLICK / CANCEL |
| Diagnostics | `app.py` (Mac window) | Main + ultra-wide with landmarks, shadow view, contact probability bar, state, fps, frame age |

### Shadow features (all in finger lengths: tip-to-PIP distance)

| Feature | Touching | Hovering |
|---|---|---|
| `along_n`: shadow tip vs fingertip along the finger | negative (shadow tip hidden behind the finger) | near 0 or ahead of the tip |
| `area_n`: visible shadow near the tip | small | larger |
| `sep_n`: lit wall between shadow and finger | ~0 (shadow attached) | grows with height |
| `gap_n`, `near_n`, `dx_n`, `dy_n` | small | larger, direction set by projector position |

The projected UI is predicted from what the app rendered, so dark buttons and drawings are not mistaken
for shadows (tested).

## Changes from the V1 brief

- **No StereoSGBM.** The two lenses differ in field of view and distortion, and the 1 cm baseline gives
  ≈ 1.4 px per 3 cm. The measured plane-plus-parallax shift is kept as a feature instead, so the model's
  weight on it shows how much it helps.
- **No checkerboard lens calibration.** The wall is a plane, so a homography from 15 markers maps it
  directly (1.5 mm fit in the Touch 3D session).
- **Events are JSON lines + Quartz mouse/keys**, not a WebSocket server yet. The event format matches the
  brief, so a browser canvas can subscribe later without changing the engine.
- **Hover heights 10–50 mm** as in the brief. Per-position samples allow the 5 × 5 error analysis.

## Status

- [x] Engine, wizard, Experiments A/B/C, live mode, diagnostics (3 Nov 2026)
- [x] Synthetic tests (`.venv/bin/python -m pytest tests`): 6 pass. End-to-end dry run with synthetic
      frames: 25/25 touches detected, 0 false touches, error ≈ 3 mm. This checks the wiring only, not real
      performance.
- [ ] First projector session (planned 4 Nov 2026)
- [ ] Browser canvas over WebSocket, multi-finger

## Zoom, recording and replay (added 4 Nov 2026)

**Zoom** (phone stays by the projector): `./build_streamer.sh` once, then
`.venv/bin/python run_surfacetouch.py --host 127.0.0.1 --token <code> --zoom`.
The first calibration finds the wall in the full view, the phone sensor is cropped to the wall plus a margin
(up to 3×, both lenses), and the app calibrates again in the zoomed view. Zoom mode closes the phone app
(it would restart its own un-zoomed streamer) and keeps the screen awake over USB.

**Recording** (default on, `--no-record` to skip): `logs/surface_<time>_video/` holds `main.mp4`, `wide.mp4`,
`mac.mp4`, `review.mp4` (diagnostics + projected wall), `frames.csv` (cue, target, button, contact p, state),
and every calibration (`calib_<n>.json`, `white_<n>.npy`).

**Replay**: `.venv/bin/python -m surfacetouch.replay logs/surface_<time>` re-runs tracking and features on the
recorded video, scans the Mac/phone time offset, and scores everything on the Experiment T button labels.
`--finger lock` tries the pointing-finger lock against thumb/index swaps.
