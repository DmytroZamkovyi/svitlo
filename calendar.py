"""Read-only calendars: current schedule, schedule history, actual outages."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .entity import SvitloEntity
from .runtime import SvitloRuntime
from .schedule import DaySchedule, diff_snapshots, ongoing_end
from .texts import fmt_duration, fmt_ranges, texts

PARALLEL_UPDATES = 0
MARKER_LENGTH = timedelta(minutes=15)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime: SvitloRuntime = entry.runtime_data
    async_add_entities(
        [
            CurrentScheduleCalendar(runtime),
            ScheduleHistoryCalendar(runtime),
            ActualOutagesCalendar(runtime),
        ]
    )


class BaseCalendar(SvitloEntity, CalendarEntity):
    """Common event/async_get_events plumbing."""

    def _events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        raise NotImplementedError

    @property
    def _t(self) -> dict[str, str]:
        return texts(self.runtime.language)

    @property
    def event(self) -> CalendarEvent | None:
        now = dt_util.now()
        events = self._events(now - timedelta(days=1), now + timedelta(days=3))
        active = [e for e in events if e.start <= now < e.end]
        if active:
            return min(active, key=lambda e: e.start)
        upcoming = [e for e in events if e.start > now]
        return min(upcoming, key=lambda e: e.start) if upcoming else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        return self._events(start_date, end_date)


class CurrentScheduleCalendar(BaseCalendar):
    """Planned outages for today and tomorrow only (no history)."""

    def __init__(self, runtime: SvitloRuntime) -> None:
        super().__init__(runtime, "current_schedule", "yasno")

    def _events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        data = self.runtime.coordinator.data
        if data is None:
            return []
        tz = self.runtime.tz
        today0 = datetime.combine(dt_util.now().date(), time(0, 0), tzinfo=tz)
        t = self._t
        return [
            CalendarEvent(
                start=iv.start,
                end=iv.end,
                summary=t[iv.kind],
                uid=f"current:{iv.kind}:{iv.start.isoformat()}:{iv.end.isoformat()}",
            )
            for iv in data.snapshot.intervals(tz)
            if iv.end > today0 and iv.end > start and iv.start < end
        ]


class ScheduleHistoryCalendar(BaseCalendar):
    """Final slots per day (frozen after the day) plus a marker per schedule change."""

    def __init__(self, runtime: SvitloRuntime) -> None:
        super().__init__(runtime, "schedule_history", "yasno")

    def _events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        tz = self.runtime.tz
        t = self._t
        versions = sorted(self.runtime.store.versions, key=lambda v: v.fetched_at)
        events: list[CalendarEvent] = []

        final: dict[date, DaySchedule] = {}
        for version in versions:
            final.update(version.days)
        for day, schedule in final.items():
            span_start = datetime.combine(day, time(0, 0), tzinfo=tz)
            if span_start + timedelta(days=1) <= start or span_start >= end:
                continue
            for iv in schedule.intervals(tz):
                events.append(
                    CalendarEvent(
                        start=iv.start,
                        end=iv.end,
                        summary=t[iv.kind],
                        uid=f"slot:{iv.kind}:{iv.start.isoformat()}:{iv.end.isoformat()}",
                    )
                )

        for index, version in enumerate(versions):
            if version.fetched_at + MARKER_LENGTH <= start or version.fetched_at >= end:
                continue
            previous = versions[index - 1] if index else None
            lines = []
            for day, before, after in diff_snapshots(previous, version):
                line = f"{day:%d.%m}: "
                if previous is not None:
                    line += f"{t['before']} {fmt_ranges(before, t)} → "
                line += f"{t['after']} {fmt_ranges(after, t)}"
                lines.append(line)
            events.append(
                CalendarEvent(
                    start=version.fetched_at,
                    end=version.fetched_at + MARKER_LENGTH,
                    summary=t["schedule_updated" if previous else "schedule_first"],
                    description="\n".join(lines) or None,
                    uid=f"version:{version.fetched_at.isoformat()}",
                )
            )
        return events


class ActualOutagesCalendar(BaseCalendar):
    """Observed outages."""

    def __init__(self, runtime: SvitloRuntime) -> None:
        super().__init__(runtime, "actual_outages", "power")

    def _events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        t = self._t
        now = dt_util.utcnow()
        events: list[CalendarEvent] = []
        for outage in self.runtime.power.machine.outages:
            finish = outage.end or ongoing_end(outage.start, now)
            if finish <= outage.start:
                finish = outage.start + timedelta(minutes=1)
            if finish <= start or outage.start >= end:
                continue
            minutes = (finish - outage.start).total_seconds() / 60
            summary = t["no_power"] + (
                f" ({t['ongoing']})" if outage.end is None else ""
            )
            events.append(
                CalendarEvent(
                    start=outage.start,
                    end=finish,
                    summary=summary,
                    description=f"{t['duration']}: {fmt_duration(minutes, t)}",
                    uid=f"outage:{outage.start.isoformat()}",
                )
            )
        return events
