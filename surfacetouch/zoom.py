"""Sensor zoom on the wall: the phone stays by the projector, but its 4624 x 3472 sensor is cropped to the wall area.

The stream is 960 x 720, so the full view throws away ~5x of the sensor's detail. Cropping the sensor (camera2
SCALER_CROP_REGION, applied to both lenses on the POCO M4 Pro) to the wall plus a margin for the hand makes the finger
2-3x bigger in the image with real optical detail. Flow in SurfaceTouch --zoom:
  1. stream the full view, find the 15 markers
  2. crop = marker bounding box + margin, 4:3, at most MAX_ZOOM
  3. restart the phone streamer with that crop, then calibrate again in the zoomed view
The streamer runs over USB as the shell user from /data/local/tmp/stereo/stream_zoom.jar (./build_streamer.sh).
"""
import os
import subprocess
import time

import numpy as np

ADB = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
JAR = "/data/local/tmp/stereo/stream_zoom.jar"
MAX_ZOOM = 3.0
MARGIN = 0.22          # of the wall size, each side: room for the hand and arm around the wall


def unrotate(pts, turn, raw_w, raw_h):
    """Points in the rotated view (np.rot90(raw, -turn)) -> raw sensor-image coordinates."""
    pts = np.asarray(pts, float).copy()
    sizes = [(raw_w, raw_h)]
    for _ in range(turn % 4):
        w, h = sizes[-1]
        sizes.append((h, w))
    for i in range(turn % 4, 0, -1):
        _, h = sizes[i - 1]                       # pre-image of this clockwise step
        x, y = pts[:, 0].copy(), pts[:, 1].copy()
        pts[:, 0], pts[:, 1] = y, h - 1 - x
    return pts


def crop_for(corners_rot, turn, raw_w, raw_h, current=None):
    """Marker corners seen in the (rotated) main view -> normalised sensor crop (x0, y0, x1, y1), or None.
    `current`: the crop the stream already uses (coordinates are inside it)."""
    p = unrotate(corners_rot, turn, raw_w, raw_h) / [raw_w, raw_h]
    if current is not None:                          # into full-sensor coordinates
        cx0, cy0, cx1, cy1 = current
        p = p * [cx1 - cx0, cy1 - cy0] + [cx0, cy0]
    x0, y0 = p.min(0)
    x1, y1 = p.max(0)
    w, h = x1 - x0, y1 - y0
    x0, x1, y0, y1 = x0 - MARGIN * w, x1 + MARGIN * w, y0 - MARGIN * h, y1 + MARGIN * h
    aspect = raw_w / raw_h                           # keep the stream's shape (normalised units are not square)
    w, h = x1 - x0, y1 - y0
    W, H = w * raw_w, h * raw_h
    if W / H < aspect:
        w = H * aspect / raw_w
    else:
        h = W / aspect / raw_h
    w, h = max(w, 1 / MAX_ZOOM), max(h, 1 / MAX_ZOOM)
    w, h = min(w, 1.0), min(h, 1.0)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    x0 = float(np.clip(cx - w / 2, 0, 1 - w))
    y0 = float(np.clip(cy - h / 2, 0, 1 - h))
    c = (round(x0, 4), round(y0, 4), round(x0 + w, 4), round(y0 + h, 4))
    return None if w > 0.9 and h > 0.9 else c       # wall already fills the view: nothing to gain


def start_streamer(token, crop=None, size="960x720", port=27183, log=print):
    """(Re)start the phone streamer over USB with an optional sensor crop. Returns True when it is listening."""
    def sh(cmd, **kw):
        return subprocess.run([ADB, "shell", cmd], capture_output=True, text=True, timeout=15, **kw)
    if subprocess.run([ADB, "shell", f"ls {JAR}"], capture_output=True, text=True).returncode != 0:
        log("zoom streamer missing on the phone: run ./build_streamer.sh", show=True)
        return False
    sh("pkill -f '[p]honestereo.StereoStream'")
    time.sleep(1.2)
    c = "" if crop is None else " --crop0 " + ",".join(f"{v:.4f}" for v in crop)
    cmd = (f"CLASSPATH={JAR} app_process / dev.saikiran.phonestereo.StereoStream --camera 4 --tcp {port} "
           f"--token {token} --size {size} --quality 75 --focus 0{c} > /data/local/tmp/stereo/zoom.log 2>&1")
    sh(f"nohup sh -c '{cmd}' >/dev/null 2>&1 &")
    subprocess.run([ADB, "forward", f"tcp:{port}", f"tcp:{port}"], capture_output=True)
    for _ in range(30):
        time.sleep(0.25)
        if "READY" in sh("cat /data/local/tmp/stereo/zoom.log").stdout:
            log(f"phone streamer {'zoomed ' + str(crop) if crop else 'full view'} at {size}")
            return True
    log("phone streamer did not start (is the phone plugged in and unlocked?)", show=True)
    return False


def zoom_factor(crop):
    return 1.0 if crop is None else round(1 / max(crop[2] - crop[0], crop[3] - crop[1]), 2)
