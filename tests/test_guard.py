"""Unknown values never become a state (the smoke detectors' false alarms)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import STATE_OFF, STATE_ON, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.tuya_keep.config_flow import DEFAULT_OPTIONS
from custom_components.tuya_keep.const import DOMAIN
from custom_components.tuya_keep.guard import unknown_items

# The data point as the real detector defines it (product of the report).
SMOKE_RULE = {
    "value_convert": "enum",
    "status_code": "smoke_sensor_status",
    "config_item": {
        "statusFormat": '{"smoke_sensor_status":"$"}',
        "valueDesc": '{"range":["alarm","normal"]}',
        "valueType": "Enum",
        "enumMappingMap": {
            "1": {"code": "smoke_sensor_status", "value": "alarm"},
            "2": {"code": "smoke_sensor_status", "value": "normal"},
            "alarm": {"code": "smoke_sensor_status", "value": "alarm"},
            "normal": {"code": "smoke_sensor_status", "value": "normal"},
        },
        "pid": "test",
    },
}
TAMPER_RULE = {
    "value_convert": "default",
    "status_code": "temper_alarm",
    "config_item": {
        "statusFormat": '{"temper_alarm":"$"}',
        "valueDesc": "{}",
        "valueType": "Boolean",
        "enumMappingMap": {},
        "pid": "test",
    },
}


def test_unknown_items() -> None:
    """Only an enum value outside the device's own definition is caught."""
    device = SimpleNamespace(local_strategy={1: SMOKE_RULE, 4: TAMPER_RULE})
    report = [
        {"dpId": 1, "t": 1, "value": "23"},
        {"dpId": 101, "t": 1, "value": "0"},
        {"dpId": 4, "t": 1, "value": False},
    ]
    assert unknown_items(device, report) == [{"dpId": 1, "t": 1, "value": "23"}]
    for good in ("1", "2", "alarm", "normal", "ALARM"):
        assert unknown_items(device, [{"dpId": 1, "value": good}]) == []


tuya_sharing = pytest.importorskip("tuya_sharing")
from tuya_sharing import CustomerDevice, Manager  # noqa: E402
from tuya_sharing.device import DeviceStatusRange  # noqa: E402

DEVICE_ID = "smoke_detector_1"


def _detector() -> CustomerDevice:
    return CustomerDevice(
        id=DEVICE_ID,
        name="Detector",
        local_key="",
        category="ywbj",
        product_id="test",
        product_name="Smoke alarm",
        sub=False,
        uuid="uuid_smoke",
        asset_id="",
        online=True,
        icon="",
        ip="",
        time_zone="+00:00",
        active_time=0,
        create_time=0,
        update_time=0,
        set_up=False,
        support_local=True,
        local_strategy={1: SMOKE_RULE, 4: TAMPER_RULE},
        # (quiet, as after a real "normal")
        status={"smoke_sensor_status": "normal", "temper_alarm": False},
        function={},
        status_range={
            "smoke_sensor_status": DeviceStatusRange(
                code="smoke_sensor_status",
                type="Enum",
                values=json.dumps({"range": ["alarm", "normal"]}),
            ),
            "temper_alarm": DeviceStatusRange(
                code="temper_alarm", type="Boolean", values="{}"
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
        device = _detector()
        self.device_map[device.id] = device

    def refresh_mq(self) -> None:
        return

    def unload(self) -> None:
        return


@pytest.fixture
def offline_tuya():
    """The official integration with the offline manager, binary sensors on."""
    from homeassistant.components import tuya  # noqa: PLC0415

    if hasattr(tuya, "_create_manager"):
        target = "homeassistant.components.tuya._create_manager"
        new = lambda *a, **k: OfflineManager()  # noqa: E731
    else:
        target = "homeassistant.components.tuya.coordinator.Manager"
        new = OfflineManager
    with (
        patch(target, new),
        patch(
            "homeassistant.components.tuya.PLATFORMS",
            [Platform.SENSOR, Platform.BINARY_SENSOR],
        ),
    ):
        yield


async def _setup(hass: HomeAssistant, guard: bool) -> tuple[MockConfigEntry, Any, str]:
    tuya_entry = MockConfigEntry(
        domain="tuya",
        data={
            "endpoint": "https://example.invalid",
            "terminal_id": "terminal",
            "token_info": {},
            "user_code": "user",
        },
    )
    tuya_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(tuya_entry.entry_id)
    await hass.async_block_till_done()
    keep = MockConfigEntry(
        domain=DOMAIN,
        options={**DEFAULT_OPTIONS, "ignore_unknown_values": guard},
    )
    keep.add_to_hass(hass)
    assert await hass.config_entries.async_setup(keep.entry_id)
    await hass.async_block_till_done()
    smoke = next(
        e.entity_id
        for e in er.async_get(hass).entities.values()
        if e.platform == "tuya"
        and e.domain == "binary_sensor"
        and hass.states.get(e.entity_id).attributes.get("device_class") == "smoke"
    )
    return keep, tuya_entry.runtime_data.manager, smoke


async def _report(hass: HomeAssistant, manager: Any, value: Any) -> None:
    await hass.async_add_executor_job(
        manager._on_device_report, DEVICE_ID, [{"dpId": 1, "t": 1, "value": value}]
    )
    for _ in range(3):
        await hass.async_block_till_done()


async def test_without_guard_the_bug_shows(
    hass: HomeAssistant, offline_tuya: None
) -> None:
    """The bug itself: "23" becomes smoke."""
    _keep, manager, smoke = await _setup(hass, guard=False)
    assert hass.states.get(smoke).state == STATE_OFF
    await _report(hass, manager, "23")
    assert hass.states.get(smoke).state == STATE_ON


async def test_guard_ignores_unknown_and_passes_real(
    hass: HomeAssistant, offline_tuya: None
) -> None:
    """ "23" is ignored; a real alarm and a real normal pass."""
    keep, manager, smoke = await _setup(hass, guard=True)
    await _report(hass, manager, "23")
    assert hass.states.get(smoke).state == STATE_OFF
    ignored = hass.states.get("sensor.tuya_keep_ignored")
    assert ignored.state == "1"
    assert ignored.attributes["recent"][0]["code"] == "smoke_sensor_status"
    assert ignored.attributes["recent"][0]["value"] == "23"

    await _report(hass, manager, "1")  # a real alarm
    assert hass.states.get(smoke).state == STATE_ON
    await _report(hass, manager, "2")  # and back to normal
    assert hass.states.get(smoke).state == STATE_OFF

    # Unloaded: the SDK's handler is its own again.
    assert "_on_device_report" in vars(manager)
    assert await hass.config_entries.async_unload(keep.entry_id)
    assert "_on_device_report" not in vars(manager)
