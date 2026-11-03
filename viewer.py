#!/usr/bin/env python3
"""PhoneStereo live viewer.

Starts the streamer on the phone (over adb), receives the main + ultra-wide camera
frames, aligns them, computes a stereo depth map, shows everything live and logs
every reading to logs/session_<time>.csv (+ an event log).

Keys (in the window):
  C  calibrate: hold your hand / a book flat in the centre box at the set distance
  [ ]  calibration distance -/+ 5 cm          A  re-align the two cameras
  S  flip depth direction
  O  rotate the view 90°                      P  save a snapshot
  L  pause / resume the CSV log               Q / Esc  quit
"""
import argparse
import collections
import csv
import datetime
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(HERE, "logs")
CALIB_PATH = os.path.join(HERE, "calib.json")
JAR = "/data/local/tmp/stereo/stream.jar"
PORT = 27183
WIN = "PhoneStereo - live depth"

TILE_W, TILE_H = 480, 360
MIN_DISP, NUM_DISP = -32, 64          # symmetric search: works whichever camera is "left"
FRAME_HDR = struct.Struct(">IBqqhhI")
MAGIC_HEADER, MAGIC_FRAME = 0x50535431, 0x46524D31


def now_iso():
    return datetime.datetime.now().isoformat(timespec="milliseconds")


class EventLog:
    def __init__(self, path):
        self.f = open(path, "a", buffering=1)
        self.lock = threading.Lock()
        self.last = ("", 0.0)

    def __call__(self, msg, show=False):
        line = f"{now_iso()}  {msg}"
        with self.lock:
            self.f.write(line + "\n")
        print(line, flush=True)
        if show:
            self.last = (msg, time.time())


# ---------------------------------------------------------------- phone side
class Phone:
    def __init__(self, log, args):
        self.log, self.args, self.proc = log, args, None
        self.ready = threading.Event()

    def start(self):
        subprocess.run(["adb", "shell", "pkill", "-f", "StereoStream"], capture_output=True)
        # If the screen sleeps, MIUI face unlock grabs the front camera and Android evicts our
        # stream, so keep the phone awake while plugged in; stop() restores the user's setting.
        r = subprocess.run(["adb", "shell", "settings", "get", "global", "stay_on_while_plugged_in"],
                           capture_output=True, text=True)
        self.prev_stay = r.stdout.strip() or "0"
        subprocess.run(["adb", "shell", "svc", "power", "stayon", "usb"], capture_output=True)
        subprocess.run(["adb", "shell", "input", "keyevent", "KEYCODE_WAKEUP"], capture_output=True)
        cmd = (f"CLASSPATH={JAR} app_process / StereoStream --camera {self.args.camera} "
               f"--size {self.args.size} --quality {self.args.quality} --focus {self.args.focus}")
        self.proc = subprocess.Popen(["adb", "shell", cmd], stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, bufsize=1)
        threading.Thread(target=self._pump, daemon=True).start()
        if not self.ready.wait(20):
            raise RuntimeError("phone streamer did not start (is the phone connected and unlocked?)")
        subprocess.run(["adb", "forward", f"tcp:{PORT}", "localabstract:phonestereo"], check=True,
                       capture_output=True)

    def _pump(self):
        for line in self.proc.stdout:
            line = line.rstrip()
            s = line.strip()
            # MIUI prints a harmless theme-resources stack trace when the context is created.
            if not s or s.startswith("at ") or s.startswith("...") or "ErrnoException" in s \
                    or "FileNotFoundException" in s or "ThemeCompatibility" in s:
                continue
            self.log(f"phone> {s}")
            if s.startswith("READY"):
                self.ready.set()

    def stop(self):
        subprocess.run(["adb", "forward", "--remove", f"tcp:{PORT}"], capture_output=True)
        if getattr(self, "prev_stay", None) is not None:
            subprocess.run(["adb", "shell", "settings", "put", "global", "stay_on_while_plugged_in",
                            self.prev_stay], capture_output=True)
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        subprocess.run(["adb", "shell", "pkill", "-f", "StereoStream"], capture_output=True)


