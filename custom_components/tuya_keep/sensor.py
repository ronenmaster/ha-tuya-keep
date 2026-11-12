"""Sensors that show what Tuya Keep handles, live."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from . import TuyaKeepConfigEntry, TuyaKeepData
from .const import DOMAIN


def _names(hass: HomeAssistant, entity_ids: list[str]) -> list[str]:
    """Friendly names, as Home Assistant shows them."""
    names = []
    for entity_id in entity_ids:
        state = hass.states.get(entity_id)
        name = state.attributes.get("friendly_name") if state else None
        names.append(str(name or entity_id))
    return sorted(names)


def _kept_attrs(hass: HomeAssistant, data: TuyaKeepData) -> dict[str, Any]:
    kept = data.keeper.kept
    ids = sorted(kept)
    return {
        "sensors": _names(hass, ids),
        "entity_ids": ids,
        "last_seen": {
            eid: dt_util.utc_from_timestamp(kept[eid]).isoformat() for eid in ids
        },
    }


def _times_attrs(
    hass: HomeAssistant, times: dict[str, float], extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    ids = sorted(times)
    attrs: dict[str, Any] = {
        "sensors": _names(hass, ids),
        "entity_ids": ids,
        "last_seen": {
            eid: dt_util.utc_from_timestamp(times[eid]).isoformat() for eid in ids
        },
    }
    if extra:
        attrs.update(extra)
    return attrs


def _silent_attrs(hass: HomeAssistant, data: TuyaKeepData) -> dict[str, Any]:
    return _times_attrs(
        hass, data.keeper.silent(), {"silent_after_hours": data.keeper.silent_hours}
    )


def _came_up_attrs(hass: HomeAssistant, data: TuyaKeepData) -> dict[str, Any]:
    came = data.keeper.came_up_unknown
    ids = sorted(came)
    at = data.keeper.came_up_at
    return {
        "sensors": _names(hass, ids),
        "entity_ids": ids,
        "start": dt_util.utc_from_timestamp(at).isoformat() if at else None,
    }


def _watched_attrs(hass: HomeAssistant, data: TuyaKeepData) -> dict[str, Any]:
    ids = data.keeper.watched_entities()
    return {"sensors": _names(hass, ids), "entity_ids": ids}


def _last_restore(_hass: HomeAssistant, data: TuyaKeepData) -> datetime | None:
    value = data.keeper.stats.get("last_restore")
    return dt_util.parse_datetime(value) if value else None


def _cloud_status(_hass: HomeAssistant, data: TuyaKeepData) -> str:
    stats = data.sync.stats if data.sync else {}
    if stats.get("last_error"):
        return "error"
    return "ok" if stats.get("last_run") else "waiting"


@dataclass(frozen=True, kw_only=True)
class KeepSensorDescription(SensorEntityDescription):
    """A Tuya Keep sensor: where its value and attributes come from."""

    value_fn: Callable[[HomeAssistant, TuyaKeepData], StateType | datetime]
    attrs_fn: Callable[[HomeAssistant, TuyaKeepData], dict[str, Any]] | None = None
    cloud: bool = False


SENSORS: tuple[KeepSensorDescription, ...] = (
    KeepSensorDescription(
        key="kept_now",
        translation_key="kept_now",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda _h, d: len(d.keeper.kept),
        attrs_fn=_kept_attrs,
    ),
    KeepSensorDescription(
        key="watched",
        translation_key="watched",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda _h, d: d.keeper.watched_count,
        attrs_fn=_watched_attrs,
    ),
    KeepSensorDescription(
        key="restored",
        translation_key="restored",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda _h, d: d.keeper.stats["restored"],
    ),
    KeepSensorDescription(
        key="last_restore",
        translation_key="last_restore",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_last_restore,
    ),
    KeepSensorDescription(
        key="silent",
        translation_key="silent",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda _h, d: len(d.keeper.silent()),
        attrs_fn=_silent_attrs,
    ),
    KeepSensorDescription(
        key="came_up_unknown",
        translation_key="came_up_unknown",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda _h, d: len(d.keeper.came_up_unknown),
        attrs_fn=_came_up_attrs,
    ),
    KeepSensorDescription(
        key="cloud_filled",
        translation_key="cloud_filled",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda _h, d: d.sync.stats["values_filled"] if d.sync else 0,
        cloud=True,
    ),
    KeepSensorDescription(
        key="cloud_status",
        translation_key="cloud_status",
        device_class=SensorDeviceClass.ENUM,
        options=["ok", "error", "waiting"],
        value_fn=_cloud_status,
        attrs_fn=lambda _h, d: {
            "last_error": d.sync.stats.get("last_error") if d.sync else None
        },
        cloud=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TuyaKeepConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """One service device with the sensors (the cloud ones when it is used)."""
    integration = await async_get_integration(hass, DOMAIN)
    device = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Tuya Keep",
        manufacturer="Tuya Keep",
        model="Last value keeper",
        sw_version=str(integration.version),
        entry_type=DeviceEntryType.SERVICE,
    )
    data = entry.runtime_data
    async_add_entities(
        KeepSensor(data, description, device)
        for description in SENSORS
        if not description.cloud or data.sync is not None
    )


class KeepSensor(SensorEntity):
    """Updated by the keeper whenever what it handles changes."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    # Long lists: shown on the entity, kept out of the recorder.
    _unrecorded_attributes = frozenset(
        {"sensors", "entity_ids", "last_seen", "start", "silent_after_hours"}
    )
    entity_description: KeepSensorDescription

    def __init__(
        self,
        data: TuyaKeepData,
        description: KeepSensorDescription,
        device: DeviceInfo,
    ) -> None:
        """Fixed English entity ids, translated names."""
        self._data = data
        self.entity_description = description
        self._attr_unique_id = f"{DOMAIN}_{description.key}"
        self._attr_device_info = device
        self.entity_id = f"sensor.{DOMAIN}_{description.key}"

    async def async_added_to_hass(self) -> None:
        """Follow the keeper (and the cloud layer)."""
        self.async_on_remove(self._data.keeper.add_listener(self._changed))

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> StateType | datetime:
        """The current value."""
        return self.entity_description.value_fn(self.hass, self._data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """The lists behind the number."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.hass, self._data)
