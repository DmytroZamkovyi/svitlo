"""Outage statistics (actual vs schedule). Pure Python, no Home Assistant imports."""

from __future__ import annotations

from bisect import bisect_right
from datetime import date, datetime, time, timedelta, tzinfo
from math import atan2, cos, pi, sin
from statistics import median
from typing import Any

from .schedule import Snapshot
from .tracker import Outage

Seg = tuple[datetime, datetime]
Result = dict[str, tuple[Any, dict[str, Any]]]

METRIC_KEYS = [
    "actual_outage_percent",
    "scheduled_outage_percent",
    "outage_count",
    "outage_duration",
    "avg_outage_duration",
    "longest_outage",
    "longest_uptime",
    "planned_realized_percent",
    "outage_planned_percent",
    "matched_count",
    "unplanned_count",
    "unplanned_duration",
    "skipped_planned_count",
    "avg_start_deviation",
    "avg_end_deviation",
    "avg_power_off_time",
    "avg_power_on_time",
    "power_duration",
    "scheduled_outage_duration",
    "scheduled_power_duration",
    "scheduled_outage_count",
    "outage_vs_schedule",
    "schedule_changes",
    "avg_schedule_publish_time",
]


def _merge(segs: list[Seg]) -> list[Seg]:
    out: list[Seg] = []
    for s, e in sorted(segs):
        if out and out[-1][1] >= s:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _clip(segs: list[Seg], lo: datetime, hi: datetime) -> list[Seg]:
    return [(max(s, lo), min(e, hi)) for s, e in segs if min(e, hi) > max(s, lo)]


def _total(segs: list[Seg]) -> float:
    return sum((e - s).total_seconds() for s, e in segs)


def _intersect(a: list[Seg], b: list[Seg]) -> list[Seg]:
    out: list[Seg] = []
    i = j = 0
    while i < len(a) and j < len(b):
        s, e = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if e > s:
            out.append((s, e))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def _circular_mean_hhmm(minutes: list[float]) -> str | None:
    if not minutes:
        return None
    x = sum(cos(2 * pi * m / 1440) for m in minutes)
    y = sum(sin(2 * pi * m / 1440) for m in minutes)
    mean = round(atan2(y, x) / (2 * pi) * 1440) % 1440
    return f"{mean // 60:02d}:{mean % 60:02d}"


def period_bounds(
    key: str, now: datetime, tz: tzinfo
) -> tuple[datetime | None, datetime]:
    """(start, end) of a statistics period. start None = from the beginning."""
    midnight = datetime.combine(now.astimezone(tz).date(), time(0, 0), tzinfo=tz)
    if key == "today":
        return midnight, now
    if key == "yesterday":
        return datetime.combine(
            midnight.date() - timedelta(days=1), time(0, 0), tzinfo=tz
        ), midnight
    if key == "week":
        return now - timedelta(days=7), now
    if key == "month":
        return now - timedelta(days=30), now
    return None, now


def _version_for(
    versions: list[Snapshot], stamps: list[datetime], at: datetime, tz: tzinfo
) -> Snapshot | None:
    """Schedule version that was current at `at` (and knew that date)."""
    day = at.astimezone(tz).date()
    idx = bisect_right(stamps, at) - 1
    for i in range(idx, -1, -1):
        if day in versions[i].days:
            return versions[i]
    for i in range(max(idx + 1, 0), len(versions)):
        if day in versions[i].days:
            return versions[i]
    return None


