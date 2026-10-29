"""Persistent storage: schedule versions, resolved address, power state."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .schedule import Snapshot

_LOGGER = logging.getLogger(__name__)
STORAGE_VERSION = 1
SAVE_DELAY = 10


class SvitloStore:
    """Everything the integration keeps between restarts."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}"
        )
        self.versions: list[Snapshot] = []
        self.address: dict[str, Any] = {}
        self.power: dict[str, Any] = {}
        self.get_power: Callable[[], dict[str, Any]] | None = None

    async def async_load(self) -> None:
        raw = await self._store.async_load() or {}
        for item in raw.get("versions", []):
            try:
                self.versions.append(Snapshot.from_dict(item))
            except (KeyError, TypeError, ValueError):
                _LOGGER.warning("Skipping unreadable schedule version in storage")
        self.versions.sort(key=lambda v: v.fetched_at)
        self.address = raw.get("address", {})
        self.power = raw.get("power", {})

    def _data(self) -> dict[str, Any]:
        return {
            "versions": [v.to_dict() for v in self.versions],
            "address": self.address,
            "power": self.get_power() if self.get_power else self.power,
        }

    def schedule_save(self) -> None:
        self._store.async_delay_save(self._data, SAVE_DELAY)

    async def async_save(self) -> None:
        await self._store.async_save(self._data())

    async def async_remove(self) -> None:
        await self._store.async_remove()

    def purge_versions(self, cutoff: datetime) -> None:
        self.versions = [v for v in self.versions if v.fetched_at >= cutoff]
