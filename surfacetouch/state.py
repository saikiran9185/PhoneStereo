"""Touch state machine: turns a noisy contact probability into clean touch events.

States:  IDLE -> HOVER -> CANDIDATE -> DOWN -> DRAG -> (UP) -> HOVER/IDLE
 * TOUCH_DOWN only after the probability stays above p_down for confirm_down seconds.
 * TOUCH_UP after it stays below p_up for confirm_up seconds (hysteresis: p_up < p_down).
 * Losing the hand for lost_cancel seconds ends a touch at once (CANCEL), so a touch never sticks.
Events are dicts: {"event", "x", "y" (0..1 of the projected screen), "confidence", "t"}.
"""

IDLE, HOVER, CANDIDATE, DOWN, DRAG = "IDLE", "HOVER", "CANDIDATE", "DOWN", "DRAG"


class TouchStateMachine:
    def __init__(self, p_down=0.8, p_up=0.4, confirm_down=0.10, confirm_up=0.12, lost_cancel=0.15,
                 drag_start=0.015, click_max_s=0.45):
        self.p_down, self.p_up = p_down, p_up
        self.confirm_down, self.confirm_up, self.lost_cancel = confirm_down, confirm_up, lost_cancel
        self.drag_start, self.click_max_s = drag_start, click_max_s
        self.state = IDLE
        self.t_cand = self.t_low = self.t_lost = None
        self.down_at = self.down_t = None
        self.pos = None

    def _ev(self, name, t, p, pos=None):
        pos = self.pos if pos is None else pos
        return {"event": name, "x": None if pos is None else round(float(pos[0]), 4),
                "y": None if pos is None else round(float(pos[1]), 4), "confidence": round(float(p), 3), "t": t}

    def update(self, t, valid, p=0.0, pos=None):
        """valid: fingertip tracked this frame; p: contact probability; pos: (x, y) normalised."""
        ev = []
        if not valid:
            self.t_lost = self.t_lost or t
            if self.state in (DOWN, DRAG) and t - self.t_lost >= self.lost_cancel:
                ev.append(self._ev("CANCEL", t, 0.0))
                self.state = IDLE
            elif self.state in (HOVER, CANDIDATE) and t - self.t_lost >= self.lost_cancel:
                self.state = IDLE
            return ev
        self.t_lost = None
        self.pos = pos
        if self.state in (IDLE, HOVER):
            self.state = HOVER
            if p >= self.p_down:
                self.state, self.t_cand = CANDIDATE, t
            else:
                ev.append(self._ev("HOVER", t, p))
        if self.state == CANDIDATE:
            if p < self.p_down:
                self.state = HOVER
                ev.append(self._ev("HOVER", t, p))
            elif t - self.t_cand >= self.confirm_down:
                self.state, self.down_at, self.down_t, self.t_low = DOWN, pos, t, None
                ev.append(self._ev("TOUCH_DOWN", t, p))
            return ev
        if self.state in (DOWN, DRAG):
            if p < self.p_up:
                self.t_low = self.t_low or t
                if t - self.t_low >= self.confirm_up:
                    moved = ((pos[0] - self.down_at[0]) ** 2 + (pos[1] - self.down_at[1]) ** 2) ** 0.5
                    ev.append(self._ev("TOUCH_UP", t, p))
                    if t - self.down_t <= self.click_max_s and moved < self.drag_start:
                        ev.append(self._ev("CLICK", t, p, self.down_at))
                    self.state = HOVER
                return ev
            self.t_low = None
            moved = ((pos[0] - self.down_at[0]) ** 2 + (pos[1] - self.down_at[1]) ** 2) ** 0.5
            if self.state == DOWN and moved >= self.drag_start:
                self.state = DRAG
            ev.append(self._ev("TOUCH_MOVE", t, p))
        return ev
