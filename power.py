"""Home Assistant wrapper around PowerMachine: template tracking + debounce timer."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import TemplateError
from homeassistant.helpers.event import (
    TrackTemplate,
    TrackTemplateResult,
    async_call_later,
    async_track_template_result,
)
from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DELAY_OFF,
    CONF_DELAY_ON,
    CONF_POWER_TEMPLATE,
    CONF_RETENTION_DAYS,
    DEFAULT_DELAY_OFF,
    DEFAULT_DELAY_ON,
    DEFAULT_RETENTION_DAYS,
    EVENT_POWER_LOST,
    EVENT_POWER_RESTORED,
)
from .store import SvitloStore
from .tracker import PowerMachine, Transition, parse_bool

_LOGGER = logging.getLogger(__name__)


class PowerTracker:
    """Observes the power template and maintains the outage log."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        store: SvitloStore,
        fire: Callable[[str, dict[str, Any]], None],
        on_change: Callable[[], None],
    ) -> None:
        self.hass = hass
        self._store = store
        self._fire = fire
        self._on_change = on_change
        opts = entry.options
        delay_on = float(opts.get(CONF_DELAY_ON, DEFAULT_DELAY_ON))
        delay_off = float(opts.get(CONF_DELAY_OFF, DEFAULT_DELAY_OFF))
        self._retention = timedelta(
            days=opts.get(CONF_RETENTION_DAYS, DEFAULT_RETENTION_DAYS)
        )
        self.machine = (
            PowerMachine.from_dict(store.power, delay_on, delay_off)
            if store.power
            else PowerMachine(delay_on, delay_off)
        )
        store.get_power = self.machine.to_dict
        self._template = Template(opts[CONF_POWER_TEMPLATE], hass)
        self._info: Any = None
        self._timer: CALLBACK_TYPE | None = None

    # ------------------------------------------------------------------ lifecycle
    @callback
    def async_start(self) -> None:
        self._info = async_track_template_result(
            self.hass, [TrackTemplate(self._template, None)], self._template_updated
        )
        self._feed(self._render())

    @callback
    def async_stop(self) -> None:
        if self._info is not None:
            self._info.async_remove()
            self._info = None
        self._cancel_timer()

    # ------------------------------------------------------------------ internals
    def _render(self) -> bool | None:
        try:
            return parse_bool(self._template.async_render(parse_result=False))
        except TemplateError as err:
            _LOGGER.warning("Power template error: %s", err)
            return None

    @callback
    def _template_updated(self, event: Any, updates: list[TrackTemplateResult]) -> None:
        result = updates[-1].result
        if isinstance(result, TemplateError):
            _LOGGER.warning("Power template error: %s", result)
            return
        self._feed(parse_bool(result))

    def _cancel_timer(self) -> None:
        if self._timer:
            self._timer()
            self._timer = None

    @callback
    def _feed(self, raw: bool | None) -> None:
        transition, delay = self.machine.update_raw(raw, dt_util.utcnow())
        if self.machine.pending is None:
            self._cancel_timer()
        if transition:
            self._emit(transition)
        if delay and self._timer is None:
            self._timer = async_call_later(self.hass, delay, self._timer_fired)

    @callback
    def _timer_fired(self, _now: datetime) -> None:
        self._timer = None
        transition = self.machine.confirm(dt_util.utcnow())
        if transition:
            self._emit(transition)

    def _emit(self, transition: Transition) -> None:
        self.machine.purge(dt_util.utcnow() - self._retention)
        self._store.schedule_save()
        if not transition.initial:
            self._fire(
                EVENT_POWER_RESTORED if transition.state else EVENT_POWER_LOST,
                {"at": transition.at.isoformat()},
            )
        self._on_change()
