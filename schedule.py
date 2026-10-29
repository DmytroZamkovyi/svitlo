"""Schedule model and logic. Pure Python, no Home Assistant imports."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any, NamedTuple

KIND_OUTAGE = "outage"
KIND_POSSIBLE = "possible"
KIND_EMERGENCY = "emergency"
SLOT_KIND = {
    "Definite": KIND_OUTAGE,
    "Possible": KIND_POSSIBLE,
    "EmergencyShutdowns": KIND_EMERGENCY,
}
OUTAGE_KINDS = (KIND_OUTAGE, KIND_EMERGENCY)

STATE_POWER_ON = "power_on"
STATE_OUTAGE = "outage"
STATE_POSSIBLE = "possible_outage"
STATE_EMERGENCY = "emergency"
STATE_UNKNOWN = "unknown"
STATES = [STATE_POWER_ON, STATE_OUTAGE, STATE_POSSIBLE, STATE_EMERGENCY, STATE_UNKNOWN]


class Interval(NamedTuple):
    start: datetime
    end: datetime
    kind: str


def minutes_to_dt(day: date, minutes: int, tz: tzinfo) -> datetime:
    """Convert "minutes from local midnight of `day`" to an aware datetime."""
    d = day + timedelta(days=minutes // 1440)
    m = minutes % 1440
    return datetime.combine(d, time(m // 60, m % 60), tzinfo=tz)


def _join(intervals: list[Interval], ignore_kind: bool = False) -> list[Interval]:
    """Sort and join touching intervals (same kind unless ignore_kind)."""
    out: list[Interval] = []
    for iv in sorted(intervals, key=lambda i: (i.start, i.end)):
        if out and out[-1].end >= iv.start and (ignore_kind or out[-1].kind == iv.kind):
            out[-1] = Interval(out[-1].start, max(out[-1].end, iv.end), out[-1].kind)
        else:
            out.append(iv)
    return out


@dataclass
class DaySchedule:
    """Slots for one calendar day (minutes from local midnight)."""

    day: date
    status: str
    slots: list[tuple[int, int, str]]

    def intervals(
        self, tz: tzinfo, kinds: tuple[str, ...] | None = None
    ) -> list[Interval]:
        out: list[Interval] = []
        for start, end, typ in sorted(self.slots):
            kind = SLOT_KIND.get(typ)
            if kind is None or (kinds and kind not in kinds):
                continue
            out.append(
                Interval(
                    minutes_to_dt(self.day, start, tz),
                    minutes_to_dt(self.day, end, tz),
                    kind,
                )
            )
        return _join(out)

    def ranges(
        self, kinds: tuple[str, ...] = OUTAGE_KINDS
    ) -> list[tuple[int, int, str]]:
        """Merged (start_min, end_min, kind) ranges for the given kinds."""
        out: list[list[Any]] = []
        for start, end, typ in sorted(self.slots):
            kind = SLOT_KIND.get(typ)
            if kind is None or kind not in kinds:
                continue
            if out and out[-1][2] == kind and out[-1][1] >= start:
                out[-1][1] = max(out[-1][1], end)
            else:
                out.append([start, end, kind])
        return [(a, b, k) for a, b, k in out]

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "slots": [list(s) for s in self.slots]}

    @classmethod
    def from_dict(cls, day: date, data: dict[str, Any]) -> DaySchedule:
        return cls(
            day,
            str(data.get("status") or ""),
            [(int(s[0]), int(s[1]), str(s[2])) for s in data.get("slots", [])],
        )


@dataclass
class Snapshot:
    """One version of the schedule as returned by Yasno."""

    fetched_at: datetime
    group: str
    updated_on: datetime | None
    days: dict[date, DaySchedule]
    _cache: dict[Any, list[Interval]] = field(
        default_factory=dict, repr=False, compare=False
    )

    def content_key(self) -> str:
        return json.dumps(
            {
                "g": self.group,
                "d": {d.isoformat(): v.to_dict() for d, v in sorted(self.days.items())},
            },
            sort_keys=True,
        )

    def intervals(
        self, tz: tzinfo, kinds: tuple[str, ...] | None = None
    ) -> list[Interval]:
        """All intervals of all days, joined across midnight (same kind)."""
        key = (str(tz), kinds)
        if key not in self._cache:
            collected: list[Interval] = []
            for day in self.days.values():
                collected.extend(day.intervals(tz, kinds))
            self._cache[key] = _join(collected)
        return self._cache[key]

    def outage_intervals(self, tz: tzinfo) -> list[Interval]:
        """Outage + emergency intervals joined regardless of kind."""
        key = (str(tz), "joined")
        if key not in self._cache:
            self._cache[key] = _join(self.intervals(tz, OUTAGE_KINDS), ignore_kind=True)
        return self._cache[key]

    def to_dict(self) -> dict[str, Any]:
        return {
            "fetched_at": self.fetched_at.isoformat(),
            "group": self.group,
            "updated_on": self.updated_on.isoformat() if self.updated_on else None,
            "days": {d.isoformat(): v.to_dict() for d, v in sorted(self.days.items())},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Snapshot:
        days = {
            date.fromisoformat(k): DaySchedule.from_dict(date.fromisoformat(k), v)
            for k, v in data.get("days", {}).items()
        }
        updated = data.get("updated_on")
        return cls(
            datetime.fromisoformat(data["fetched_at"]),
            str(data.get("group", "")),
            datetime.fromisoformat(updated) if updated else None,
            days,
        )


def _parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def parse_planned(
    raw: Any, group: str, fetched_at: datetime, tz: tzinfo
) -> Snapshot | None:
    """Build a Snapshot from the planned-outages response, or None if unusable."""
    if not isinstance(raw, dict):
        return None
    block = raw.get(group)
    if block is None and ("today" in raw or "tomorrow" in raw):
        block = raw
    if not isinstance(block, dict):
        return None
    days: dict[date, DaySchedule] = {}
    for key in ("today", "tomorrow"):
        item = block.get(key)
        if not isinstance(item, dict):
            continue
        parsed = _parse_dt(item.get("date"))
        if parsed is None:
            continue
        day = parsed.astimezone(tz).date() if parsed.tzinfo else parsed.date()
        status = str(item.get("status") or "")
        slots: list[tuple[int, int, str]] = []
        for slot in item.get("slots") or []:
            try:
                slots.append((int(slot["start"]), int(slot["end"]), str(slot["type"])))
            except (KeyError, TypeError, ValueError):
                continue
        if status == "EmergencyShutdowns" and not any(
            typ == "EmergencyShutdowns" for _, _, typ in slots
        ):
            slots = [(0, 1440, "EmergencyShutdowns")]
        days[day] = DaySchedule(day, status, slots)
    if not days:
        return None
    return Snapshot(fetched_at, group, _parse_dt(block.get("updatedOn")), days)


def current_state(snap: Snapshot | None, now: datetime, tz: tzinfo) -> str:
    """Scheduled state for `now`."""
    if snap is None:
        return STATE_UNKNOWN
    today = now.astimezone(tz).date()
    day = snap.days.get(today)
    if day is None or not day.slots:
        return STATE_UNKNOWN
    active = {iv.kind for iv in snap.intervals(tz) if iv.start <= now < iv.end}
    if KIND_EMERGENCY in active:
        return STATE_EMERGENCY
    if KIND_OUTAGE in active:
        return STATE_OUTAGE
    if KIND_POSSIBLE in active:
        return STATE_POSSIBLE
    return STATE_POWER_ON


def next_outage_start(
    snap: Snapshot | None, now: datetime, tz: tzinfo
) -> datetime | None:
    if snap is None:
        return None
    return next((iv.start for iv in snap.outage_intervals(tz) if iv.start > now), None)


def next_power_on(snap: Snapshot | None, now: datetime, tz: tzinfo) -> datetime | None:
    """End of the current outage, or of the next one if power is on now."""
    if snap is None:
        return None
    return next((iv.end for iv in snap.outage_intervals(tz) if iv.end > now), None)


def next_boundary(snap: Snapshot | None, now: datetime, tz: tzinfo) -> datetime:
    """Next moment when the scheduled state may change (slot edge or midnight)."""
    local = now.astimezone(tz)
    candidates = [
        datetime.combine(local.date() + timedelta(days=1), time(0, 0), tzinfo=tz)
    ]
    if snap is not None:
        for iv in snap.intervals(tz):
            candidates.extend(t for t in (iv.start, iv.end) if t > now)
    return min(candidates)


def timeline(snap: Snapshot | None, tz: tzinfo) -> list[dict[str, str]]:
    if snap is None:
        return []
    return [
        {"start": iv.start.isoformat(), "end": iv.end.isoformat(), "type": iv.kind}
        for iv in snap.intervals(tz)
    ]


def diff_snapshots(
    old: Snapshot | None, new: Snapshot
) -> list[tuple[date, list[tuple[int, int, str]] | None, list[tuple[int, int, str]]]]:
    """Days whose outage ranges differ: (day, before or None, after)."""
    out = []
    for day, ds in sorted(new.days.items()):
        after = ds.ranges(tuple(SLOT_KIND.values()))
        before_day = old.days.get(day) if old else None
        before = before_day.ranges(tuple(SLOT_KIND.values())) if before_day else None
        if before != after:
            out.append((day, before, after))
    return out
