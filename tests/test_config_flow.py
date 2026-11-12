"""Config flow, options flow and diagnostics."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.tuya_keep.config_flow import DEFAULT_OPTIONS
from custom_components.tuya_keep.const import DOMAIN
from custom_components.tuya_keep.diagnostics import (
    async_get_config_entry_diagnostics,
)

TOKEN_URL = "https://openapi.tuyaeu.com/v1.0/token?grant_type=1"


def _form(cloud: dict[str, str]) -> dict:
    return {
        "mode": "companion",
        "max_age_hours": 12,
        "include_non_numeric": False,
        "platforms": ["tuya"],
        "exclude_entities": [],
        "silent_hours": 6,
        "ignore_unknown_values": True,
        "cloud": cloud,
    }


async def test_user_flow_creates_one_entry(hass: HomeAssistant) -> None:
    """One click creates the entry; a second one is refused."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == DEFAULT_OPTIONS
    await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_options_flow(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Keys are checked against the Tuya cloud before they are saved."""
    entry = MockConfigEntry(domain=DOMAIN, options=DEFAULT_OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        _form({"endpoint": "eu", "access_id": "", "access_secret": ""}),
    )
    assert result["errors"] == {"base": "cloud_missing"}

    aioclient_mock.get(
        TOKEN_URL, json={"success": False, "code": 1004, "msg": "sign invalid"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        _form({"endpoint": "eu", "access_id": "id", "access_secret": "bad"}),
    )
    assert result["errors"] == {"base": "invalid_auth"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(
        TOKEN_URL,
        json={"success": True, "result": {"access_token": "t", "expire_time": 7200}},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        _form({"endpoint": "eu", "access_id": "id", "access_secret": "good"}),
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["max_age_hours"] == 12
    assert entry.options["cloud"]["access_secret"] == "good"
    assert entry.runtime_data.sync is not None  # reloaded with the cloud layer


async def test_options_flow_without_cloud(hass: HomeAssistant) -> None:
    """'Not used' needs no keys and no network."""
    entry = MockConfigEntry(domain=DOMAIN, options=DEFAULT_OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    form = _form({"endpoint": "none"})
    form["platforms"] = []
    result = await hass.config_entries.options.async_configure(result["flow_id"], form)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["platforms"] == ["tuya"]  # empty falls back to tuya


async def test_diagnostics_hide_keys(hass: HomeAssistant) -> None:
    """No keys and no entity names in diagnostics."""
    options = {
        **DEFAULT_OPTIONS,
        "exclude_entities": ["sensor.some_room_temperature"],
        "cloud": {"endpoint": "eu", "access_id": "id123", "access_secret": "sec456"},
    }
    entry = MockConfigEntry(domain=DOMAIN, options=options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    text = str(diag)
    assert "id123" not in text
    assert "sec456" not in text
    assert "some_room" not in text
    assert diag["options"]["cloud"]["access_id"] == "**set**"
