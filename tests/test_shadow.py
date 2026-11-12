"""Layer 2: filling empty status of the official integration from the cloud."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    mock_integration,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.tuya_keep import shadow
from custom_components.tuya_keep.config_flow import DEFAULT_OPTIONS
from custom_components.tuya_keep.const import DOMAIN
from custom_components.tuya_keep.shadow import build_report

BASE = "https://openapi.tuyaeu.com"
TOKEN_URL = f"{BASE}/v1.0/token?grant_type=1"
TOKEN_OK = {"success": True, "result": {"access_token": "tok", "expire_time": 7200}}
CLOUD = {"endpoint": "eu", "access_id": "test-id", "access_secret": "test-secret"}
RANGES = {
    "va_temperature": "Integer",
    "va_humidity": "Integer",
    "battery_percentage": "Integer",
    "smoke_sensor_status": "Enum",
    "doorcontact_state": "Boolean",
}


def _device(
    dev_id: str,
    status: dict[str, Any] | None = None,
    local: bool = False,
    strategy: dict[int, dict[str, Any]] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=dev_id,
        status=dict(status or {}),
        status_range={
            code: SimpleNamespace(code=code, type=kind) for code, kind in RANGES.items()
        },
        support_local=local,
        local_strategy=strategy or {},
    )


class FakeManager:
    """Stands in for tuya_sharing.Manager."""

    def __init__(self, devices: list[SimpleNamespace]) -> None:
        self.device_map = {d.id: d for d in devices}
        self.reports: list[tuple[str, list[dict[str, Any]]]] = []

    def _on_device_report(self, device_id: str, status: list[dict[str, Any]]) -> None:
        self.reports.append((device_id, status))
        device = self.device_map[device_id]
        for item in status:
            if "code" in item:
                device.status[item["code"]] = item["value"]


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shadow, "SHADOW_DEBOUNCE", 0)
    monkeypatch.setattr(shadow, "SHADOW_RETRY_DELAY", 0)


def _tuya(hass: HomeAssistant, manager: FakeManager) -> MockConfigEntry:
    # A stand-in for the official integration (its SDK is not needed here).
    mock_integration(hass, MockModule("tuya"))
    entry = MockConfigEntry(domain="tuya", data={})
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    entry.runtime_data = SimpleNamespace(manager=manager)
    return entry


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, options={**DEFAULT_OPTIONS, "cloud": CLOUD})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


def test_build_report_by_code_only_missing() -> None:
    """Codes the device has a value for are never overwritten."""
    device = _device("d1", status={"va_humidity": 50})
    props = [
        {"code": "va_temperature", "dp_id": 1, "value": 215},
        {"code": "va_humidity", "dp_id": 2, "value": 40},
        {"code": "not_in_range", "dp_id": 9, "value": 1},
        {"code": "battery_percentage", "dp_id": 4, "value": None},
    ]
    assert build_report(device, props) == [{"code": "va_temperature", "value": 215}]


def test_build_report_local_strategy_by_dp_id() -> None:
    """Devices with a local strategy get raw data points by id."""
    device = _device(
        "d1",
        status={"va_humidity": 50},
        local=True,
        strategy={
            1: {"status_code": "va_temperature"},
            2: {"status_code": "va_humidity"},
        },
    )
    props = [
        {"code": "temp_current", "dp_id": 1, "value": 215, "time": 1700000000000},
        {"code": "humidity_value", "dp_id": 2, "value": 40},
        {"code": "other", "dp_id": 3, "value": 1},
    ]
    assert build_report(device, props) == [
        {"dpId": 1, "value": 215, "t": 1700000000000}
    ]


async def test_sync_fills_only_devices_with_empty_status(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Only devices that miss values are read, then handed to the SDK."""
    empty = _device("dev_empty")
    full = _device(
        "dev_full",
        status={"va_temperature": 200, "va_humidity": 50, "battery_percentage": 90},
    )
    manager = FakeManager([empty, full])
    _tuya(hass, manager)
    aioclient_mock.get(TOKEN_URL, json=TOKEN_OK)
    aioclient_mock.get(
        f"{BASE}/v2.0/cloud/thing/dev_empty/shadow/properties",
        json={
            "success": True,
            "result": {
                "properties": [
                    {"code": "va_temperature", "dp_id": 1, "value": 215},
                    {"code": "va_humidity", "dp_id": 2, "value": 48},
                ]
            },
        },
    )

    entry = await _setup(hass)

    assert manager.reports == [
        (
            "dev_empty",
            [
                {"code": "va_temperature", "value": 215},
                {"code": "va_humidity", "value": 48},
            ],
        )
    ]
    urls = [str(call[1]) for call in aioclient_mock.mock_calls]
    assert not any("dev_full" in url for url in urls)
    # Signed requests.
    headers = aioclient_mock.mock_calls[-1][3]
    assert headers["client_id"] == "test-id"
    assert headers["access_token"] == "tok"
    assert len(headers["sign"]) == 64 and headers["sign"].isupper()
    assert entry.runtime_data.sync.stats["values_filled"] == 2
    assert hass.states.get("sensor.tuya_keep_cloud_filled").state == "2"
    assert hass.states.get("sensor.tuya_keep_cloud_status").state == "ok"


