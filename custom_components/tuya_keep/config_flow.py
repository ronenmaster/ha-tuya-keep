"""Config and options flow for Tuya Keep."""

from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CLOUD_ENDPOINTS,
    CLOUD_NONE,
    CONF_CLOUD,
    CONF_CLOUD_ACCESS_ID,
    CONF_CLOUD_ACCESS_SECRET,
    CONF_CLOUD_ENDPOINT,
    CONF_EXCLUDE,
    CONF_GUARD,
    CONF_MAX_AGE_HOURS,
    CONF_MODE,
    CONF_NON_NUMERIC,
    CONF_PLATFORMS,
    CONF_SILENT_HOURS,
    DEFAULT_GUARD,
    DEFAULT_MAX_AGE_HOURS,
    DEFAULT_NON_NUMERIC,
    DEFAULT_PLATFORMS,
    DEFAULT_SILENT_HOURS,
    DOMAIN,
    LOGGER,
    MODE_COMPANION,
    MODE_IN_PLACE,
)
from .shadow import TuyaCloudError, TuyaOpenApi

DEFAULT_OPTIONS: dict[str, Any] = {
    CONF_MODE: MODE_COMPANION,
    CONF_MAX_AGE_HOURS: DEFAULT_MAX_AGE_HOURS,
    CONF_NON_NUMERIC: DEFAULT_NON_NUMERIC,
    CONF_PLATFORMS: list(DEFAULT_PLATFORMS),
    CONF_EXCLUDE: [],
    CONF_SILENT_HOURS: DEFAULT_SILENT_HOURS,
    CONF_GUARD: DEFAULT_GUARD,
    CONF_CLOUD: {
        CONF_CLOUD_ENDPOINT: CLOUD_NONE,
        CONF_CLOUD_ACCESS_ID: "",
        CONF_CLOUD_ACCESS_SECRET: "",
    },
}


class TuyaKeepConfigFlow(ConfigFlow, domain=DOMAIN):
    """One entry covers the whole installation."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm and create the entry with the default options."""
        if user_input is not None:
            return self.async_create_entry(
                title="Tuya Keep", data={}, options=DEFAULT_OPTIONS
            )
        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Options: how long to keep, what to watch, the cloud layer."""
        return TuyaKeepOptionsFlow()


class TuyaKeepOptionsFlow(OptionsFlow):
    """Change what is kept and the optional cloud layer."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """One form; the cloud part is folded away."""
        errors: dict[str, str] = {}
        current = {**DEFAULT_OPTIONS, **self.config_entry.options}
        if CONF_MODE not in self.config_entry.options:
            current[CONF_MODE] = MODE_IN_PLACE  # made before the choice existed

        if user_input is not None:
            cloud_in = user_input.get(CONF_CLOUD) or {}
            new_options = {
                CONF_MODE: user_input.get(CONF_MODE) or current[CONF_MODE],
                CONF_MAX_AGE_HOURS: int(user_input.get(CONF_MAX_AGE_HOURS) or 0),
                CONF_NON_NUMERIC: bool(user_input.get(CONF_NON_NUMERIC)),
                CONF_PLATFORMS: [
                    p.strip()
                    for p in user_input.get(CONF_PLATFORMS) or []
                    if p and p.strip()
                ]
                or list(DEFAULT_PLATFORMS),
                CONF_EXCLUDE: list(user_input.get(CONF_EXCLUDE) or []),
                CONF_SILENT_HOURS: int(user_input.get(CONF_SILENT_HOURS) or 0),
                CONF_GUARD: bool(user_input.get(CONF_GUARD, DEFAULT_GUARD)),
                CONF_CLOUD: {
                    CONF_CLOUD_ENDPOINT: cloud_in.get(CONF_CLOUD_ENDPOINT)
                    or CLOUD_NONE,
                    CONF_CLOUD_ACCESS_ID: (
                        cloud_in.get(CONF_CLOUD_ACCESS_ID) or ""
                    ).strip(),
                    CONF_CLOUD_ACCESS_SECRET: (
                        cloud_in.get(CONF_CLOUD_ACCESS_SECRET) or ""
                    ).strip(),
                },
            }
            errors = await self._async_check_cloud(new_options[CONF_CLOUD])
            if not errors:
                return self.async_create_entry(data=new_options)
            current = new_options

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(self._schema(), current),
            errors=errors,
        )

    async def _async_check_cloud(self, cloud: dict[str, str]) -> dict[str, str]:
        endpoint = CLOUD_ENDPOINTS.get(cloud[CONF_CLOUD_ENDPOINT])
        if endpoint is None:
            return {}
        if not cloud[CONF_CLOUD_ACCESS_ID] or not cloud[CONF_CLOUD_ACCESS_SECRET]:
            return {"base": "cloud_missing"}
        api = TuyaOpenApi(
            async_get_clientsession(self.hass),
            endpoint,
            cloud[CONF_CLOUD_ACCESS_ID],
            cloud[CONF_CLOUD_ACCESS_SECRET],
        )
        try:
            await api.check()
        except TuyaCloudError as err:
            LOGGER.debug("Tuya cloud refused the keys: %s", err)
            return {"base": "invalid_auth"}
        except (aiohttp.ClientError, TimeoutError):
            return {"base": "cannot_connect"}
        return {}

    def _schema(self) -> vol.Schema:
        registry = er.async_get(self.hass)
        platforms = sorted(
            {e.platform for e in registry.entities.values() if e.domain == "sensor"}
            | set(DEFAULT_PLATFORMS)
        )
        return vol.Schema(
            {
                vol.Required(CONF_MODE): SelectSelector(
                    SelectSelectorConfig(
                        options=[MODE_COMPANION, MODE_IN_PLACE],
                        translation_key=CONF_MODE,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
                vol.Required(CONF_MAX_AGE_HOURS): NumberSelector(
                    NumberSelectorConfig(
                        min=0,
                        max=720,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="h",
                    )
                ),
                vol.Required(CONF_NON_NUMERIC): BooleanSelector(),
                vol.Required(CONF_PLATFORMS): SelectSelector(
                    SelectSelectorConfig(
                        options=platforms,
                        multiple=True,
                        custom_value=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(CONF_EXCLUDE): EntitySelector(
                    EntitySelectorConfig(domain="sensor", multiple=True)
                ),
                vol.Required(CONF_GUARD): BooleanSelector(),
                vol.Required(CONF_SILENT_HOURS): NumberSelector(
                    NumberSelectorConfig(
                        min=0,
                        max=168,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="h",
                    )
                ),
                vol.Required(CONF_CLOUD): section(
                    vol.Schema(
                        {
                            vol.Required(CONF_CLOUD_ENDPOINT): SelectSelector(
                                SelectSelectorConfig(
                                    options=[CLOUD_NONE, *CLOUD_ENDPOINTS],
                                    translation_key=CONF_CLOUD_ENDPOINT,
                                    mode=SelectSelectorMode.DROPDOWN,
                                )
                            ),
                            vol.Optional(CONF_CLOUD_ACCESS_ID): TextSelector(),
                            vol.Optional(CONF_CLOUD_ACCESS_SECRET): TextSelector(
                                TextSelectorConfig(type=TextSelectorType.PASSWORD)
                            ),
                        }
                    ),
                    {"collapsed": True},
                ),
            }
        )
