"""Home Assistant glue for one battery: tracks its sensors and feeds the live model."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, State, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    EventStateChangedData,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter, PowerConverter

from . import battery
from .const import (
    CONF_BAT_CAPACITY,
    CONF_BAT_ENERGY_IN,
    CONF_BAT_ENERGY_OUT,
    CONF_BAT_MAX_POWER,
    CONF_BAT_OWN_CONSUMPTION,
    CONF_BAT_POWER,
    CONF_BAT_POWER_IN,
    CONF_BAT_POWER_MODE,
    CONF_BAT_POWER_OUT,
    CONF_BAT_RESERVE,
    CONF_BAT_SOC,
    CONF_BAT_TARGET,
    DEFAULT_BAT_RESERVE,
    DEFAULT_BAT_TARGET,
    DOMAIN,
    POWER_MODE_INVERTED,
    POWER_MODE_NONE,
    POWER_MODE_STANDARD,
    POWER_MODE_TWO,
)

_LOGGER = logging.getLogger(__name__)
TICK = timedelta(minutes=1)
UNDECIDED = {"unknown", "unavailable", "none", ""}


def _num(state: State | None, kind: str) -> float | None:
    """Numeric state converted to kWh (energy) or W (power)."""
    if state is None or state.state.lower() in UNDECIDED:
        return None
    try:
        value = float(state.state)
    except ValueError:
        return None
    unit = state.attributes.get("unit_of_measurement")
    try:
        if kind == "energy" and unit and unit != "kWh":
            return EnergyConverter.convert(value, unit, "kWh")
        if kind == "power" and unit and unit != "W":
            return PowerConverter.convert(value, unit, "W")
    except Exception:  # noqa: BLE001 - unknown unit: take the value as is
        return value
    return value


class BatteryManager:
    """Live values and window statistics for one battery sub-entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, sub: ConfigSubentry) -> None:
        self.hass = hass
        self.entry = entry
        self.subentry_id = sub.subentry_id
        self.title = sub.title
        d = sub.data
        self.soc_id: str | None = d.get(CONF_BAT_SOC)
        self.energy_in_id: str | None = d.get(CONF_BAT_ENERGY_IN)
        self.energy_out_id: str | None = d.get(CONF_BAT_ENERGY_OUT)
        self.mode: str = d.get(CONF_BAT_POWER_MODE, POWER_MODE_NONE)
        self.power_id: str | None = d.get(CONF_BAT_POWER)
        self.power_in_id: str | None = d.get(CONF_BAT_POWER_IN)
        self.power_out_id: str | None = d.get(CONF_BAT_POWER_OUT)
        self.cfg = battery.BatteryConfig(
            capacity_kwh=d.get(CONF_BAT_CAPACITY) or None,
            reserve_pct=float(d.get(CONF_BAT_RESERVE, DEFAULT_BAT_RESERVE)),
            target_pct=float(d.get(CONF_BAT_TARGET, DEFAULT_BAT_TARGET)),
            max_discharge_w=d.get(CONF_BAT_MAX_POWER) or None,
            own_consumption_w=float(d.get(CONF_BAT_OWN_CONSUMPTION) or 0),
        )
        self.live = battery.LiveBattery(self.cfg)
        self.signal = f"{DOMAIN}_{entry.entry_id}_{sub.subentry_id}_battery"
        self._unsubs: list[CALLBACK_TYPE] = []

    # ---------------------------------------------------------------- config
    @property
    def has_power_sensor(self) -> bool:
        return self.mode != POWER_MODE_NONE

    @property
    def has_energy(self) -> bool:
        return bool(self.energy_in_id or self.energy_out_id)

    def _power_ids(self) -> list[str]:
        if self.mode == POWER_MODE_TWO:
            return [i for i in (self.power_in_id, self.power_out_id) if i]
        if self.mode in (POWER_MODE_STANDARD, POWER_MODE_INVERTED) and self.power_id:
            return [self.power_id]
        return []

    def _tracked(self) -> list[str]:
        ids = [self.soc_id, self.energy_in_id, self.energy_out_id, *self._power_ids()]
        return [i for i in ids if i]

    # ------------------------------------------------------------ live input
    def _read(self, entity_id: str | None, kind: str) -> float | None:
        return _num(self.hass.states.get(entity_id), kind) if entity_id else None

    @callback
    def _ingest(self) -> None:
        now = dt_util.utcnow()
        if self.soc_id:
            self.live.set_soc(now, self._read(self.soc_id, "pct"))
        if self.mode == POWER_MODE_TWO:
            out = self._read(self.power_out_id, "power")
            inn = self._read(self.power_in_id, "power")
            if out is None and inn is None:
                self.live.set_power(now, None)
            else:
                self.live.set_power(now, (out or 0.0) - (inn or 0.0))
        elif self.mode in (POWER_MODE_STANDARD, POWER_MODE_INVERTED):
            value = self._read(self.power_id, "power")
            if value is not None and self.mode == POWER_MODE_INVERTED:
                value = -value
            self.live.set_power(now, value)
        if self.has_energy:
            inn = self._read(self.energy_in_id, "energy")
            out = self._read(self.energy_out_id, "energy")
            if inn is not None or out is not None:
                self.live.set_energy(now, inn or 0.0, out or 0.0)

    @callback
    def _on_state(self, _event: Event[EventStateChangedData]) -> None:
        self._ingest()
        self._notify()

    @callback
    def _notify(self) -> None:
        async_dispatcher_send(self.hass, self.signal)

    # ------------------------------------------------------------- lifecycle
    async def _tick(self, _now: datetime) -> None:
        # Sample every minute so that flat counters read as "no flow", not "unknown"
        self._ingest()
        self._notify()

    async def async_start(self) -> None:
        self._ingest()
        if ids := self._tracked():
            self._unsubs.append(
                async_track_state_change_event(self.hass, ids, self._on_state)
            )
        self._unsubs.append(async_track_time_interval(self.hass, self._tick, TICK))

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    def device_identifier(self, group: str) -> tuple[str, str]:
        """group: "now" (latest readings) or "avg" (averaged over 5 minutes)."""
        suffix = "" if group == "now" else f"_{group}"
        return (DOMAIN, f"{self.entry.entry_id}_battery_{self.subentry_id}{suffix}")
