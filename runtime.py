"""Runtime object tying coordinator, power tracker and statistics together."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from . import stats
from .api import YasnoClient
from .const import (
    CONF_MATCH_MIN_OVERLAP,
    DEFAULT_MATCH_MIN_OVERLAP,
    DOMAIN,
    PERIODS,
)
from .coordinator import YasnoCoordinator
from .power import PowerTracker
from .store import SvitloStore

_LOGGER = logging.getLogger(__name__)
STATS_REFRESH = timedelta(minutes=5)


class SvitloRuntime:
    """Per-entry state shared by all platforms."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.signal = f"{DOMAIN}_{entry.entry_id}_update"
        self.event_signal = f"{DOMAIN}_{entry.entry_id}_event"
        self.store = SvitloStore(hass, entry.entry_id)
        self.client = YasnoClient(async_get_clientsession(hass))
        self.coordinator: YasnoCoordinator
        self.power: PowerTracker
        self.stats: dict[str, stats.Result] = {}
        self._unsubs: list[CALLBACK_TYPE] = []

    # ------------------------------------------------------------------ helpers
    @property
    def tz(self):
        return dt_util.get_default_time_zone()

    @property
    def language(self) -> str:
        return self.hass.config.language

    def device(self, key: str) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.entry.entry_id}_{key}")},
            entry_type=DeviceEntryType.SERVICE,
            translation_key=key,
        )

    @callback
    def fire(self, event_type: str, data: dict[str, Any]) -> None:
        async_dispatcher_send(self.hass, self.event_signal, event_type, data)

    @callback
    def changed(self) -> None:
        """Something observable changed: recompute statistics and refresh entities."""
        self.refresh_stats()
        async_dispatcher_send(self.hass, self.signal)

    @callback
    def _periodic(self, _now: datetime) -> None:
        self.changed()

    @callback
    def refresh_stats(self) -> None:
        now = dt_util.utcnow()
        machine = self.power.machine
        min_overlap = self.entry.options.get(
            CONF_MATCH_MIN_OVERLAP, DEFAULT_MATCH_MIN_OVERLAP
        )
        for period in PERIODS:
            start, end = stats.period_bounds(period, now, self.tz)
            self.stats[period] = stats.compute(
                machine.outages,
                self.store.versions,
                now,
                start,
                end,
                self.tz,
                float(min_overlap),
                machine.tracking_since,
            )

    # ------------------------------------------------------------------ lifecycle
    async def async_start(self) -> None:
        await self.store.async_load()
        # These read stored data in their constructors, so build them after loading
        self.coordinator = YasnoCoordinator(
            self.hass, self.entry, self.store, self.client, self.fire
        )
        self.power = PowerTracker(
            self.hass, self.entry, self.store, self.fire, self.changed
        )
        await self.coordinator.async_config_entry_first_refresh()
        self.power.async_start()
        self.refresh_stats()
        self._unsubs.append(self.coordinator.async_add_listener(self.changed))
        self._unsubs.append(
            async_track_time_interval(self.hass, self._periodic, STATS_REFRESH)
        )

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self.power.async_stop()
        await self.coordinator.async_shutdown()
        await self.store.async_save()

    async def async_remove_data(self) -> None:
        await self.store.async_remove()