class Receiver(threading.Thread):
    """Reads the phone's stream. host 127.0.0.1 = USB/adb forward; a phone IP = the PhoneStereo app
    over Wi-Fi, which needs the 4-digit pairing code (token) before it sends anything."""

    def __init__(self, log, host="127.0.0.1", token=None, port=PORT):
        super().__init__(daemon=True)
        self.log = log
        self.host, self.token, self.port = host, token, port
        self.lock = threading.Lock()
        self.frames = [collections.deque(maxlen=8), collections.deque(maxlen=8)]
        self.header = None
        self.header_ready = threading.Event()
        self.arrivals = [collections.deque(maxlen=60), collections.deque(maxlen=60)]
        self.bytes = collections.deque(maxlen=120)
        self.error = None
        self.running = True

    def _exact(self, n):
        buf = bytearray(n)
        view, got = memoryview(buf), 0
        while got < n:
            r = self.sock.recv_into(view[got:], n - got)
            if r == 0:
                raise ConnectionError("phone closed the stream")
            got += r
        return bytes(buf)

    def run(self):
        try:
            deadline = time.time() + 10
            while True:
                try:
                    self.sock = socket.create_connection((self.host, self.port), timeout=5)
                    if self.token:
                        self.sock.sendall(f"PS {self.token}\n".encode())
                    break
                except OSError:
                    if time.time() > deadline:
                        raise
                    time.sleep(0.3)
            self.sock.settimeout(10)
            first = self._exact(8)
            if first.startswith(b"ERR"):
                raise ConnectionError("phone refused: wrong pairing code")
            magic, n = struct.unpack(">II", first)
            if magic != MAGIC_HEADER:
                raise ConnectionError(f"bad header magic {magic:#x}")
            self.header = json.loads(self._exact(n).decode())
            self.header_ready.set()
            while self.running:
                magic, cam, ts, _phone_ns, w, h, n = FRAME_HDR.unpack(self._exact(FRAME_HDR.size))
                if magic != MAGIC_FRAME:
                    raise ConnectionError(f"bad frame magic {magic:#x}")
                jpg = self._exact(n)
                t = time.time()
                img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
                with self.lock:
                    self.frames[cam].append({"ts": ts, "img": img, "t_recv": t})
                    self.arrivals[cam].append(t)
                    self.bytes.append((t, n + FRAME_HDR.size))
        except Exception as e:  # surfaced in the UI and the log
            if not self.running:
                return
            self.error = str(e)
            self.header_ready.set()
            self.log(f"stream error: {e}", show=True)

    def fps(self, cam):
        with self.lock:
            a = list(self.arrivals[cam])
        if len(a) < 2 or time.time() - a[-1] > 1.0:
            return 0.0
        return (len(a) - 1) / (a[-1] - a[0])

    def mbps(self):
        with self.lock:
            b = list(self.bytes)
        if len(b) < 2:
            return 0.0
        return sum(x[1] for x in b) / max(1e-3, b[-1][0] - b[0][0]) / 1e6


# ---------------------------------------------------------------- geometry
def gray(img):
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def factory_homography(cams, w, h):
    """Ultra-wide -> main pixel mapping from the phone's own lens data (parallel lenses assumed):
    just the focal-length ratio about each principal point, scaled to the stream size."""
    def K(c):
        fx, fy, cx, cy, _ = c["intrinsics"]
        sx, sy = w / c["preCorrectionActiveArray"][2], h / c["preCorrectionActiveArray"][3]
        return np.array([[fx * sx, 0, cx * sx], [0, fy * sy, cy * sy], [0, 0, 1]], np.float64)
    return K(cams[0]) @ np.linalg.inv(K(cams[1]))


def match_points(main, uw_pre):
    sift = cv2.SIFT_create(nfeatures=4000)
    k1, d1 = sift.detectAndCompute(gray(main), None)
    k2, d2 = sift.detectAndCompute(gray(uw_pre), None)
    if d1 is None or d2 is None or len(k1) < 20 or len(k2) < 20:
        return np.zeros((0, 2), np.float32), np.zeros((0, 2), np.float32)
    knn = cv2.BFMatcher(cv2.NORM_L2).knnMatch(d2, d1, k=2)
    good = [m for m, n in (p for p in knn if len(p) == 2) if m.distance < 0.75 * n.distance]
    src = np.float32([k2[m.queryIdx].pt for m in good]).reshape(-1, 2)
    dst = np.float32([k1[m.trainIdx].pt for m in good]).reshape(-1, 2)
    keep = np.linalg.norm(dst - src, axis=1) < 120   # after the factory warp, true matches are close
    return src[keep], dst[keep]


def fit_alignment(H0, src, dst):
    """Correction on top of the factory mapping, fitted to matches pooled from still frames.

    1. An affine fit lines the views up for the dominant scene depth.
    2. Whatever is nearer / farther stays shifted, and for two cameras that differ only by a
       translation all those shifts point along one line: the baseline. Its angle is found by
       voting, so both images can be rotated until the baseline lies along x for the matcher.
    The leftover constant offset is removed by the distance calibration."""
    if len(src) < 60:
        return None
    A, mask = cv2.estimateAffine2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=2.0)
    if A is None:
        return None
    scale = float(np.sqrt(abs(np.linalg.det(A[:, :2]))))
    roll = float(np.degrees(np.arctan2(A[1, 0], A[0, 0])))
    shift = float(np.hypot(A[0, 2], A[1, 2]))
    if not (0.85 < scale < 1.15 and abs(roll) < 5 and shift < 150):
        return {"rejected": f"scale {scale:.3f} roll {roll:.2f} deg shift {shift:.0f} px"}
    res = dst - (src @ A[:, :2].T + A[:, 2])
    n = np.linalg.norm(res, axis=1)
    off = res[(n > 2.5) & (n < 30)]
    angle, support = 0.0, 0.0
    if len(off) >= 30:
        best = (0, 0.0)
        for a in np.arange(-90, 90, 0.5):
            u = np.array([np.cos(np.radians(a)), np.sin(np.radians(a))])
            c = int((np.abs(off @ np.array([-u[1], u[0]])) < 1.2).sum())
            if c > best[0]:
                best = (c, a)
        support = best[0] / len(off)
        if support >= 0.4:
            angle = float(best[1])
    return {
        "H": np.vstack([A, [0, 0, 1]]) @ H0, "inliers": int(mask.sum()), "matches": len(src),
        "scale": scale, "roll_deg": roll, "shift_px": shift, "off_plane": int(len(off)),
        "angle_deg": angle, "angle_support": float(support),
    }


