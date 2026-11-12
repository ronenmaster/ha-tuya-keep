"""Layer 2 (optional): read the real values from the Tuya cloud.

The official integration talks to Tuya through the "Sharing" (Smart Life app)
API. For some devices that API returns an empty status right after the
integration starts, so the integration has nothing to show until the device
pushes its next report. The Tuya IoT platform (OpenAPI) keeps the last values
of every device in its "shadow". With the user's own cloud project keys this
module reads that shadow and hands the values to the official integration's
own update path, exactly as if the device had just reported them. The
official entities then show real values (not restored ones).

Everything here is best effort: any failure only leaves layer 1 in charge.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import hashlib
import hmac
import time
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback

from .const import (
    LOGGER,
    SHADOW_DEBOUNCE,
    SHADOW_RETRIES,
    SHADOW_RETRY_DELAY,
    TUYA_DOMAIN,
)

_TIMEOUT = aiohttp.ClientTimeout(total=15)
_TOKEN_INVALID = {1010, 1011}  # token invalid / token expired


class TuyaCloudError(Exception):
    """A Tuya OpenAPI call failed."""

    def __init__(self, code: Any, msg: str) -> None:
        """Keep the code and the message of Tuya's answer."""
        super().__init__(f"{code}: {msg}")
        self.code = code
        self.msg = msg


class TuyaOpenApi:
    """A tiny Tuya OpenAPI client (only what is needed here)."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        endpoint: str,
        access_id: str,
        access_secret: str,
    ) -> None:
        """Keep the keys; the token is fetched on first use."""
        self._session = session
        self._endpoint = endpoint.rstrip("/")
        self._id = access_id
        self._secret = access_secret
        self._token: str | None = None
        self._token_until = 0.0
        self._lock = asyncio.Lock()

    def _sign(self, method: str, url: str, body: str, t: str, with_token: bool) -> str:
        """Tuya's HMAC-SHA256 request signature."""
        content_hash = hashlib.sha256(body.encode()).hexdigest()
        string_to_sign = f"{method}\n{content_hash}\n\n{url}"
        token = (self._token or "") if with_token else ""
        message = self._id + token + t + string_to_sign
        return (
            hmac.new(self._secret.encode(), message.encode(), hashlib.sha256)
            .hexdigest()
            .upper()
        )

    async def _request(
        self, path: str, params: dict[str, Any] | None, with_token: bool
    ) -> Any:
        query = ""
        if params:
            query = "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
        url = path + query
        t = str(int(time.time() * 1000))
        headers = {
            "client_id": self._id,
            "sign": self._sign("GET", url, "", t, with_token),
            "sign_method": "HMAC-SHA256",
            "t": t,
            "lang": "en",
        }
        if with_token:
            headers["access_token"] = self._token or ""
        async with self._session.get(
            self._endpoint + url, headers=headers, timeout=_TIMEOUT
        ) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
        if not isinstance(data, dict) or not data.get("success"):
            code = data.get("code") if isinstance(data, dict) else None
            msg = data.get("msg", "") if isinstance(data, dict) else str(data)
            raise TuyaCloudError(code, msg)
        return data.get("result")

    async def _ensure_token(self, force: bool = False) -> None:
        async with self._lock:
            if not force and self._token and time.time() < self._token_until:
                return
            self._token = None
            result = await self._request("/v1.0/token", {"grant_type": 1}, False)
            self._token = result["access_token"]
            # Renew a minute before Tuya would expire it.
            life = int(result.get("expire_time") or result.get("expire") or 3600)
            self._token_until = time.time() + max(life - 60, 30)

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Signed GET with a valid token (renewed once when Tuya says so)."""
        await self._ensure_token()
        try:
            return await self._request(path, params, True)
        except TuyaCloudError as err:
            if err.code not in _TOKEN_INVALID:
                raise
        await self._ensure_token(force=True)
        return await self._request(path, params, True)

    async def check(self) -> None:
        """Fetch a token: proves the keys and the data center are right."""
        await self._ensure_token(force=True)

    async def ensure_token(self) -> None:
        """Have a valid token (fetch one only when needed)."""
        await self._ensure_token()

    async def shadow(self, device_id: str) -> list[dict[str, Any]]:
        """Return the shadow properties (last values) of one device."""
        result = await self.get(f"/v2.0/cloud/thing/{device_id}/shadow/properties")
        props = (result or {}).get("properties") if isinstance(result, dict) else None
        return props if isinstance(props, list) else []


# Only number readings (temperature, humidity, battery...) are filled from
# the cloud. Alarm and on/off states (smoke, water, door, motion) are Enum or
# Boolean and are never filled: an old "alarm" kept in the cloud would raise
# a false alarm in Home Assistant.
_FILL_TYPES = {"integer"}


def _fillable_codes(device: Any) -> set[str]:
    """Status codes whose old value is safe to show again (numbers)."""
    ranges = getattr(device, "status_range", None) or {}
    return {
        code
        for code, spec in ranges.items()
        if str(getattr(spec, "type", "") or "").lower() in _FILL_TYPES
    }


def _missing_codes(device: Any) -> set[str]:
    """Number codes the device should have but has no value for."""
    status = getattr(device, "status", None) or {}
    return {code for code in _fillable_codes(device) if status.get(code) is None}


def build_report(device: Any, props: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn shadow properties into the status list of a device report.

    Only values the device has none of are included, so a fresher value
    that came in the meantime is never overwritten.
    """
    missing = _missing_codes(device)
    if not missing:
        return []
    report: list[dict[str, Any]] = []
    local = getattr(device, "support_local", False)
    strategy = getattr(device, "local_strategy", None) or {}
    for prop in props:
        value = prop.get("value")
        if value is None:
            continue
        if local:
            # Devices with a "local strategy" report raw data points by id;
            # the SDK converts them with the same rules as a real report.
            try:
                dp_id = int(prop.get("dp_id"))
            except (TypeError, ValueError):
                continue
            item = strategy.get(dp_id)
            if not item or item.get("status_code") not in missing:
                continue
            entry: dict[str, Any] = {"dpId": dp_id, "value": value}
            if isinstance(prop.get("time"), int):
                entry["t"] = prop["time"]
            report.append(entry)
        else:
            code = prop.get("code")
            if code in missing:
                report.append({"code": code, "value": value})
    return report


