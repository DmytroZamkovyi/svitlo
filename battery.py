"""Battery calculations. Pure Python, no Home Assistant imports.

Sign convention everywhere: power > 0 = discharging, power < 0 = charging.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

IDLE_W = 20.0  # |power| below this counts as idle
SMOOTH = timedelta(minutes=5)  # averaging window for the "current" load
TREND = timedelta(minutes=30)  # look-back for power derived from counters / SoC
MIN_TREND = timedelta(minutes=3)

STATE_CHARGING = "charging"
STATE_DISCHARGING = "discharging"
STATE_IDLE = "idle"
STATE_UNKNOWN = "unknown"


@dataclass
class BatteryConfig:
    capacity_kwh: float | None = None
    reserve_pct: float = 0.0
    target_pct: float = 100.0
    max_discharge_w: float | None = None


class LiveBattery:
    """Current state of one battery, smoothed over the last minutes."""

    def __init__(self, cfg: BatteryConfig) -> None:
        self.cfg = cfg
        self.soc: float | None = None
        self._power: deque[tuple[datetime, float]] = deque()
        self._soc: deque[tuple[datetime, float]] = deque()
        self._energy: deque[tuple[datetime, float, float]] = deque()

    # ------------------------------------------------------------------ input
    def set_soc(self, ts: datetime, value: float | None) -> None:
        self.soc = value
        if value is None:
            self._soc.clear()
            return
        self._soc.append((ts, value))
        self._prune(self._soc, ts)

    def set_power(self, ts: datetime, watts: float | None) -> None:
        """Signed power, positive = discharging. None = sensor unknown."""
        if watts is None:
            self._power.clear()
            return
        self._power.append((ts, watts))
        while len(self._power) > 1 and self._power[1][0] <= ts - SMOOTH:
            self._power.popleft()

    def set_energy(self, ts: datetime, charged_kwh: float, discharged_kwh: float) -> None:
        """Cumulative energy counters (kWh)."""
        self._energy.append((ts, charged_kwh, discharged_kwh))
        self._prune(self._energy, ts)

    @staticmethod
    def _prune(dq: deque, now: datetime) -> None:
        while len(dq) > 2 and dq[1][0] <= now - TREND:
            dq.popleft()

    # ------------------------------------------------------------------ power
    def _measured(self, now: datetime) -> float | None:
        pts = list(self._power)
        if not pts:
            return None
        lo = now - SMOOTH
        total = dur = 0.0
        for i, (t, w) in enumerate(pts):
            end = pts[i + 1][0] if i + 1 < len(pts) else now
            s, e = max(t, lo), min(end, now)
            if e > s:
                seconds = (e - s).total_seconds()
                total += w * seconds
                dur += seconds
        return total / dur if dur > 0 else pts[-1][1]

    def _from_energy(self, now: datetime) -> float | None:
        if len(self._energy) < 2:
            return None
        old, new = self._energy[0], self._energy[-1]
        dt = (new[0] - old[0]).total_seconds() / 3600
        if new[0] - old[0] < MIN_TREND:
            return None
        d_ch, d_dis = new[1] - old[1], new[2] - old[2]
        if d_ch < 0 or d_dis < 0:  # counter reset
            return None
        return (d_dis - d_ch) * 1000 / dt

    def _from_soc(self, now: datetime) -> float | None:
        cap = self.cfg.capacity_kwh
        if not cap or len(self._soc) < 2:
            return None
        old, new = self._soc[0], self._soc[-1]
        if new[0] - old[0] < MIN_TREND or new[1] == old[1]:
            return None  # no visible change: cannot tell idle from a slow trickle
        dt = (new[0] - old[0]).total_seconds() / 3600
        return -(new[1] - old[1]) / 100 * cap * 1000 / dt

    def power_w(self, now: datetime, smooth: bool = True) -> float | None:
        """Signed power. smooth=False: the latest reading, smooth=True: the average
        of the last minutes. Without a power sensor both come from the energy
        counters or the charge trend, which are averages by nature."""
        if not smooth and self._power:
            return self._power[-1][1]
        for source in (self._measured, self._from_energy, self._from_soc):
            value = source(now)
            if value is not None:
                return value
        return None

    def state(self, now: datetime, smooth: bool = True) -> str:
        p = self.power_w(now, smooth)
        if p is None:
            return STATE_UNKNOWN
        if p > IDLE_W:
            return STATE_DISCHARGING
        if p < -IDLE_W:
            return STATE_CHARGING
        return STATE_IDLE

    def discharge_w(self, now: datetime, smooth: bool = True) -> float | None:
        p = self.power_w(now, smooth)
        return None if p is None else max(p, 0.0)

    def charge_w(self, now: datetime, smooth: bool = True) -> float | None:
        p = self.power_w(now, smooth)
        return None if p is None else max(-p, 0.0)

    # ----------------------------------------------------------------- energy
    def stored_kwh(self) -> float | None:
        cap = self.cfg.capacity_kwh
        if self.soc is None or not cap:
            return None
        return cap * self.soc / 100

    def usable_kwh(self) -> float | None:
        cap = self.cfg.capacity_kwh
        if self.soc is None or not cap:
            return None
        return cap * max(self.soc - self.cfg.reserve_pct, 0.0) / 100

    def time_to_empty_h(self, now: datetime, smooth: bool = True) -> float | None:
        usable = self.usable_kwh()
        if usable is None:
            return None
        if usable <= 0:
            return 0.0
        p = self.power_w(now, smooth)
        if p is None or p <= IDLE_W:
            return None
        return usable / (p / 1000)

    def time_to_full_h(self, now: datetime, smooth: bool = True) -> float | None:
        cap = self.cfg.capacity_kwh
        if self.soc is None or not cap:
            return None
        missing = cap * max(self.cfg.target_pct - self.soc, 0.0) / 100
        if missing <= 0:
            return 0.0
        p = self.power_w(now, smooth)
        if p is None or p >= -IDLE_W:
            return None
        return missing / (-p / 1000)

    def load_percent(self, now: datetime, smooth: bool = True) -> float | None:
        maxw = self.cfg.max_discharge_w
        d = self.discharge_w(now, smooth)
        if not maxw or d is None:
            return None
        return d / maxw * 100
