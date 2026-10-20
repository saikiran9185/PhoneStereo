# PhoneStereo

A phone's main and ultra-wide cameras streamed together to a Mac as a ~1 cm-baseline stereo camera,
and the hand-tracking music toys built on top of it. Tested on a POCO M4 Pro (HyperOS 1, Android 13).

## How it works
- Camera id 4 on this phone is a logical multi-camera made of physical cameras 0 (main) and 2 (ultra-wide).
  Opening it with `OutputConfiguration.setPhysicalCameraId` gives both streams at 1280×960, ~24 fps, with
  identical timestamps.
- Xiaomi hides the extra cameras from normal apps, so the streamer (`StereoStream.java`) runs as the adb shell
  user through `app_process` (the scrcpy trick) and sends JPEG pairs over `adb forward`.
- Only one camera device can be open at a time (each costs 100 of 100 resource units), so three back cameras at
  once is impossible (`TriProbe.java`).
- Depth near the table: a homography lines the table plane up in both views, and what's left over (parallax)
  is height above the table. Hands come from MediaPipe.

## Setup
```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
curl -L -o hand_landmarker.task \
  https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task
```
Needs the Android SDK (adb, build-tools `d8`) in `~/Library/Android/sdk`, openjdk 17, and the phone plugged in
with USB debugging on. The launch scripts build and push the streamer themselves.

## Apps
| Script | What it does |
|---|---|
| `live.sh` | Live stereo depth viewer (`viewer.py`) |
| `piano.sh` | Mat piano: a projected keyboard on a desk mat, front/back keys, multi-finger chords (`piano.py`) |
| `sticky.sh` | StickyBeat: write a sound on sticky notes (kick, snare, Am, G7, C4, or a .wav name), tap them to play. Notes are read with Apple Vision (`ocr.swift`); touch = stereo depth + the finger meeting its shadow (`sticky.py`) |
| `studio.py` | StickyBeat Studio, **work in progress**: FL-Studio-style channel rack (16 steps, patterns A–D, volume/mute/solo, BPM, swing, metronome, DJ filter), tap-to-record loops, a printable paper pattern sheet read by the camera, WAV recording, served on http://localhost:8770 |
| `run.sh` | Builds and runs `StereoProbe.java`, which lists the phone's cameras |

## Status
- StickyBeat works live. The camera has to look down on the notes: a phone looking level across the table
  sees them as thin slivers.
- Studio: sequencer, tap recording and the sheet reader are tested offline. Still to do: the browser UI
  (`studio.html`), a `studio.sh` launcher, a live test, and better reading of the sheet's far rows at very low
  camera angles.