async def test_permanent_error_is_not_retried(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """'permission deny' (app account not linked): asked once only."""
    manager = FakeManager([_device("dev1")])
    _tuya(hass, manager)
    aioclient_mock.get(TOKEN_URL, json=TOKEN_OK)
    aioclient_mock.get(
        f"{BASE}/v2.0/cloud/thing/dev1/shadow/properties",
        json={"success": False, "code": 1106, "msg": "permission deny"},
    )
    entry = await _setup(hass)
    calls = aioclient_mock.call_count
    entry.runtime_data.sync.trigger()
    await hass.async_block_till_done(wait_background_tasks=True)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert manager.reports == []
    assert aioclient_mock.call_count == calls
    stats = entry.runtime_data.sync.stats
    assert stats["errors"] == 1
    assert "permission deny" in stats["last_error"]
    assert hass.states.get("sensor.tuya_keep_cloud_status").state == "error"


async def test_token_is_renewed_when_tuya_says_so(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 'token invalid' answer fetches a new token and asks again."""
    manager = FakeManager([_device("dev1")])
    _tuya(hass, manager)
    aioclient_mock.get(TOKEN_URL, json=TOKEN_OK)
    answers = [
        {"success": False, "code": 1010, "msg": "token invalid"},
        {
            "success": True,
            "result": {"properties": [{"code": "va_temperature", "value": 199}]},
        },
    ]

    async def _shadow(method, url, data):
        return AiohttpClientMockResponse(method, url, json=answers.pop(0))

    aioclient_mock.get(
        f"{BASE}/v2.0/cloud/thing/dev1/shadow/properties", side_effect=_shadow
    )
    await _setup(hass)
    assert manager.reports == [("dev1", [{"code": "va_temperature", "value": 199}])]
    token_calls = [c for c in aioclient_mock.mock_calls if "token" in str(c[1])]
    assert len(token_calls) == 2


async def test_no_cloud_layer_without_keys(hass: HomeAssistant) -> None:
    """Without keys nothing talks to the cloud."""
    entry = MockConfigEntry(domain=DOMAIN, options=DEFAULT_OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.runtime_data.sync is None


async def test_wrong_keys_stop_after_the_token(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Refused keys: one token call, no device calls, nothing retried."""
    manager = FakeManager([_device("dev1"), _device("dev2")])
    _tuya(hass, manager)
    aioclient_mock.get(
        TOKEN_URL, json={"success": False, "code": 1004, "msg": "sign invalid"}
    )
    entry = await _setup(hass)
    assert aioclient_mock.call_count == 1
    entry.runtime_data.sync.trigger()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert aioclient_mock.call_count == 1
    assert manager.reports == []
    assert "sign invalid" in entry.runtime_data.sync.stats["last_error"]


def test_alarm_and_on_off_states_are_never_filled() -> None:
    """An old 'alarm' in the cloud must not become a false alarm."""
    device = _device("smoke1")
    props = [
        {"code": "smoke_sensor_status", "dp_id": 1, "value": "alarm"},
        {"code": "doorcontact_state", "dp_id": 2, "value": True},
        {"code": "battery_percentage", "dp_id": 15, "value": 80},
    ]
    assert build_report(device, props) == [{"code": "battery_percentage", "value": 80}]