def baseline_rotation(angle_deg, w, h):
    """3x3 rotation about the image centre that turns the baseline direction into +x."""
    c, s = np.cos(np.radians(angle_deg)), np.sin(np.radians(angle_deg))
    R = np.array([[c, s], [-s, c]])
    p0 = np.array([w / 2, h / 2])
    t = p0 - R @ p0
    return np.array([[R[0, 0], R[0, 1], t[0]], [R[1, 0], R[1, 1], t[1]], [0, 0, 1]])


def make_sgbm(block=5):
    return cv2.StereoSGBM_create(
        minDisparity=MIN_DISP, numDisparities=NUM_DISP, blockSize=block,
        P1=8 * block * block, P2=32 * block * block, disp12MaxDiff=2,
        preFilterCap=31, uniquenessRatio=8, speckleWindowSize=80, speckleRange=2,
        mode=cv2.STEREO_SGBM_MODE_SGBM_3WAY)


def fit_tile(img, w=TILE_W, h=TILE_H):
    ih, iw = img.shape[:2]
    s = min(w / iw, h / ih)
    nw, nh = int(iw * s), int(ih * s)
    canvas = np.zeros((h, w, 3), np.uint8)
    ox, oy = (w - nw) // 2, (h - nh) // 2
    canvas[oy:oy + nh, ox:ox + nw] = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
    return canvas, s, ox, oy


def rot(img, k):
    return np.ascontiguousarray(np.rot90(img, -k)) if k % 4 else img


def fmt_m(z):
    if z is None or not np.isfinite(z):
        return "--"
    return f"{z * 100:.1f} cm" if z < 1.0 else f"{z:.2f} m"


