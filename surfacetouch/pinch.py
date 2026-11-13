"""Pinch as the click: the wall position comes from the pointing fingertip (which works well), and contact comes
from thumb + index closing (which the hand tracker sees well), not from guessing whether the finger touches the wall.

  quick pinch            -> CLICK (tap)
  pinch and hold / move  -> mouse held down: drag, until the fingers open
  pinch + fast sideways  -> flick: next / previous slide in Live mode

Pinch measure: thumb-tip to index-tip distance / palm length (wrist to index knuckle), in the phone view and, when the
Mac camera sees the same hand, in the Mac view too (the larger of the two counts, so one foreshortened view can't fake
a pinch). Closing the fingers moves the index tip, so the cursor is anchored where it pointed just before the fingers
started to close, and a drag follows the pinch point with that same offset (no jump on press or release).
"""
import collections

import numpy as np

ENTER, EXIT = 0.28, 0.40        # pinch ratio: closed below ENTER, open again above EXIT
APPROACH = 0.60                 # fingers start closing below this: remember the cursor here
SETTLE = 0.20                   # s to hold the cursor still after release
DEAD = 25.0                     # wall pt the pinch must move before a drag starts (fingers settling isn't a drag)


def ratio(lm):
    """Thumb tip to index tip, in palm lengths."""
    if lm is None:
        return None
    palm = float(np.linalg.norm(np.asarray(lm[5], float) - np.asarray(lm[0], float)))
    if palm < 1e-3:
        return None
    return float(np.linalg.norm(np.asarray(lm[4], float) - np.asarray(lm[8], float))) / palm


def combined(r_phone, r_mac):
    vals = [r for r in (r_phone, r_mac) if r is not None]
    return max(vals) if vals else None


def prob(r):
    """Pinch 'probability' for the touch state machine: 0.5 at the middle of ENTER..EXIT."""
    if r is None:
        return 0.0
    mid, width = (ENTER + EXIT) / 2, (EXIT - ENTER) / 4
    return float(1 / (1 + np.exp((r - mid) / width)))


class Cursor:
    def __init__(self):
        self.closed = False
        self.anchor = None          # cursor where the fingers started to close
        self.offset = None
        self.release_t = None
        self.last = None
        self.hist = collections.deque(maxlen=30)

    def update(self, t, r, tip, mid):
        """r: pinch ratio, tip: index tip on the wall, mid: thumb-index midpoint on the wall (all (x, y) or None)."""
        if tip is None or r is None:
            return self.last
        tip, mid = np.asarray(tip, float), np.asarray(mid, float)
        if not self.closed:
            if r < APPROACH and self.anchor is None:
                self.anchor = self.last if self.last is not None else tip
            elif r >= APPROACH:
                self.anchor = None
            if r < ENTER:
                self.closed = True
                self.anchor = self.anchor if self.anchor is not None else tip
                self.mid0, self.offset = mid, None
        elif r > EXIT:
            self.closed = False
            self.release_t = t
            self.anchor = None
        if self.closed:
            if self.offset is None and np.linalg.norm(mid - self.mid0) > DEAD:
                self.offset = self.anchor - mid           # drag starts here, continuing from the anchor
            out = self.anchor if self.offset is None else mid + self.offset
        elif self.release_t is not None and t - self.release_t < SETTLE:
            out = self.last
        else:
            out = tip if self.anchor is None else self.anchor      # closing: hold still at the anchor
        self.last = out
        return out
