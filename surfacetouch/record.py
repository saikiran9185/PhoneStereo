"""Session recorder: video of every camera + the projected wall + the diagnostics view, synced to the data.

Files per session (logs/surface_<time>_video/):
  main.mp4, wide.mp4   raw phone frames (each phone frame once) - can be re-analysed offline
  mac.mp4              Mac camera frame nearest each phone frame
  review.mp4           what you watch: diagnostics + projected wall side by side
  frames.csv           one row per phone frame: index, times, mode, cue, button, contact p, touch state, tip position
Writing runs on its own thread so the touch loop never waits for the disk; frames are dropped (and counted)
only if the disk falls behind.
"""
import csv
import os
import queue
import threading

import cv2
import numpy as np

FPS = 20.0


class Recorder(threading.Thread):
    def __init__(self, folder, log=print):
        super().__init__(daemon=True)
        self.folder, self.log = folder, log
        os.makedirs(folder, exist_ok=True)
        self.q = queue.Queue(maxsize=120)
        self.writers, self.sizes, self.pending = {}, {}, {}
        self.dropped = self.n = 0
        self.running = True
        self.csv = open(os.path.join(folder, "frames.csv"), "w", newline="")
        self.cw = None

    def add(self, images, row):
        """images: {name: BGR image or None}; row: dict for frames.csv."""
        try:
            self.q.put_nowait((images, row))
        except queue.Full:
            self.dropped += 1

    def _write(self, name, img):
        if img is None:
            if name not in self.sizes:              # camera not started yet: pad with black once it starts
                self.pending[name] = self.pending.get(name, 0) + 1
                return
            img = np.zeros((self.sizes[name][1], self.sizes[name][0], 3), np.uint8)
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        if name not in self.writers:
            h, w = img.shape[:2]
            w, h = w - w % 2, h - h % 2
            self.sizes[name] = (w, h)
            self.writers[name] = cv2.VideoWriter(os.path.join(self.folder, f"{name}.mp4"),
                                                 cv2.VideoWriter_fourcc(*"avc1"), FPS, (w, h))
            if not self.writers[name].isOpened():           # fall back if H.264 is unavailable
                self.writers[name] = cv2.VideoWriter(os.path.join(self.folder, f"{name}.mp4"),
                                                     cv2.VideoWriter_fourcc(*"mp4v"), FPS, (w, h))
            for _ in range(self.pending.pop(name, 0)):        # keep every video frame-aligned with frames.csv
                self.writers[name].write(np.zeros((h, w, 3), np.uint8))
        w, h = self.sizes[name]
        if img.shape[1] != w or img.shape[0] != h:
            img = cv2.resize(img, (w, h))
        self.writers[name].write(np.ascontiguousarray(img))

    def run(self):
        while self.running or not self.q.empty():
            try:
                images, row = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            row = {"frame": self.n, **row}
            for name, img in images.items():
                self._write(name, img)
            if self.cw is None:
                self.cw = csv.DictWriter(self.csv, fieldnames=list(row.keys()), extrasaction="ignore")
                self.cw.writeheader()
            self.cw.writerow(row)
            self.n += 1
        for w in self.writers.values():
            w.release()
        self.csv.close()

    def stop(self):
        self.running = False
        self.join(timeout=10)
        self.log(f"recording saved: {self.n} frames, {self.dropped} dropped -> {self.folder}")


def review_frame(diag, canvas):
    """Diagnostics (left) + projected wall (right), same height."""
    h = diag.shape[0]
    c = cv2.resize(canvas, (int(canvas.shape[1] * h / canvas.shape[0]), h))
    return np.hstack([diag, c])