def compute(
    outages: list[Outage],
    versions: list[Snapshot],
    now: datetime,
    start: datetime | None,
    end: datetime,
    tz: tzinfo,
    min_overlap_min: float,
    tracking_since: datetime | None,
) -> Result:
    """Compute all metrics for [start, end]."""
    empty: Result = {k: (None, {}) for k in METRIC_KEYS}
    end = min(end, now)
    if tracking_since is None:
        return empty
    start = max(start, tracking_since) if start else tracking_since
    if end <= start:
        return empty
    period_len = (end - start).total_seconds()
    versions = sorted(versions, key=lambda v: v.fetched_at)
    stamps = [v.fetched_at for v in versions]

    all_actual = _merge([(o.start, o.end or now) for o in outages])
    actual = _clip(all_actual, start, end)
    actual_total = _total(actual)
    res: Result = {}
    res["actual_outage_percent"] = (round(actual_total / period_len * 100, 1), {})
    res["outage_duration"] = (round(actual_total / 60, 1), {})

    # --- schedule coverage: final version of every date inside the period
    final: dict[date, Any] = {}
    for v in versions:
        for d, ds in v.days.items():
            final[d] = ds
    planned_raw: list[Seg] = []
    covered: list[Seg] = []
    for d, ds in final.items():
        span = (
            datetime.combine(d, time(0, 0), tzinfo=tz),
            datetime.combine(d + timedelta(days=1), time(0, 0), tzinfo=tz),
        )
        if span[1] <= start or span[0] >= end:
            continue
        covered.append(span)
        planned_raw.extend(
            (iv.start, iv.end) for iv in ds.intervals(tz, ("outage", "emergency"))
        )
    planned_all = _merge(planned_raw)
    covered = _merge(covered)
    planned = _clip(planned_all, start, end)
    covered_c = _clip(covered, start, end)
    covered_len = _total(covered_c)
    planned_total = _total(planned)
    actual_cov = _intersect(actual, covered_c)
    actual_cov_total = _total(actual_cov)
    overlap_total = _total(_intersect(actual, planned))

    res["scheduled_outage_percent"] = (
        round(planned_total / covered_len * 100, 1) if covered_len else None,
        {},
    )
    res["planned_realized_percent"] = (
        round(overlap_total / planned_total * 100, 1) if planned_total else None,
        {},
    )
    res["outage_planned_percent"] = (
        round(overlap_total / actual_cov_total * 100, 1) if actual_cov_total else None,
        {},
    )
    res["unplanned_duration"] = (
        round((actual_cov_total - overlap_total) / 60, 1) if covered_len else None,
        {},
    )

    # --- per-outage matching against the schedule version current at its start
    considered = sorted(
        (o for o in outages if start <= o.start < end), key=lambda o: o.start
    )
    candidates: list[tuple[float, int, Any, Any]] = []
    own_overlap: dict[int, float] = {}
    has_schedule: dict[int, bool] = {}
    for i, o in enumerate(considered):
        version = _version_for(versions, stamps, o.start, tz)
        has_schedule[i] = version is not None
        if version is None:
            continue
        o_end = o.end or now
        dur = (o_end - o.start).total_seconds()
        own_overlap[i] = 0.0
        for iv in version.outage_intervals(tz):
            ov = (min(o_end, iv.end) - max(o.start, iv.start)).total_seconds()
            if ov <= 0:
                continue
            own_overlap[i] += ov
            need = min(min_overlap_min * 60, dur, (iv.end - iv.start).total_seconds())
            if ov >= need:
                candidates.append((ov, i, iv, o))
    candidates.sort(key=lambda c: -c[0])
    used_o: set[int] = set()
    used_p: set[tuple[datetime, datetime]] = set()
    pairs: list[tuple[Outage, Any]] = []
    for _ov, i, iv, o in candidates:
        key = (iv.start, iv.end)
        if i in used_o or key in used_p:
            continue
        used_o.add(i)
        used_p.add(key)
        pairs.append((o, iv))

    unplanned = 0
    for i, o in enumerate(considered):
        if i in used_o or not has_schedule[i]:
            continue
        dur = ((o.end or now) - o.start).total_seconds()
        if own_overlap.get(i, 0.0) < 0.5 * dur:
            unplanned += 1
    res["matched_count"] = (len(pairs), {})
    res["unplanned_count"] = (unplanned if any(has_schedule.values()) else None, {})

    skipped = 0
    for iv_s, iv_e in planned_all:
        if iv_s < start or iv_e > end:
            continue
        need = min(min_overlap_min * 60, (iv_e - iv_s).total_seconds())
        if _total(_intersect(all_actual, [(iv_s, iv_e)])) < need:
            skipped += 1
    res["skipped_planned_count"] = (skipped if covered_len else None, {})

    def _dev(values: list[float]) -> tuple[Any, dict[str, Any]]:
        if not values:
            return None, {}
        return round(sum(values) / len(values), 1), {
            "median": round(median(values), 1),
            "min": round(min(values), 1),
            "max": round(max(values), 1),
            "samples": len(values),
        }

    res["avg_start_deviation"] = _dev(
        [(o.start - iv.start).total_seconds() / 60 for o, iv in pairs]
    )
    res["avg_end_deviation"] = _dev(
        [(o.end - iv.end).total_seconds() / 60 for o, iv in pairs if o.end]
    )

    # --- general
    durations = [((o.end or now) - o.start).total_seconds() / 60 for o in considered]
    res["outage_count"] = (len(considered), {})
    res["avg_outage_duration"] = (
        (round(sum(durations) / len(durations), 1), {}) if durations else (None, {})
    )
    res["longest_outage"] = (round(max(durations), 1), {}) if durations else (None, {})
    gaps: list[float] = []
    cursor = start
    for s, e in actual:
        gaps.append((s - cursor).total_seconds())
        cursor = e
    gaps.append((end - cursor).total_seconds())
    res["longest_uptime"] = (round(max(gaps) / 60, 1), {})

    def _minute_of_day(dt: datetime) -> float:
        local = dt.astimezone(tz)
        return local.hour * 60 + local.minute + local.second / 60

    res["avg_power_off_time"] = (
        _circular_mean_hhmm([_minute_of_day(o.start) for o in considered]),
        {},
    )
    res["avg_power_on_time"] = (
        _circular_mean_hhmm([_minute_of_day(o.end) for o in considered if o.end]),
        {},
    )

    # --- schedule-oriented
    res["power_duration"] = (round((period_len - actual_total) / 60, 1), {})
    if covered_len:
        res["scheduled_outage_duration"] = (round(planned_total / 60, 1), {})
        res["scheduled_power_duration"] = (
            round((covered_len - planned_total) / 60, 1),
            {},
        )
        res["scheduled_outage_count"] = (
            sum(1 for s, e in planned_all if e > start and s < end),
            {},
        )
        res["outage_vs_schedule"] = (
            round((actual_cov_total - planned_total) / 60, 1),
            {},
        )
    res["schedule_changes"] = (
        sum(1 for v in versions[1:] if start <= v.fetched_at < end),
        {},
    )
    # When the schedule for a day first appeared in advance (the day before).
    first_seen: dict[date, datetime] = {}
    for v in versions:
        for d, ds in v.days.items():
            if ds.slots and d not in first_seen:
                first_seen[d] = v.fetched_at
    baseline = versions[0].fetched_at if versions else None
    published = []
    for d, seen in first_seen.items():
        midnight = datetime.combine(d, time(0, 0), tzinfo=tz)
        if seen == baseline or seen >= midnight or not start <= seen < end:
            continue
        published.append(_minute_of_day(seen))
    res["avg_schedule_publish_time"] = (
        _circular_mean_hhmm(published),
        {"samples": len(published)} if published else {},
    )
    return {k: res.get(k, (None, {})) for k in METRIC_KEYS}
