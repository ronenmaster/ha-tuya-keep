"""Layer 3: unknown values never become a state (the false alarms fix).

Found on a Tuya smoke detector (2026-11): at its periodic check-in the
detector reports its smoke data point with a value its own definition does
not list (for example "23", where the definition knows only "1"/"alarm" and
"2"/"normal"). The Tuya SDK's "enum" conversion then falls back to the FIRST
value of the range - "alarm" - so Home Assistant shows smoke that is not
there, and it stays, because the detector never sends "normal" (for it there
was no alarm). The Tuya app shows nothing.

This guard sits in front of the SDK's report handler of every Tuya manager
and drops a report item whose value is neither in the data point's own
mapping nor in its range. A real value ("1", "alarm", "normal" ...) passes
untouched; the state simply stays as it was for an unknown one.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
import contextlib
import json
import time
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback

from .const import LOGGER, TUYA_DOMAIN

_ATTR = "_on_device_report"


def _range_of(config_item: dict[str, Any]) -> list[str]:
    try:
        values = json.loads(config_item.get("valueDesc") or "{}").get("range") or []
    except (TypeError, ValueError):
        return []
    return [str(v) for v in values]


def unknown_items(device: Any, status: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The report items an enum conversion would turn into a made-up value."""
    strategy = getattr(device, "local_strategy", None) or {}
    out = []
    for item in status or []:
        if not isinstance(item, dict) or "dpId" not in item or "value" not in item:
            continue
        rule = strategy.get(item["dpId"])
        if not rule or rule.get("value_convert") != "enum":
            continue
        config = rule.get("config_item") or {}
        mapping = config.get("enumMappingMap") or {}
        raw = str(item["value"])
        known = set(mapping) | set(_range_of(config))
        if raw not in known and raw.lower() not in known:
            out.append(item)
    return out


class UnknownValueGuard:
    """Wraps the report handler of each loaded Tuya manager."""

    def __init__(self, hass: HomeAssistant, on_change: Callable[[], None]) -> None:
        """Nothing is wrapped until check() runs."""
        self.hass = hass
        self._on_change = on_change
        # id(manager) -> (manager, its own attribute before ours or None)
        self._wrapped: dict[int, tuple[Any, Any]] = {}
        self.ignored = 0
        self.recent: deque[dict[str, Any]] = deque(maxlen=20)

    @callback
    def check(self, *_args: Any) -> None:
        """Wrap new managers (start, reload of Tuya); forget gone ones."""
        live: set[int] = set()
        for entry in self.hass.config_entries.async_entries(TUYA_DOMAIN):
            if entry.state is not ConfigEntryState.LOADED:
                continue
            manager = getattr(getattr(entry, "runtime_data", None), "manager", None)
            if manager is None or not callable(getattr(manager, _ATTR, None)):
                continue
            live.add(id(manager))
            if id(manager) not in self._wrapped:
                self._wrap(manager)
        for key in [k for k in self._wrapped if k not in live]:
            del self._wrapped[key]  # its manager is gone with its entry

    def _wrap(self, manager: Any) -> None:
        own_before = vars(manager).get(_ATTR)
        original = getattr(manager, _ATTR)
        hass = self.hass

        def guarded(device_id: str, status: list, *args: Any, **kwargs: Any) -> Any:
            try:
                device = manager.device_map.get(device_id)
                if device is not None and getattr(device, "support_local", False):
                    bad = unknown_items(device, status)
                    if bad:
                        status = [i for i in status if i not in bad]
                        hass.loop.call_soon_threadsafe(self._record, device, bad)
                        if not status:
                            return None
            except Exception:  # noqa: BLE001 - never stop a real report
                LOGGER.debug("Unknown value check failed", exc_info=True)
            return original(device_id, status, *args, **kwargs)

        guarded.__tuya_keep__ = True  # type: ignore[attr-defined]
        setattr(manager, _ATTR, guarded)
        self._wrapped[id(manager)] = (manager, own_before)

    @callback
    def _record(self, device: Any, items: list[dict[str, Any]]) -> None:
        strategy = getattr(device, "local_strategy", None) or {}
        for item in items:
            rule = strategy.get(item.get("dpId")) or {}
            code = rule.get("status_code") or str(item.get("dpId"))
            self.ignored += 1
            self.recent.appendleft(
                {
                    "device": str(getattr(device, "name", "") or "")[:60],
                    "code": code,
                    "value": str(item.get("value"))[:20],
                    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
            )
            LOGGER.info(
                "Ignored an unknown value %r for %s of a Tuya device (it would have "
                "shown as the first value of its range)",
                item.get("value"),
                code,
            )
        self._on_change()

    @callback
    def stop(self) -> None:
        """Put every handler back as it was."""
        for manager, own_before in self._wrapped.values():
            current = vars(manager).get(_ATTR)
            if current is None or not getattr(current, "__tuya_keep__", False):
                continue  # replaced by someone else meanwhile: leave it
            if own_before is not None:
                setattr(manager, _ATTR, own_before)
            else:
                with contextlib.suppress(AttributeError):
                    delattr(manager, _ATTR)
        self._wrapped.clear()
