"""End to end with the real official Tuya integration and its SDK.

The Tuya cloud is replaced by a manager that returns one temperature and
humidity sensor with an empty status, as the real cloud does for some
battery devices right after a restart. Skipped when the SDK is missing.
"""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from homeassistant.const import STATE_UNKNOWN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.tuya_keep import shadow
from custom_components.tuya_keep.config_flow import DEFAULT_OPTIONS
from custom_components.tuya_keep.const import (
    ATTR_RESTORED,
    DOMAIN,
    STORE_KEY,
    STORE_VERSION,
)

tuya_sharing = pytest.importorskip("tuya_sharing")
from tuya_sharing import CustomerDevice, Manager  # noqa: E402
from tuya_sharing.device import DeviceStatusRange  # noqa: E402

DEVICE_ID = "test_device_1"


def _device() -> CustomerDevice:
    return CustomerDevice(
        id=DEVICE_ID,
        name="Sensor",
        local_key="",
        category="wsdcg",
        product_id="test_product",
        product_name="Temperature and humidity sensor",
        sub=False,
        uuid="test_uuid",
        asset_id="",
        online=True,
        icon="",
        ip="",
        time_zone="+00:00",
        active_time=0,
        create_time=0,
        update_time=0,
        set_up=False,
        support_local=False,
        local_strategy={},
        status={},  # what the cloud returns after a restart
        function={},
        status_range={
            "va_temperature": DeviceStatusRange(
                code="va_temperature",
                type="Integer",
                values=json.dumps(
                    {"unit": "℃", "min": -200, "max": 600, "scale": 1, "step": 1}
                ),
            ),
            "va_humidity": DeviceStatusRange(
                code="va_humidity",
                type="Integer",
                values=json.dumps(
                    {"unit": "%", "min": 0, "max": 100, "scale": 0, "step": 1}
                ),
            ),
        },
    )


class OfflineManager(Manager):
    """The real SDK manager, without the network."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(
            "client",
            "user",
            "terminal",
            "https://example.invalid",
            {
                "t": 0,
                "expire_time": 0,
                "uid": "",
                "access_token": "",
                "refresh_token": "",
            },
            None,
        )

    def update_device_cache(self) -> None:
        self.device_map.clear()
        device = _device()
        self.device_map[device.id] = device

    def refresh_mq(self) -> None:
        return

    def unload(self) -> None:
        return


@pytest.fixture
def offline_tuya():
    """Make the official integration use the offline manager."""
    from homeassistant.components import tuya  # noqa: PLC0415

    if hasattr(tuya, "_create_manager"):
        target = "homeassistant.components.tuya._create_manager"
        new = lambda *a, **k: OfflineManager()  # noqa: E731
    else:  # newer Home Assistant: the manager is made in coordinator.py
        target = "homeassistant.components.tuya.coordinator.Manager"
        new = OfflineManager
    # Only the sensor platform (the others need extra system libraries).
    with (
        patch(target, new),
        patch("homeassistant.components.tuya.PLATFORMS", [Platform.SENSOR]),
    ):
        yield


async def _setup_tuya(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain="tuya",
        data={
            "endpoint": "https://example.invalid",
            "terminal_id": "terminal",
            "token_info": {},
            "user_code": "user",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _entities(hass: HomeAssistant) -> dict[str, str]:
    """device class -> entity_id of the Tuya sensors."""
    out = {}
    for entry in er.async_get(hass).entities.values():
        if entry.platform == "tuya" and entry.domain == "sensor":
            state = hass.states.get(entry.entity_id)
            out[state.attributes.get("device_class")] = entry.entity_id
    return out


async def test_official_sensor_keeps_its_value(
    hass: HomeAssistant, hass_storage: dict[str, Any], offline_tuya: None
) -> None:
    """Unknown after start and after a reload: the kept value comes back."""
    tuya_entry = await _setup_tuya(hass)
    ids = _entities(hass)
    temp = ids["temperature"]
    assert hass.states.get(temp).state == STATE_UNKNOWN  # the bug itself

    hass_storage[STORE_KEY] = {
        "version": STORE_VERSION,
        "minor_version": 1,
        "key": STORE_KEY,
        "data": {"values": {temp: {"s": "21.5", "t": time.time() - 900}}},
    }
    keep = MockConfigEntry(domain=DOMAIN, options=DEFAULT_OPTIONS)
    keep.add_to_hass(hass)
    assert await hass.config_entries.async_setup(keep.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(temp).state == "21.5"

    # Reloading the official integration: unknown again, and restored again.
    assert await hass.config_entries.async_reload(tuya_entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get(temp)
    assert state.state == "21.5"
    assert state.attributes[ATTR_RESTORED] is True

    # The device's next real report replaces it, through the SDK's own path.
    manager = tuya_entry.runtime_data.manager
    await hass.async_add_executor_job(
        manager._on_device_report, DEVICE_ID, [{"code": "va_temperature", "value": 233}]
    )
    await hass.async_block_till_done()
    state = hass.states.get(temp)
    assert float(state.state) == pytest.approx(23.3)
    assert ATTR_RESTORED not in state.attributes


async def test_cloud_layer_gives_the_real_values(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    offline_tuya: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With cloud keys the official entities show the shadow's values."""
    monkeypatch.setattr(shadow, "SHADOW_DEBOUNCE", 0)
    base = "https://openapi.tuyaeu.com"
    aioclient_mock.get(
        f"{base}/v1.0/token?grant_type=1",
        json={"success": True, "result": {"access_token": "tok", "expire_time": 7200}},
    )
    aioclient_mock.get(
        f"{base}/v2.0/cloud/thing/{DEVICE_ID}/shadow/properties",
        json={
            "success": True,
            "result": {
                "properties": [
                    {"code": "va_temperature", "dp_id": 1, "value": 198},
                    {"code": "va_humidity", "dp_id": 2, "value": 61},
                ]
            },
        },
    )
    await _setup_tuya(hass)
    ids = _entities(hass)

    keep = MockConfigEntry(
        domain=DOMAIN,
        options={
            **DEFAULT_OPTIONS,
            "cloud": {"endpoint": "eu", "access_id": "id", "access_secret": "secret"},
        },
    )
    keep.add_to_hass(hass)
    assert await hass.config_entries.async_setup(keep.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    await hass.async_block_till_done(wait_background_tasks=True)

    temp = hass.states.get(ids["temperature"])
    humidity = hass.states.get(ids["humidity"])
    assert float(temp.state) == pytest.approx(19.8)
    assert float(humidity.state) == pytest.approx(61)
    assert ATTR_RESTORED not in temp.attributes  # real values, not restored
