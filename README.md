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

## Wireless: phone app + Mac app
No cable, no adb on the Mac.

- **Phone app** (`android/`, package `dev.saikiran.phonestereo`). Xiaomi lets only the shell user open the
  second back camera, so the app's **Start stereo** button runs `StereoStream` (packed in the APK) as shell
  through **Shizuku**, listening on Wi-Fi port 27183. It shows a 4-digit pairing code and announces itself
  over Bonjour (`_phonestereo._tcp`). The stream only starts after the Mac sends `PS <code>`. Long-press the
  code to make a new one. Keep the app open with the screen on: if the screen sleeps, MIUI face unlock takes
  the camera.
- **Mac app**: `./make_mac_app.sh` builds `~/Applications/PhoneStereo.app`, a native SwiftUI app
  (`mac/PhoneStereoApp.swift`). It finds the phone over Bonjour, pairs with the code (saved in
  `~/.config/phonestereo/pairing.json`), asks macOS for the camera, and runs Live depth / Sign Check as child
  processes, so camera + local-network permission belong to the app. CLI fallback: `connect.py`, or
  `.venv/bin/python viewer.py --host <phone ip> --token <code>`.
- Build and install the phone app: `cd android && ./gradlew assembleDebug`, then
  `adb install -r app/build/outputs/apk/debug/app-debug.apk`.
- Shizuku must be running (Shizuku app → Start via Wireless debugging, once after every reboot).

`signs.py` is **Sign Check**, a hand-sign game that scores the Mac camera, phone main and phone ultra-wide
against each other (and fused), then compares stereo depth with single-camera hand size (PUSH / TILT rounds).
Each camera estimates how open each finger is (joint angles on MediaPipe's 3D landmarks); fusion reads each
finger from the camera that has been right most often about it (`logs/trust.json`, re-learned after every
game from the labelled rounds). The two phone lenses count as one view (they are 1 cm apart). First
measured gain (3 Nov, cross-validated): Mac alone 79% → 3 cameras 89% of frames; thumbs-up 0% → 78%.

## Research tools (3 Nov 2026)
| Script | What it does |
|---|---|
| `speedtest.py` | **Sign Cue Test**: cues 6 signs at random times, scores Mac camera / phone 2 cameras / all 3 against known ground truth (recognised %, latency, added delay, settled accuracy, per-sign, confusion). Saves every frame (finger probabilities, 21 landmarks, size, brightness) for research |
| `research.py` | Re-scores all cue sessions offline with several fusion methods (leave-one-session-out trust, average, vote) and bootstrap 95% CIs → `logs/research/results.json`. `--phone front` for front-camera sessions |
| `airbuttons.py` | Air Buttons: point at on-screen buttons, push toward the screen to press (Mac hand-scale depth + optional phone stereo), with a timed check |
| `linktest.py` | Wi-Fi vs USB stream test: fps, gaps, stalls, jitter |
| `report/sign_study.html` | The study write-up (3 sessions, 90 cues) |

First results (back phone pair, frontal placement): Mac alone 94% recognised / 85% settled; phone pair 89% / 78%
(+80 ms); all 3 with fairly learned trust = Mac (no gain, because both views face the hand head-on). THUMBS UP is the
weak sign (read as FIST). USB: longest stall 68 ms vs 355 ms on Wi-Fi.

**USB mode**: the Mac app's *Connection* switch can use a cable instead of Wi-Fi (`adb forward tcp:27183`).
**Front camera**: the phone app's *Camera* button switches between the back pair (main + ultra-wide) and the front
selfie camera, streamed alone.

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
