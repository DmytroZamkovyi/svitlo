"""Client for the public Yasno blackout-service API."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

BASE_URL = "https://app.yasno.ua/api/blackout-service/public/shutdowns"
TIMEOUT = 20


class YasnoError(Exception):
    """Network or protocol error while talking to Yasno."""


class AddressNotFoundError(YasnoError):
    """Saved address could not be found in Yasno anymore."""


def item_name(item: Any) -> str:
    """Return a display name for a region/dso/street/house item."""
    if isinstance(item, dict):
        for key in ("name", "value", "label"):
            if item.get(key):
                return str(item[key])
    return str(item)


class YasnoClient:
    """Thin async wrapper around the Yasno endpoints."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            async with asyncio.timeout(TIMEOUT):
                resp = await self._session.get(f"{BASE_URL}/{path}", params=params)
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise YasnoError(f"{path}: {err}") from err

    async def get_regions(self) -> list[dict[str, Any]]:
        data = await self._get("addresses/v2/regions")
        if not isinstance(data, list):
            raise YasnoError("regions: unexpected response")
        return data

    async def search_streets(
        self, region_id: int, dso_id: int, query: str
    ) -> list[dict[str, Any]]:
        data = await self._get(
            "addresses/v2/streets",
            {"RegionId": region_id, "DsoId": dso_id, "query": query},
        )
        return data if isinstance(data, list) else []

    async def search_houses(
        self, region_id: int, dso_id: int, street_id: int, query: str
    ) -> list[dict[str, Any]]:
        data = await self._get(
            "addresses/v2/houses",
            {
                "RegionId": region_id,
                "DsoId": dso_id,
                "StreetId": street_id,
                "query": query,
            },
        )
        return data if isinstance(data, list) else []

    async def get_group(
        self, region_id: int, dso_id: int, street_id: int, house_id: int
    ) -> tuple[int, int]:
        data = await self._get(
            "addresses/v2/group",
            {
                "RegionId": region_id,
                "DsoId": dso_id,
                "StreetId": street_id,
                "HouseId": house_id,
            },
        )
        if not isinstance(data, dict) or data.get("group") is None:
            raise AddressNotFoundError("group not returned")
        return int(data["group"]), int(data.get("subgroup") or 1)

    async def get_planned(
        self, region_id: int, dso_id: int, group: int, subgroup: int
    ) -> Any:
        return await self._get(
            f"regions/{region_id}/dsos/{dso_id}/planned-outages",
            {
                "regionId": region_id,
                "DsoId": dso_id,
                "groupId": group,
                "SubGroupId": subgroup,
            },
        )

    async def get_probable(
        self, region_id: int, dso_id: int, group: int, subgroup: int
    ) -> Any:
        return await self._get(
            "probable-outages",
            {
                "regionId": region_id,
                "DsoId": dso_id,
                "groupId": group,
                "SubGroupId": subgroup,
            },
        )

    async def resolve_address(
        self,
        region_id: int,
        dso_id: int,
        street_name: str,
        house_name: str,
    ) -> tuple[int, int, int, int]:
        """Find (street_id, house_id, group, subgroup) by saved names."""
        street = await self._find_exact(
            await self._street_candidates(region_id, dso_id, street_name),
            street_name,
        )
        if street is None:
            raise AddressNotFoundError(f"street {street_name!r} not found")
        street_id = int(street["id"])
        house = await self._find_exact(
            await self.search_houses(region_id, dso_id, street_id, house_name),
            house_name,
        )
        if house is None:
            raise AddressNotFoundError(f"house {house_name!r} not found")
        house_id = int(house["id"])
        group, subgroup = await self.get_group(region_id, dso_id, street_id, house_id)
        return street_id, house_id, group, subgroup

    async def _street_candidates(
        self, region_id: int, dso_id: int, name: str
    ) -> list[dict[str, Any]]:
        found = await self.search_streets(region_id, dso_id, name)
        if found:
            return found
        # Fallback: query without the leading street type ("вул.", "просп." ...)
        short = name.split(" ", 1)[-1] if " " in name else name
        if short != name:
            return await self.search_streets(region_id, dso_id, short)
        return []

    @staticmethod
    async def _find_exact(
        items: list[dict[str, Any]], name: str
    ) -> dict[str, Any] | None:
        for item in items:
            if item_name(item) == name:
                return item
        return items[0] if len(items) == 1 else None
