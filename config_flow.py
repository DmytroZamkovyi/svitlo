"""Config, options and reconfigure flows."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import TemplateError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TemplateSelector,
    TextSelector,
)
from homeassistant.helpers.template import Template

from .api import AddressNotFoundError, YasnoClient, YasnoError, item_name
from .const import (
    CONF_ADDRESS_FAIL_LIMIT,
    CONF_ADDRESS_INTERVAL,
    CONF_DELAY_OFF,
    CONF_DELAY_ON,
    CONF_DSO_ID,
    CONF_DSO_NAME,
    CONF_GROUP,
    CONF_HOUSE_ID,
    CONF_HOUSE_NAME,
    CONF_MATCH_MIN_OVERLAP,
    CONF_POWER_TEMPLATE,
    CONF_REGION_ID,
    CONF_REGION_NAME,
    CONF_RETENTION_DAYS,
    CONF_SCHEDULE_INTERVAL,
    CONF_STREET_ID,
    CONF_STREET_NAME,
    CONF_SUBGROUP,
    DEFAULT_ADDRESS_FAIL_LIMIT,
    DEFAULT_ADDRESS_INTERVAL,
    DEFAULT_DELAY_OFF,
    DEFAULT_DELAY_ON,
    DEFAULT_MATCH_MIN_OVERLAP,
    DEFAULT_POWER_TEMPLATE,
    DEFAULT_RETENTION_DAYS,
    DEFAULT_SCHEDULE_INTERVAL,
    DOMAIN,
)
from .tracker import parse_bool

_LOGGER = logging.getLogger(__name__)
UNDECIDED = {"unknown", "unavailable", "none", ""}


def _select(options: list[tuple[str, str]]) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=[SelectOptionDict(value=v, label=label) for v, label in options],
            mode=SelectSelectorMode.DROPDOWN,
        )
    )


def _number(minimum: float, maximum: float) -> NumberSelector:
    # Units are in the translated labels; the selector's own unit is not translated.
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum, max=maximum, step=1, mode=NumberSelectorMode.BOX
        )
    )


def validate_template(hass: HomeAssistant, value: str) -> str | None:
    """Return an error key if the template is unusable."""
    try:
        result = Template(value, hass).async_render(parse_result=False)
    except TemplateError:
        return "invalid_template"
    if parse_bool(result) is None and str(result).strip().lower() not in UNDECIDED:
        return "template_not_boolean"
    return None


class SvitloConfigFlow(ConfigFlow, domain=DOMAIN):
    """Address (region -> DSO -> street -> house) and the power template."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._regions: list[dict[str, Any]] = []
        self._streets: list[dict[str, Any]] = []
        self._houses: list[dict[str, Any]] = []
        self._dsos: list[dict[str, Any]] = []
        self._reconfigure = False

    @property
    def _client(self) -> YasnoClient:
        return YasnoClient(async_get_clientsession(self.hass))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SvitloOptionsFlow:
        return SvitloOptionsFlow()

    # --------------------------------------------------------------- region / dso
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if not self._regions:
            try:
                self._regions = await self._client.get_regions()
            except YasnoError:
                return self.async_abort(reason="cannot_connect")
        if user_input is not None:
            region = next(
                (r for r in self._regions if str(r.get("id")) == user_input["region"]),
                None,
            )
            if region is not None:
                self._data[CONF_REGION_ID] = int(region["id"])
                self._data[CONF_REGION_NAME] = item_name(region)
                self._dsos = region.get("dsos") or []
                if len(self._dsos) == 1:
                    return self._set_dso(self._dsos[0])
                return await self.async_step_dso()
            errors["base"] = "unknown"
        schema = vol.Schema(
            {
                vol.Required("region"): _select(
                    [(str(r["id"]), item_name(r)) for r in self._regions]
                )
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_dso(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            dso = next(
                (d for d in self._dsos if str(d.get("id")) == user_input["dso"]), None
            )
            if dso is not None:
                return self._set_dso(dso)
        schema = vol.Schema(
            {
                vol.Required("dso"): _select(
                    [(str(d["id"]), item_name(d)) for d in self._dsos]
                )
            }
        )
        return self.async_show_form(step_id="dso", data_schema=schema)

    def _set_dso(self, dso: dict[str, Any]) -> ConfigFlowResult:
        self._data[CONF_DSO_ID] = int(dso["id"])
        self._data[CONF_DSO_NAME] = item_name(dso)
        return self.async_show_form(
            step_id="street",
            data_schema=vol.Schema({vol.Required("query"): TextSelector()}),
        )

    # --------------------------------------------------------------- street
    async def async_step_street(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._streets = await self._client.search_streets(
                    self._data[CONF_REGION_ID],
                    self._data[CONF_DSO_ID],
                    user_input["query"],
                )
            except YasnoError:
                errors["base"] = "cannot_connect"
            else:
                if self._streets:
                    return await self.async_step_street_pick()
                errors["base"] = "street_not_found"
        return self.async_show_form(
            step_id="street",
            data_schema=vol.Schema({vol.Required("query"): TextSelector()}),
            errors=errors,
        )

    async def async_step_street_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            street = next(
                (s for s in self._streets if str(s.get("id")) == user_input["street"]),
                None,
            )
            if street is not None:
                self._data[CONF_STREET_ID] = int(street["id"])
                self._data[CONF_STREET_NAME] = item_name(street)
                return self.async_show_form(
                    step_id="house",
                    data_schema=vol.Schema({vol.Required("query"): TextSelector()}),
                )
        schema = vol.Schema(
            {
                vol.Required("street"): _select(
                    [(str(s["id"]), item_name(s)) for s in self._streets[:100]]
                )
            }
        )
        return self.async_show_form(step_id="street_pick", data_schema=schema)

    # --------------------------------------------------------------- house
    async def async_step_house(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._houses = await self._client.search_houses(
                    self._data[CONF_REGION_ID],
                    self._data[CONF_DSO_ID],
                    self._data[CONF_STREET_ID],
                    user_input["query"],
                )
            except YasnoError:
                errors["base"] = "cannot_connect"
            else:
                if self._houses:
                    return await self.async_step_house_pick()
                errors["base"] = "house_not_found"
        return self.async_show_form(
            step_id="house",
            data_schema=vol.Schema({vol.Required("query"): TextSelector()}),
            errors=errors,
        )

    async def async_step_house_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            house = next(
                (h for h in self._houses if str(h.get("id")) == user_input["house"]),
                None,
            )
            if house is not None:
                try:
                    group, subgroup = await self._client.get_group(
                        self._data[CONF_REGION_ID],
                        self._data[CONF_DSO_ID],
                        self._data[CONF_STREET_ID],
                        int(house["id"]),
                    )
                except AddressNotFoundError:
                    errors["base"] = "group_not_found"
                except YasnoError:
                    errors["base"] = "cannot_connect"
                else:
                    self._data[CONF_HOUSE_ID] = int(house["id"])
                    self._data[CONF_HOUSE_NAME] = item_name(house)
                    self._data[CONF_GROUP] = group
                    self._data[CONF_SUBGROUP] = subgroup
                    return await self._address_done()
        schema = vol.Schema(
            {
                vol.Required("house"): _select(
                    [(str(h["id"]), item_name(h)) for h in self._houses[:100]]
                )
            }
        )
        return self.async_show_form(
            step_id="house_pick", data_schema=schema, errors=errors
        )

    async def _address_done(self) -> ConfigFlowResult:
        if self._reconfigure:
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(), data_updates=self._data
            )
        return await self.async_step_power()

    # --------------------------------------------------------------- power template
    async def async_step_power(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if error := validate_template(self.hass, user_input[CONF_POWER_TEMPLATE]):
                errors["base"] = error
            else:
                return self.async_create_entry(
                    title="Svitlo",
                    data=self._data,
                    options={
                        CONF_POWER_TEMPLATE: user_input[CONF_POWER_TEMPLATE],
                        CONF_SCHEDULE_INTERVAL: DEFAULT_SCHEDULE_INTERVAL,
                        CONF_ADDRESS_INTERVAL: DEFAULT_ADDRESS_INTERVAL,
                        CONF_ADDRESS_FAIL_LIMIT: DEFAULT_ADDRESS_FAIL_LIMIT,
                        CONF_DELAY_OFF: DEFAULT_DELAY_OFF,
                        CONF_DELAY_ON: DEFAULT_DELAY_ON,
                        CONF_MATCH_MIN_OVERLAP: DEFAULT_MATCH_MIN_OVERLAP,
                        CONF_RETENTION_DAYS: DEFAULT_RETENTION_DAYS,
                    },
                )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_POWER_TEMPLATE,
                    default=(user_input or {}).get(
                        CONF_POWER_TEMPLATE, DEFAULT_POWER_TEMPLATE
                    ),
                ): TemplateSelector()
            }
        )
        return self.async_show_form(step_id="power", data_schema=schema, errors=errors)

    # --------------------------------------------------------------- reconfigure
    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the address (region -> ... -> house)."""
        self._reconfigure = True
        return await self.async_step_user()


class SvitloOptionsFlow(OptionsFlowWithReload):
    """Timers, matching tolerance, retention and the power template."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> Any:
        errors: dict[str, str] = {}
        current = {**self.config_entry.options, **(user_input or {})}
        if user_input is not None:
            if error := validate_template(self.hass, user_input[CONF_POWER_TEMPLATE]):
                errors["base"] = error
            else:
                return self.async_create_entry(
                    data={
                        CONF_POWER_TEMPLATE: user_input[CONF_POWER_TEMPLATE],
                        **{
                            key: int(user_input[key])
                            for key in (
                                CONF_DELAY_OFF,
                                CONF_DELAY_ON,
                                CONF_SCHEDULE_INTERVAL,
                                CONF_ADDRESS_INTERVAL,
                                CONF_ADDRESS_FAIL_LIMIT,
                                CONF_MATCH_MIN_OVERLAP,
                                CONF_RETENTION_DAYS,
                            )
                        },
                    }
                )

        def default(key: str, fallback: Any) -> Any:
            return current.get(key, fallback)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_POWER_TEMPLATE,
                    default=default(CONF_POWER_TEMPLATE, DEFAULT_POWER_TEMPLATE),
                ): TemplateSelector(),
                vol.Required(
                    CONF_DELAY_OFF, default=default(CONF_DELAY_OFF, DEFAULT_DELAY_OFF)
                ): _number(0, 3600),
                vol.Required(
                    CONF_DELAY_ON, default=default(CONF_DELAY_ON, DEFAULT_DELAY_ON)
                ): _number(0, 3600),
                vol.Required(
                    CONF_SCHEDULE_INTERVAL,
                    default=default(CONF_SCHEDULE_INTERVAL, DEFAULT_SCHEDULE_INTERVAL),
                ): _number(1, 1440),
                vol.Required(
                    CONF_ADDRESS_INTERVAL,
                    default=default(CONF_ADDRESS_INTERVAL, DEFAULT_ADDRESS_INTERVAL),
                ): _number(1, 1440),
                vol.Required(
                    CONF_ADDRESS_FAIL_LIMIT,
                    default=default(
                        CONF_ADDRESS_FAIL_LIMIT, DEFAULT_ADDRESS_FAIL_LIMIT
                    ),
                ): _number(1, 100),
                vol.Required(
                    CONF_MATCH_MIN_OVERLAP,
                    default=default(CONF_MATCH_MIN_OVERLAP, DEFAULT_MATCH_MIN_OVERLAP),
                ): _number(0, 240),
                vol.Required(
                    CONF_RETENTION_DAYS,
                    default=default(CONF_RETENTION_DAYS, DEFAULT_RETENTION_DAYS),
                ): _number(7, 3650),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