# ---------------------------------------------------------------- viewer
class Viewer:
    def __init__(self, args):
        os.makedirs(LOG_DIR, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.stamp = stamp
        self.log = EventLog(os.path.join(LOG_DIR, f"session_{stamp}.log"))
        self.csv_path = os.path.join(LOG_DIR, f"session_{stamp}.csv")
        self.args = args
        self.proc_w = args.proc_width
        self.sgbm = make_sgbm()
        self.clahe = cv2.createCLAHE(2.0, (8, 8))
        self.calib = self._load_calib()
        self.cal_dist_cm = self.calib.get("distance_cm", 40)
        self.collect = None
        self.rotation = 0
        self.mouse = None
        self.depth_view = None          # (rotated depth array, scale, ox, oy) for cursor lookup
        self.logging = True
        self.rows = 0
        self.pairs = 0
        self.last_ts0 = -1
        self.proc_times = collections.deque(maxlen=30)
        self.pair_times = collections.deque(maxlen=30)
        self.gaps = collections.deque(maxlen=60)
        self.vert_check = None
        self.last_print = 0
        self.last_save = 0
        self.snap_n = 0
        self.t0 = time.time()
        self.readings = {}

    # ---- calibration file
    def _load_calib(self):
        try:
            with open(CALIB_PATH) as f:
                c = json.load(f)
            if "H" in c:
                c["H"] = np.array(c["H"], np.float64).reshape(3, 3)
            return c
        except Exception:
            return {"sign": 1, "offset_px": 0.0, "calibrated": False}

    def _save_calib(self):
        c = dict(self.calib)
        if "H" in c:
            c["H"] = np.asarray(c["H"]).ravel().tolist()
        c["proc_w"] = self.proc_w
        c["saved"] = now_iso()
        with open(CALIB_PATH, "w") as f:
            json.dump(c, f, indent=2)

    # ---- setup
    def run(self):
        a = self.args
        self.log(f"session start - log {self.csv_path}")
        self.phone = Phone(self.log, a) if not (a.no_phone or a.host) else None
        if self.phone:
            self.log("starting phone streamer ...")
            self.phone.start()
        self.rx = Receiver(self.log, host=a.host or "127.0.0.1", token=a.token)
        self.rx.start()
        self.rx.header_ready.wait(15)
        if self.rx.error or not self.rx.header:
            raise RuntimeError(f"no stream from phone: {self.rx.error}")
        h = self.rx.header
        cams = h["cameras"]
        self.log(f"stream: camera {h['logicalId']} -> physical {[c['id'] for c in cams]}, "
                 f"{h['width']}x{h['height']}, sync {h['syncType']}, focus {h['focusDiopters']} dpt")
        for c in cams:
            self.log(f"  cam {c['id']}: intrinsics {c['intrinsics']} pose {c['poseTranslation']} "
                     f"array {c['preCorrectionActiveArray']} orient {c['sensorOrientation']}")
        fx_main, pre_w = cams[0]["intrinsics"][0], cams[0]["preCorrectionActiveArray"][2]
        self.baseline = float(np.linalg.norm(cams[1]["poseTranslation"])) or 0.01008
        self.full_w, self.full_h = h["width"], h["height"]
        self.proc_h = int(round(self.proc_w * self.full_h / self.full_w))
        self.f_proc = fx_main * self.proc_w / pre_w
        self.fB = self.f_proc * self.baseline
        self.sync_type = h["syncType"]
        self.H0 = factory_homography(cams, self.full_w, self.full_h)
        self.align_state = {"pool": [], "prev": None}
        if "H" not in self.calib:
            self.log("aligning: prop the phone still, point it at a scene with near and far objects, "
                     "stay out of view (or press A later)", show=True)
        if self.calib.get("proc_w") and self.calib["proc_w"] != self.proc_w:
            self.calib["offset_px"] *= self.proc_w / self.calib["proc_w"]
        self.log(f"baseline {self.baseline * 100:.2f} cm, focal {self.f_proc:.1f} px at {self.proc_w} wide "
                 f"-> 1 px disparity = {self.fB:.2f} m")
        if self.calib.get("calibrated"):
            self.log(f"loaded calibration: sign {self.calib['sign']}, offset {self.calib['offset_px']:.2f} px "
                     f"(set at {self.calib.get('distance_cm')} cm)", show=True)

        self.csv_f = open(self.csv_path, "w", newline="")
        self.csv = csv.writer(self.csv_f)
        self.csv.writerow(["time", "t_s", "pair", "ts_main_ns", "ts_uw_ns", "sync_gap_ms", "main_fps",
                           "uw_fps", "proc_fps", "proc_ms", "frame_age_ms", "valid_pct", "center_disp_px",
                           "center_cm", "nearest_cm", "nearest_x", "nearest_y", "median_cm", "p5_cm",
                           "p95_cm", "cursor_cm", "calibrated", "sign", "offset_px", "align_inliers",
                           "vertical_err_px"])

        if not a.headless:
            cv2.namedWindow(WIN, cv2.WINDOW_AUTOSIZE)
            cv2.setMouseCallback(WIN, self._on_mouse)
        try:
            self._loop()
        finally:
            self.rx.running = False
            if self.phone:
                self.phone.stop()
            self.csv_f.close()
            self.log(f"session end - {self.pairs} pairs processed, {self.rows} rows logged to {self.csv_path}")
            if not a.headless:
                cv2.destroyAllWindows()

    def _on_mouse(self, event, x, y, *_):
        if event == cv2.EVENT_MOUSEMOVE:
            self.mouse = (x, y)

    # ---- main loop
    def _pick_pair(self):
        with self.rx.lock:
            mains, uws = list(self.rx.frames[0]), list(self.rx.frames[1])
        if not mains or not uws:
            return None
        cands = []
        for f0 in reversed(mains):
            if f0["ts"] <= self.last_ts0:
                break
            f1 = min(uws, key=lambda f: abs(f["ts"] - f0["ts"]))
            cands.append((f0, f1, abs(f1["ts"] - f0["ts"]) / 1e6))
        if not cands:
            return None
        for c in cands:              # newest pair captured together (same timestamp)
            if c[2] < 8:
                return c
        # Its partner may still be on the way; only give up on exact sync after 120 ms.
        if time.time() - cands[0][0]["t_recv"] > 0.12:
            return min(cands, key=lambda c: c[2])
        return None

    def _loop(self):
        a = self.args
        while True:
            if self.rx.error:
                raise RuntimeError(self.rx.error)
            pair = self._pick_pair()
            if pair is None:
                if not a.headless and self._keys(cv2.waitKey(5)):
                    return
                time.sleep(0.002)
                continue
            f0, f1, gap = pair
            self.last_ts0 = f0["ts"]
            t_start = time.time()
            self._process(f0, f1, gap, t_start)
            if not a.headless:
                cv2.imshow(WIN, self.view)
                if self._keys(cv2.waitKey(1)):
                    return
            if a.duration and time.time() - self.t0 > a.duration:
                return

    def _keys(self, k):
        if k < 0:
            return False
        k &= 0xFF
        ch = chr(k).lower() if k < 128 else ""
        if k == 27 or ch == "q":
            return True
        if ch == "c":
            self.collect = {"center": [], "ring": [], "frames": 0}
            self.log(f"calibration started at {self.cal_dist_cm} cm - hold still", show=True)
        elif ch == "[":
            self.cal_dist_cm = max(10, self.cal_dist_cm - 5)
            self.log(f"calibration distance {self.cal_dist_cm} cm", show=True)
        elif ch == "]":
            self.cal_dist_cm = min(150, self.cal_dist_cm + 5)
            self.log(f"calibration distance {self.cal_dist_cm} cm", show=True)
        elif ch == "a":
            self.calib.pop("H", None)
            self.calib["calibrated"] = False
            self.align_state = {"pool": [], "prev": None}
            self.log("re-aligning: keep the phone and scene still for ~3 s", show=True)
        elif ch == "s":
            self.calib["sign"] = -self.calib.get("sign", 1)
            self.calib["calibrated"] = False
            self.log(f"depth direction flipped (sign {self.calib['sign']}) - recalibrate with C", show=True)
        elif ch == "o":
            self.rotation = (self.rotation + 1) % 4
        elif ch == "p":
            self._snapshot()
        elif ch == "l":
            self.logging = not self.logging
            self.log(f"CSV logging {'on' if self.logging else 'paused'}", show=True)
        return False

    # ---- per pair
    def _process(self, f0, f1, gap, t_start):
        main, uw = f0["img"], f1["img"]
        cal = self.calib
        if "H" not in cal:
            uw_pre = cv2.warpPerspective(uw, self.H0, (self.full_w, self.full_h))
            if gap < 1 and self.pairs % 3 == 0:
                self._align_step(main, uw_pre)
            self.pairs += 1
            pw, ph = self.proc_w, self.proc_h
            self._draw(cv2.resize(main, (pw, ph), interpolation=cv2.INTER_AREA),
                       cv2.resize(uw_pre, (pw, ph), interpolation=cv2.INTER_AREA), None, None, gap, f0, t_start)
            return

        pw, ph = self.proc_w, self.proc_h
        S = np.diag([pw / self.full_w, ph / self.full_h, 1.0])
        R = baseline_rotation(cal.get("angle_deg", 0.0), pw, ph)
        main_s = cv2.resize(main, (pw, ph), interpolation=cv2.INTER_AREA)
        uw_w = cv2.warpPerspective(uw, S @ cal["H"], (pw, ph), flags=cv2.INTER_LINEAR)
        cover = cv2.warpPerspective(np.full(uw.shape[:2], 255, np.uint8), S @ cal["H"], (pw, ph),
                                    flags=cv2.INTER_NEAREST) > 0
        # Rotate both views so the baseline lies along x (the only direction SGBM searches).
        gl = cv2.warpAffine(self.clahe.apply(gray(main_s)), R[:2], (pw, ph))
        gr = cv2.warpAffine(self.clahe.apply(gray(uw_w)), R[:2], (pw, ph))
        disp_r = self.sgbm.compute(gl, gr).astype(np.float32) / 16.0
        disp = cv2.warpAffine(disp_r, R[:2], (pw, ph), flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=MIN_DISP - 1)
        # Values pinned at either end of the search range are noise that ran out of room, not depth.
        valid = (disp >= MIN_DISP + 1) & (disp <= MIN_DISP + NUM_DISP - 2) & cover
        sd = cal.get("sign", 1) * disp

        if self.pairs % 15 == 0:
            self.vert_check = self._vertical_check(gl, gr)

        if cal.get("calibrated"):
            dtrue = sd + cal["offset_px"]
            ok = valid & (dtrue > 0.25)
            depth = np.full(disp.shape, np.nan, np.float32)
            depth[ok] = self.fB / dtrue[ok]
            depth[depth > 8] = np.nan
        else:
            depth = None

        if self.collect is not None:
            self._collect_calibration(sd, valid)

        self.proc_times.append((time.time() - t_start) * 1000)
        self.pair_times.append(time.time())
        self.gaps.append(gap)
        self.pairs += 1
        self._draw(main_s, uw_w, sd, (valid, depth), gap, f0, t_start, f1=f1)

    def _align_step(self, main, uw_pre):
        """Collect matches from still, synced frames; fit once 8 still frames are pooled."""
        small = cv2.resize(gray(main), (160, 120), interpolation=cv2.INTER_AREA).astype(np.float32)
        st = self.align_state
        if st["prev"] is not None and float(np.mean(np.abs(small - st["prev"]))) > 4.0:
            if st["pool"]:
                self.log("align: movement detected - keep the phone and scene still", show=True)
            st["pool"] = []
        st["prev"] = small
        st["pool"].append(match_points(main, uw_pre))
        if len(st["pool"]) < 8:
            return
        src = np.vstack([p[0] for p in st["pool"]])
        dst = np.vstack([p[1] for p in st["pool"]])
        st["pool"] = []
        cal = self.calib
        r = fit_alignment(self.H0, src, dst)
        if r and "H" in r:
            cal["H"], cal["angle_deg"] = r["H"], r["angle_deg"]
            cal["align"] = {k: r[k] for k in ("inliers", "matches", "scale", "roll_deg", "shift_px",
                                              "off_plane", "angle_deg", "angle_support")}
            cal["calibrated"] = False
            self._save_calib()
            how = (f"baseline at {r['angle_deg']:+.1f} deg ({100 * r['angle_support']:.0f}% of "
                   f"{r['off_plane']} shifted points agree)" if r["angle_support"] >= 0.4 else
                   "baseline direction unclear - using factory direction (x)")
            self.log(f"aligned: {r['inliers']}/{r['matches']} inliers, lens correction scale {r['scale']:.3f} "
                     f"roll {r['roll_deg']:.2f} deg shift {r['shift_px']:.1f} px; {how}", show=True)
        elif r:
            self.log(f"align rejected ({r['rejected']}) - retrying", show=True)
        else:
            self.log(f"align: only {len(src)} matches - point the phone at something with detail "
                     f"(shelf, keyboard, posters)", show=True)

    def _vertical_check(self, a, b):
        """Live check of the alignment: matched features should differ only along x after rotation."""
        orb = cv2.ORB_create(800)
        ka, da = orb.detectAndCompute(a, None)
        kb, db = orb.detectAndCompute(b, None)
        if da is None or db is None:
            return None
        ms = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(da, db)
        if len(ms) < 20:
            return None
        d = np.float32([np.subtract(ka[m.queryIdx].pt, kb[m.trainIdx].pt) for m in ms])
        d = d[np.linalg.norm(d, axis=1) < 40]
        if len(d) < 15:
            return None
        return float(np.median(np.abs(d[:, 1])))

    def _center_box(self, w, h):
        bw, bh = int(w * 0.18), int(h * 0.22)
        return (w - bw) // 2, (h - bh) // 2, bw, bh

    def _collect_calibration(self, sd, valid):
        h, w = sd.shape
        x, y, bw, bh = self._center_box(w, h)
        box = np.zeros_like(valid)
        box[y:y + bh, x:x + bw] = True
        cv_ = valid & box
        ring = valid & ~box
        c = self.collect
        c["frames"] += 1
        if cv_.sum() > 0.25 * bw * bh and ring.sum() > 500:
            c["center"].append(float(np.median(sd[cv_])))
            c["ring"].append(float(np.median(sd[ring])))
        if c["frames"] >= 30 or len(c["center"]) >= 12:
            self.collect = None
            if len(c["center"]) < 6:
                self.log("calibration failed: not enough texture in the centre box - try a book or "
                         "your open hand, closer to the camera", show=True)
                return
            center, ringv = float(np.median(c["center"])), float(np.median(c["ring"]))
            # Whatever sits in the box is nearer than the room behind it, so it must end up
            # with the larger disparity. That fixes which way the cameras are ordered.
            if center < ringv:
                self.calib["sign"] = -self.calib.get("sign", 1)
                center, ringv = -center, -ringv
            z = self.cal_dist_cm / 100.0
            self.calib["offset_px"] = self.fB / z - center
            self.calib["calibrated"] = True
            self.calib["distance_cm"] = self.cal_dist_cm
            self._save_calib()
            self.log(f"calibrated at {self.cal_dist_cm} cm: sign {self.calib['sign']}, offset "
                     f"{self.calib['offset_px']:.2f} px (centre {center:.2f} px, room {ringv:.2f} px)", show=True)

    # ---- readings + drawing
    def _readings(self, sd, valid, depth):
        h, w = sd.shape
        x, y, bw, bh = self._center_box(w, h)
        r = {"valid_pct": 100.0 * valid.mean()}
        cbox = valid[y:y + bh, x:x + bw]
        r["center_disp"] = float(np.median(sd[y:y + bh, x:x + bw][cbox])) if cbox.sum() > 30 else None
        if depth is not None:
            dz = depth[y:y + bh, x:x + bw]
            dz = dz[np.isfinite(dz)]
            r["center"] = float(np.median(dz)) if dz.size > 30 else None
            fin = np.isfinite(depth)
            fin[:, :max(0, MIN_DISP + NUM_DISP)] = False  # SGBM cannot search the left edge
            if fin.sum() > 200:
                vals = depth[fin]
                r["median"] = float(np.median(vals))
                r["p5"], r["p95"] = float(np.percentile(vals, 5)), float(np.percentile(vals, 95))
                filled = np.where(fin, depth, 99).astype(np.float32)
                filled = cv2.medianBlur(filled, 5)
                iy, ix = np.unravel_index(np.argmin(filled), filled.shape)
                r["nearest"] = float(filled[iy, ix]) if filled[iy, ix] < 50 else None
                r["nearest_xy"] = (int(ix), int(iy))
        return r

    def _depth_image(self, sd, valid, depth):
        if depth is not None:
            inv = np.where(np.isfinite(depth), 1.0 / np.maximum(depth, 1e-3), 0)
            near, far = 1 / 0.15, 1 / 2.5
            n = np.clip((inv - far) / (near - far), 0, 1)
            img = cv2.applyColorMap((n * 255).astype(np.uint8), cv2.COLORMAP_TURBO)
            img[~np.isfinite(depth)] = 0
        else:
            v = sd[valid]
            lo, hi = (np.percentile(v, 2), np.percentile(v, 98)) if v.size > 100 else (0, 1)
            n = np.clip((sd - lo) / max(1e-3, hi - lo), 0, 1)
            img = cv2.applyColorMap((n * 255).astype(np.uint8), cv2.COLORMAP_TURBO)
            img[~valid] = 0
        return img

    def _draw(self, main_s, uw_w, sd, vd, gap, f0, t_start, f1=None):
        h, w = main_s.shape[:2]
        bx, by, bw, bh = self._center_box(w, h)
        r = {}
        if sd is not None:
            valid, depth = vd
            r = self._readings(sd, valid, depth)
            dimg = self._depth_image(sd, valid, depth)
            cv2.rectangle(dimg, (bx, by), (bx + bw, by + bh), (255, 255, 255), 1)
            if r.get("nearest_xy") and r.get("nearest") is not None:
                cv2.circle(dimg, r["nearest_xy"], 9, (255, 255, 255), 2)
            depth_rot = rot(depth, self.rotation) if depth is not None else None
        else:
            dimg = np.zeros((h, w, 3), np.uint8)
            cv2.putText(dimg, "aligning cameras...", (w // 2 - 110, h // 2), cv2.FONT_HERSHEY_SIMPLEX,
                        0.7, (200, 200, 200), 2)
            depth_rot = None
        main_d = main_s.copy()
        cv2.rectangle(main_d, (bx, by), (bx + bw, by + bh), (255, 255, 255), 1)

        k = self.rotation
        t_main, _, _, _ = fit_tile(rot(main_d, k))
        t_uw, _, _, _ = fit_tile(rot(uw_w, k))
        t_depth, s, ox, oy = fit_tile(rot(dimg, k))

        cursor = None
        if self.mouse and depth_rot is not None:
            mx, my = self.mouse
            if 0 <= mx - 0 < TILE_W and TILE_H <= my < 2 * TILE_H:
                px, py = int((mx - ox) / s), int((my - TILE_H - oy) / s)
                if 0 <= py < depth_rot.shape[0] and 0 <= px < depth_rot.shape[1]:
                    v = depth_rot[py, px]
                    cursor = (mx, my - TILE_H, float(v) if np.isfinite(v) else None)
        if cursor:
            cv2.drawMarker(t_depth, cursor[:2], (255, 255, 255), cv2.MARKER_CROSS, 16, 1)
        r["cursor"] = cursor[2] if cursor else None

        for img, label in ((t_main, "MAIN camera (aligned)"), (t_uw, "ULTRA-WIDE warped onto main"),
                           (t_depth, "DEPTH  near = red, far = blue" if self.calib.get("calibrated")
                            else "DISPARITY (uncalibrated)")):
            cv2.rectangle(img, (0, 0), (TILE_W, 22), (0, 0, 0), -1)
            cv2.putText(img, label, (8, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 255, 255), 1, cv2.LINE_AA)

        panel = self._panel(r, gap, f0)
        self.view = np.vstack([np.hstack([t_main, t_uw]), np.hstack([t_depth, panel])])
        self.readings = r
        self._log_row(r, gap, f0, f1, t_start)

        t = time.time()
        if t - self.last_save > 2:
            cv2.imwrite(os.path.join(LOG_DIR, "latest_view.jpg"), self.view)
            self.last_save = t
        if t - self.last_print > 2:
            self.last_print = t
            print(f"[{t - self.t0:6.1f}s] main {self.rx.fps(0):4.1f}fps uw {self.rx.fps(1):4.1f}fps "
                  f"gap {gap:5.1f}ms proc {self._proc_fps():4.1f}fps {self._proc_ms():5.1f}ms "
                  f"valid {r.get('valid_pct', 0):4.1f}% centre {fmt_m(r.get('center'))} "
                  f"(disp {r.get('center_disp') if r.get('center_disp') is None else round(r['center_disp'], 2)}) "
                  f"nearest {fmt_m(r.get('nearest'))} vert {self.vert_check}", flush=True)

    def _proc_fps(self):
        p = list(self.pair_times)
        return (len(p) - 1) / (p[-1] - p[0]) if len(p) > 1 and p[-1] > p[0] else 0.0

    def _proc_ms(self):
        return float(np.mean(self.proc_times)) if self.proc_times else 0.0

    def _panel(self, r, gap, f0):
        p = np.full((TILE_H, TILE_W, 3), 18, np.uint8)
        y = [24]

        def line(text, color=(220, 220, 220), scale=0.46, gapy=19, bold=1):
            cv2.putText(p, text, (12, y[0]), cv2.FONT_HERSHEY_SIMPLEX, scale, color, bold, cv2.LINE_AA)
            y[0] += gapy

        head, ok, warn, bad = (120, 200, 255), (130, 230, 130), (60, 200, 255), (90, 90, 255)
        line("PHONE STREAM", head, 0.5)
        line(f"main {self.rx.fps(0):.1f} fps   ultra-wide {self.rx.fps(1):.1f} fps   {self.rx.mbps():.1f} MB/s")
        med_gap = float(np.median(self.gaps)) if self.gaps else gap
        line(f"sync gap {gap:.1f} ms (median {med_gap:.1f})  [{self.sync_type}]",
             ok if med_gap < 20 else warn)
        age = (time.time() - f0["t_recv"]) * 1000
        line(f"depth {self._proc_fps():.1f} fps   compute {self._proc_ms():.0f} ms   age {age:.0f} ms")
        y[0] += 6
        line("ALIGNMENT", head, 0.5)
        al = self.calib.get("align", {})
        vc = "--" if self.vert_check is None else f"{self.vert_check:.2f} px"
        if "H" in self.calib:
            line(f"inliers {al.get('inliers', '--')}   baseline {self.calib.get('angle_deg', 0):+.1f} deg   "
                 f"cross error {vc}", ok if self.vert_check is not None and self.vert_check < 1.0 else warn)
        else:
            line(f"aligning... still frames {len(self.align_state['pool'])}/8 (keep phone + scene still)", warn)
        y[0] += 6
        if self.calib.get("calibrated"):
            line(f"DEPTH  (calibrated at {self.calib.get('distance_cm')} cm)", head, 0.5)
            line(f"centre box   {fmt_m(r.get('center'))}", (255, 255, 255), 0.62, 24, 2)
            line(f"nearest      {fmt_m(r.get('nearest'))}", (255, 255, 255), 0.62, 24, 2)
            line(f"scene median {fmt_m(r.get('median'))}   range {fmt_m(r.get('p5'))} - {fmt_m(r.get('p95'))}")
            line(f"cursor {fmt_m(r.get('cursor'))}   valid pixels {r.get('valid_pct', 0):.0f}%")
        else:
            line("DEPTH  (UNCALIBRATED)", warn, 0.5)
            cd = r.get("center_disp")
            line(f"centre disparity {'--' if cd is None else f'{cd:.2f} px'}   valid {r.get('valid_pct', 0):.0f}%")
            line(f"Hold hand/book in the box at {self.cal_dist_cm} cm, press C", warn)
            line("( [ / ] change that distance )", (160, 160, 160))
        if self.collect is not None:
            line(f"calibrating... {len(self.collect['center'])}/12", warn)
        msg, t = self.log.last
        y[0] = TILE_H - 44
        if msg and time.time() - t < 6:
            line(msg[:62], ok, 0.42)
        y[0] = TILE_H - 22
        logtxt = f"log {'ON' if self.logging else 'PAUSED'} {self.rows} rows"
        line(f"{logtxt}  |  C cal  A align  S flip  O rot  P snap  Q quit", (150, 150, 150), 0.38)
        return p

    def _log_row(self, r, gap, f0, f1, t_start):
        if not self.logging:
            return

        def cm(v):
            return "" if v is None or not np.isfinite(v) else f"{v * 100:.1f}"

        nxy = r.get("nearest_xy") or ("", "")
        self.csv.writerow([
            now_iso(), f"{time.time() - self.t0:.3f}", self.pairs, f0["ts"], f1["ts"] if f1 else "",
            f"{gap:.2f}", f"{self.rx.fps(0):.1f}", f"{self.rx.fps(1):.1f}", f"{self._proc_fps():.1f}",
            f"{self._proc_ms():.1f}", f"{(time.time() - f0['t_recv']) * 1000:.0f}",
            f"{r.get('valid_pct', 0):.1f}",
            "" if r.get("center_disp") is None else f"{r['center_disp']:.3f}",
            cm(r.get("center")), cm(r.get("nearest")), nxy[0], nxy[1], cm(r.get("median")),
            cm(r.get("p5")), cm(r.get("p95")), cm(r.get("cursor")),
            int(bool(self.calib.get("calibrated"))), self.calib.get("sign", 1),
            f"{self.calib.get('offset_px', 0):.3f}", self.calib.get("align", {}).get("inliers", ""),
            "" if self.vert_check is None else f"{self.vert_check:.3f}"])
        self.rows += 1
        if self.rows % 20 == 0:
            self.csv_f.flush()

    def _snapshot(self):
        self.snap_n += 1
        base = os.path.join(LOG_DIR, f"snap_{self.stamp}_{self.snap_n:02d}")
        cv2.imwrite(base + "_view.png", self.view)
        self.log(f"snapshot saved: {os.path.basename(base)}_view.png "
                 f"(centre {fmt_m(self.readings.get('center'))}, nearest {fmt_m(self.readings.get('nearest'))})",
                 show=True)


def main():
    ap = argparse.ArgumentParser(description="Live stereo depth from the phone's main + ultra-wide cameras")
    ap.add_argument("--camera", default="4", help="logical multi-camera id on the phone")
    ap.add_argument("--size", default="1280x960")
    ap.add_argument("--quality", type=int, default=80)
    ap.add_argument("--focus", type=float, default=1.5, help="diopters (1.5 = 67 cm); 0 = autofocus")
    ap.add_argument("--proc-width", type=int, default=640, help="depth is computed at this width")
    ap.add_argument("--headless", action="store_true", help="no window (logs + logs/latest_view.jpg only)")
    ap.add_argument("--duration", type=float, default=0, help="stop after N seconds")
    ap.add_argument("--no-phone", action="store_true", help="streamer already running on the phone")
    ap.add_argument("--host", help="phone IP: connect to the PhoneStereo app over Wi-Fi (no adb)")
    ap.add_argument("--token", help="pairing code shown in the phone app")
    args = ap.parse_args()
    try:
        Viewer(args).run()
    except KeyboardInterrupt:
        pass
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
