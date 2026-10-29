"""Power state machine with debounce. Pure Python, no Home Assistant imports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class Outage:
    """A real (observed) power outage."""

    start: datetime
    end: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat() if self.end else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Outage:
        return cls(
            datetime.fromisoformat(data["start"]),
            datetime.fromisoformat(data["end"]) if data.get("end") else None,
        )


@dataclass
class Transition:
    """A confirmed change of the power state."""

    state: bool  # True = power is on
    at: datetime  # when the change really started
    initial: bool = False  # first ever observation (no real change)


class PowerMachine:
    """Tracks the confirmed power state, ignoring short flickers."""

    def __init__(
        self,
        delay_on: float,
        delay_off: float,
        state: bool | None = None,
        since: datetime | None = None,
        tracking_since: datetime | None = None,
        outages: list[Outage] | None = None,
    ) -> None:
        self.delay_on = delay_on
        self.delay_off = delay_off
        self.state = state
        self.since = since
        self.tracking_since = tracking_since
        self.outages: list[Outage] = outages or []
        self.pending: bool | None = None
        self.pending_since: datetime | None = None

    @property
    def open_outage(self) -> Outage | None:
        if self.outages and self.outages[-1].end is None:
            return self.outages[-1]
        return None

    def update_raw(
        self, raw: bool | None, now: datetime
    ) -> tuple[Transition | None, float | None]:
        """Feed an observed raw value.

        Returns (immediate transition, delay in seconds for a confirm timer).
        A pending change that went back to the confirmed value is cancelled.
        """
        if raw is None:
            return None, None
        if self.state is None:
            self.tracking_since = self.tracking_since or now
            return self._apply(raw, now, initial=True), None
        if raw == self.state:
            self.pending = None
            self.pending_since = None
            return None, None
        if self.pending == raw:
            return None, None
        self.pending = raw
        self.pending_since = now
        delay = self.delay_on if raw else self.delay_off
        if delay <= 0:
            return self.confirm(now), None
        return None, delay

    def confirm(self, now: datetime) -> Transition | None:
        """Confirm the pending change (the debounce timer fired)."""
        if self.pending is None or self.pending_since is None:
            return None
        raw, at = self.pending, self.pending_since
        self.pending = None
        self.pending_since = None
        return self._apply(raw, at, initial=False)

    def _apply(self, state: bool, at: datetime, initial: bool) -> Transition:
        self.state = state
        self.since = at
        if not state:
            if self.open_outage is None:
                self.outages.append(Outage(at))
        elif (open_outage := self.open_outage) is not None:
            open_outage.end = max(at, open_outage.start)
        return Transition(state, at, initial)

    def purge(self, cutoff: datetime) -> bool:
        """Drop outages that ended before cutoff. Returns True if changed."""
        kept = [o for o in self.outages if o.end is None or o.end >= cutoff]
        changed = len(kept) != len(self.outages)
        self.outages = kept
        return changed

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "since": self.since.isoformat() if self.since else None,
            "tracking_since": (
                self.tracking_since.isoformat() if self.tracking_since else None
            ),
            "outages": [o.to_dict() for o in self.outages],
        }

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], delay_on: float, delay_off: float
    ) -> PowerMachine:
        def _dt(key: str) -> datetime | None:
            return datetime.fromisoformat(data[key]) if data.get(key) else None

        return cls(
            delay_on,
            delay_off,
            data.get("state"),
            _dt("since"),
            _dt("tracking_since"),
            [Outage.from_dict(o) for o in data.get("outages", [])],
        )


_TRUE = {"true", "on", "yes", "1"}
_FALSE = {"false", "off", "no", "0"}


def parse_bool(value: Any) -> bool | None:
    """Template result -> True/False, or None when undecided (unknown etc.)."""
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return None