class ShadowSync:
    """Fill empty device status of the official integration from the cloud."""

    def __init__(self, hass: HomeAssistant, api: TuyaOpenApi) -> None:
        """Nothing runs until trigger() is called."""
        self.hass = hass
        self._api = api
        # (entry_id, id(manager)) -> device ids already handled
        self._done: dict[tuple[str, int], set[str]] = {}
        self._timer: asyncio.TimerHandle | None = None
        self._task: asyncio.Task | None = None
        self._again = False
        self._stopped = False
        self.on_update: Callable[[], None] | None = None
        self.stats: dict[str, Any] = {
            "devices_filled": 0,
            "values_filled": 0,
            "devices_empty": 0,
            "errors": 0,
            "last_error": None,
            "last_run": None,
        }

    @callback
    def trigger(self, _entity_id: str | None = None) -> None:
        """Ask for a run a few seconds from now (calls are batched)."""
        if self._stopped:
            return
        if self._timer is not None:
            self._timer.cancel()
        self._timer = self.hass.loop.call_later(SHADOW_DEBOUNCE, self._start)

    @callback
    def stop(self) -> None:
        """Stop all timers and the running task."""
        self._stopped = True
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._task is not None and not self._task.done():
            self._task.cancel()

    @callback
    def _start(self) -> None:
        self._timer = None
        if self._task is not None and not self._task.done():
            self._again = True
            return
        self._task = self.hass.async_create_background_task(
            self._run(), "tuya_keep shadow sync"
        )

    async def _run(self) -> None:
        try:
            for attempt in range(SHADOW_RETRIES):
                self._again = False
                failed = await self._sync_all()
                if self._again:
                    continue
                if not failed or attempt == SHADOW_RETRIES - 1:
                    break
                await asyncio.sleep(SHADOW_RETRY_DELAY)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - layer 1 keeps working
            LOGGER.exception("Tuya cloud sync failed")
        self.stats["last_run"] = time.time()
        if self.on_update is not None:
            self.on_update()

    def _managers(self) -> list[tuple[tuple[str, int], Any]]:
        found = []
        live_keys = set()
        for entry in self.hass.config_entries.async_entries(TUYA_DOMAIN):
            if entry.state is not ConfigEntryState.LOADED:
                continue
            listener = getattr(entry, "runtime_data", None)
            manager = getattr(listener, "manager", None)
            if manager is None or not hasattr(manager, "device_map"):
                continue
            key = (entry.entry_id, id(manager))
            live_keys.add(key)
            found.append((key, manager))
        # A reload makes a new manager: forget what the old one had done.
        for key in list(self._done):
            if key not in live_keys:
                del self._done[key]
        return found

    def _error(self, err: Exception) -> None:
        self.stats["errors"] += 1
        self.stats["last_error"] = _plain(err)
        LOGGER.debug("Tuya cloud read failed: %s", err)

    def _todo(self) -> list[tuple[set[str], Any, Any]]:
        """(done set, manager, device) for each device that misses values."""
        todo: list[tuple[set[str], Any, Any]] = []
        for key, manager in self._managers():
            done = self._done.setdefault(key, set())
            for device in list(manager.device_map.values()):
                device_id = getattr(device, "id", None)
                if not device_id or device_id in done:
                    continue
                if not _missing_codes(device):
                    done.add(device_id)
                    continue
                todo.append((done, manager, device))
        return todo

    async def _sync_all(self) -> bool:
        """One pass over all devices; True when it is worth trying again."""
        todo = self._todo()
        if not todo:
            return False

        try:
            await self._api.ensure_token()
        except TuyaCloudError as err:
            # Wrong keys or data center: no device call can work.
            self._error(err)
            for done, _manager, device in todo:
                done.add(device.id)
            return False
        except (aiohttp.ClientError, TimeoutError) as err:
            self._error(err)
            return True

        failed = False
        for done, manager, device in todo:
            try:
                props = await self._api.shadow(device.id)
            except (TuyaCloudError, aiohttp.ClientError, TimeoutError) as err:
                self._error(err)
                if isinstance(err, TuyaCloudError) and err.code not in (None, 500):
                    # A permanent answer (no permission, no such device):
                    # asking again will not help.
                    done.add(device.id)
                else:
                    failed = True
                continue
            done.add(device.id)
            report = build_report(device, props)
            if not report:
                self.stats["devices_empty"] += 1
                continue
            try:
                await self.hass.async_add_executor_job(
                    _deliver, manager, device, report
                )
            except Exception as err:  # noqa: BLE001 - an SDK change; layer 1 stays
                LOGGER.warning(
                    "The Tuya integration did not accept the cloud values: %s", err
                )
                self.stats["errors"] += 1
                self.stats["last_error"] = f"deliver: {type(err).__name__}"
                continue
            self.stats["devices_filled"] += 1
            self.stats["values_filled"] += len(report)
        return failed


def _deliver(manager: Any, device: Any, report: list[dict[str, Any]]) -> None:
    """Hand the values to the official integration (runs in the executor).

    The SDK's own report handler converts the values and tells the official
    entities, as for a real report pushed by the device. If a future SDK has
    no such handler, the values are set directly and the integration's
    listeners are told.
    """
    handler: Callable[[str, list], None] | None = getattr(
        manager, "_on_device_report", None
    )
    if callable(handler):
        handler(device.id, report)
        return
    codes = []
    for item in report:
        if "code" in item:
            device.status[item["code"]] = item["value"]
            codes.append(item["code"])
    if not codes:
        return
    for listener in list(getattr(manager, "device_listeners", []) or []):
        try:
            listener.update_device(device, codes)
        except Exception:  # noqa: BLE001
            LOGGER.debug("A Tuya listener rejected the update", exc_info=True)


def _plain(err: Exception) -> str:
    if isinstance(err, TuyaCloudError):
        return f"Tuya cloud: {err.msg} ({err.code})"
    return type(err).__name__
