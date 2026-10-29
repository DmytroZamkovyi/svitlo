"""Coordinator: Yasno schedule + periodic address (group) re-resolution."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import AddressNotFoundError, YasnoClient, YasnoError
from .const import (
    CONF_ADDRESS_FAIL_LIMIT,
    CONF_ADDRESS_INTERVAL,
    CONF_DSO_ID,
    CONF_GROUP,
    CONF_HOUSE_ID,
    CONF_HOUSE_NAME,
    CONF_REGION_ID,
    CONF_RETENTION_DAYS,
    CONF_SCHEDULE_INTERVAL,
    CONF_STREET_ID,
    CONF_STREET_NAME,
    CONF_SUBGROUP,
    DEFAULT_ADDRESS_FAIL_LIMIT,
    DEFAULT_ADDRESS_INTERVAL,
    DEFAULT_RETENTION_DAYS,
    DEFAULT_SCHEDULE_INTERVAL,
    DOMAIN,
    EVENT_GROUP_CHANGED,
    EVENT_SCHEDULE_CHANGED,
    ISSUE_ADDRESS_NOT_FOUND,
    ISSUE_STALE_DATA,
    STALE_FAIL_LIMIT,
)
from .schedule import Snapshot, diff_snapshots, next_boundary, parse_planned
from .store import SvitloStore

_LOGGER = logging.getLogger(__name__)

FireEvent = Callable[[str, dict[str, Any]], None]


@dataclass
class ScheduleData:
    """Latest usable schedule."""

    snapshot: Snapshot
    probable: list[Any] | None
    stale: bool
    updated: datetime


def extract_probable(raw: Any, region: int, dso: int, group: str) -> list[Any] | None:
    try:
        slots = raw[str(region)]["dsos"][str(dso)]["groups"][group]["slots"]
    except (KeyError, TypeError):
        return None
    return slots if isinstance(slots, list) else None


class YasnoCoordinator(DataUpdateCoordinator[ScheduleData]):
    """Fetches the schedule and keeps street/house IDs and group fresh."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        store: SvitloStore,
        client: YasnoClient,
        fire: FireEvent,
    ) -> None:
        opts = entry.options
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                minutes=opts.get(CONF_SCHEDULE_INTERVAL, DEFAULT_SCHEDULE_INTERVAL)
            ),
        )
        self.store = store
        self.client = client
        self._fire = fire
        self._address_interval = timedelta(
            minutes=opts.get(CONF_ADDRESS_INTERVAL, DEFAULT_ADDRESS_INTERVAL)
        )
        self._address_fail_limit = opts.get(
            CONF_ADDRESS_FAIL_LIMIT, DEFAULT_ADDRESS_FAIL_LIMIT
        )
        self._retention = timedelta(
            days=opts.get(CONF_RETENTION_DAYS, DEFAULT_RETENTION_DAYS)
        )
        self._last_resolve: datetime | None = None
        self._address_failures = 0
        self._failures = 0
        self._unsub_boundary: CALLBACK_TYPE | None = None
        self.address = self._initial_address()

    # ------------------------------------------------------------------ address
    def _initial_address(self) -> dict[str, Any]:
        data = self.config_entry.data
        saved = self.store.address
        if (
            saved.get(CONF_STREET_NAME) == data[CONF_STREET_NAME]
            and saved.get(CONF_HOUSE_NAME) == data[CONF_HOUSE_NAME]
            and saved.get(CONF_GROUP) is not None
        ):
            return dict(saved)
        return {
            CONF_STREET_NAME: data[CONF_STREET_NAME],
            CONF_HOUSE_NAME: data[CONF_HOUSE_NAME],
            CONF_STREET_ID: data[CONF_STREET_ID],
            CONF_HOUSE_ID: data[CONF_HOUSE_ID],
            CONF_GROUP: data[CONF_GROUP],
            CONF_SUBGROUP: data[CONF_SUBGROUP],
        }

    @property
    def group_key(self) -> str:
        return f"{self.address[CONF_GROUP]}.{self.address[CONF_SUBGROUP]}"

    async def _async_resolve_address(self) -> None:
        """Re-resolve street/house IDs and group by their saved names."""
        data = self.config_entry.data
        try:
            street_id, house_id, group, subgroup = await self.client.resolve_address(
                data[CONF_REGION_ID],
                data[CONF_DSO_ID],
                data[CONF_STREET_NAME],
                data[CONF_HOUSE_NAME],
            )
        except AddressNotFoundError as err:
            self._address_failures += 1
            _LOGGER.warning(
                "Address lookup failed (%s/%s): %s",
                self._address_failures,
                self._address_fail_limit,
                err,
            )
            if self._address_failures >= self._address_fail_limit:
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    ISSUE_ADDRESS_NOT_FOUND,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=ISSUE_ADDRESS_NOT_FOUND,
                )
            return
        self._address_failures = 0
        ir.async_delete_issue(self.hass, DOMAIN, ISSUE_ADDRESS_NOT_FOUND)
        old_key = self.group_key
        self.address.update(
            {
                CONF_STREET_ID: street_id,
                CONF_HOUSE_ID: house_id,
                CONF_GROUP: group,
                CONF_SUBGROUP: subgroup,
            }
        )
        self.store.address = dict(self.address)
        self.store.schedule_save()
        if self.group_key != old_key:
            _LOGGER.info("Outage group changed: %s -> %s", old_key, self.group_key)
            self._fire(
                EVENT_GROUP_CHANGED, {"old_group": old_key, "new_group": self.group_key}
            )

    # ------------------------------------------------------------------ update
    async def _async_update_data(self) -> ScheduleData:
        now = dt_util.utcnow()
        tz = dt_util.get_default_time_zone()
        previous = self.data
        try:
            if (
                self._last_resolve is None
                or now - self._last_resolve >= self._address_interval
            ):
                self._last_resolve = now
                await self._async_resolve_address()
            data = self.config_entry.data
            group, subgroup = self.address[CONF_GROUP], self.address[CONF_SUBGROUP]
            planned = await self.client.get_planned(
                data[CONF_REGION_ID], data[CONF_DSO_ID], group, subgroup
            )
            snapshot = parse_planned(planned, self.group_key, now, tz)
            if snapshot is None:
                raise YasnoError("planned-outages: unexpected response")
            try:
                probable_raw = await self.client.get_probable(
                    data[CONF_REGION_ID], data[CONF_DSO_ID], group, subgroup
                )
                probable = extract_probable(
                    probable_raw,
                    data[CONF_REGION_ID],
                    data[CONF_DSO_ID],
                    self.group_key,
                )
            except YasnoError:
                probable = previous.probable if previous else None
        except YasnoError as err:
            self._failures += 1
            if self._failures >= STALE_FAIL_LIMIT:
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    ISSUE_STALE_DATA,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=ISSUE_STALE_DATA,
                )
            if previous is None:
                raise UpdateFailed(str(err)) from err
            _LOGGER.warning("Yasno update failed, keeping last data: %s", err)
            self._schedule_boundary()
            return replace(previous, stale=True)

        self._failures = 0
        ir.async_delete_issue(self.hass, DOMAIN, ISSUE_STALE_DATA)
        self._register_version(snapshot)
        self._schedule_boundary(snapshot)
        return ScheduleData(snapshot, probable, False, now)

    def _register_version(self, snapshot: Snapshot) -> None:
        versions = self.store.versions
        last = versions[-1] if versions else None
        if last is not None and last.content_key() == snapshot.content_key():
            return
        versions.append(snapshot)
        self.store.purge_versions(snapshot.fetched_at - self._retention)
        self.store.schedule_save()
        if last is not None:
            changed = diff_snapshots(last, snapshot)
            self._fire(
                EVENT_SCHEDULE_CHANGED,
                {
                    "group": snapshot.group,
                    "changed_days": [d.isoformat() for d, _, _ in changed],
                },
            )

    # ------------------------------------------------------------------ timers
    @callback
    def _schedule_boundary(self, snapshot: Snapshot | None = None) -> None:
        """Wake listeners when the scheduled state may change (slot edge, midnight)."""
        if self._unsub_boundary:
            self._unsub_boundary()
            self._unsub_boundary = None
        if snapshot is None and self.data is not None:
            snapshot = self.data.snapshot
        tz = dt_util.get_default_time_zone()
        when = next_boundary(snapshot, dt_util.utcnow(), tz) + timedelta(seconds=1)
        self._unsub_boundary = async_track_point_in_time(
            self.hass, self._boundary_reached, when
        )

    @callback
    def _boundary_reached(self, _now: datetime) -> None:
        self._unsub_boundary = None
        self.async_update_listeners()
        self._schedule_boundary()

    async def async_shutdown(self) -> None:
        if self._unsub_boundary:
            self._unsub_boundary()
            self._unsub_boundary = None
        await super().async_shutdown()
