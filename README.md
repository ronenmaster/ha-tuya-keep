<p align="center">
  <img src="custom_components/tuya_keep/brand/icon@2x.png" alt="Tuya Keep" width="128">
</p>

<h1 align="center">Tuya Keep</h1>

<h3 align="center">The last value of every Tuya sensor, right after a restart,<br>and no more false smoke alarms.</h3>

<p align="center">
  <a href="https://hacs.xyz"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg" alt="HACS Custom"></a>
  <a href="https://github.com/ronenmaster/ha-tuya-keep/releases"><img src="https://img.shields.io/github/v/release/ronenmaster/ha-tuya-keep" alt="Release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT"></a>
</p>

<p align="center"><a href="README.he.md">עברית</a></p>

---

## What you get

| | Without Tuya Keep | With Tuya Keep |
|---|---|---|
| **After a restart** | Tuya sensors show **Unknown** for up to an hour | Each Tuya number sensor gets a **"last value" companion** that shows the last value at once, marked as not live. **The Tuya sensors are not touched** |
| **Smoke detectors** | **"Smoke detected"** every few hours, with no smoke, and it stays | **No false alarms** from unknown values. **Real alarms are never touched** |
| **Dead batteries** | A device that stopped reporting goes unnoticed | It is **listed as silent**, with the time it was last seen |

Works for every current and future Tuya device, with nothing to set per device. Tuya Keep works next to the official Tuya integration: it does not replace it and does not change its files. When Home Assistant fixes these problems itself, you simply remove it.

---

## Contents

- [Problem 1: "Unknown" after a restart](#problem-1-unknown-after-a-restart)
- [Problem 2: false smoke alarms](#problem-2-false-smoke-alarms)
- [What Tuya Keep does](#what-tuya-keep-does)
- [Install](#install)
- [Set up](#set-up)
- [Settings](#settings)
- [What you see in Home Assistant](#what-you-see-in-home-assistant)
- [Optional: real values from the Tuya cloud](#optional-real-values-from-the-tuya-cloud)
- [How it works (details)](#how-it-works-details)
- [Limits](#limits)
- [Privacy](#privacy)
- [When to remove it](#when-to-remove-it)
- [Reporting a problem to Home Assistant](#reporting-a-problem-to-home-assistant)
- [Troubleshooting](#troubleshooting)
- [Development](#development)

---

## Problem 1: "Unknown" after a restart

After you restart Home Assistant, or reload the Tuya integration, some Tuya devices show **Unknown** for their sensors:

- temperature and humidity sensors (very common),
- other battery powered or "sleepy" devices (Zigbee or Bluetooth sub-devices behind a gateway),
- sometimes battery level, illuminance and similar values.

They stay **Unknown** until the device sends its next report, which for these devices can take **15 to 60 minutes or more**. Meanwhile dashboards show "Unknown" (gauge cards say **"Entity is non-numeric"**), automations and templates that use the value fail, history graphs get a hole, and every app that reads Home Assistant shows wrong data. The Tuya app shows the right values the whole time.

**Why:** the official integration reads your devices through Tuya's "Smart Life / Tuya app" sharing API. For some devices, mainly battery devices that sleep most of the time, that API returns an **empty status** when the integration starts. The integration then has nothing to show until the device pushes its next report. Tuya sensors do not restore their last state either. Reported upstream: [home-assistant/core#178105](https://github.com/home-assistant/core/issues/178105).

## Problem 2: false smoke alarms

Some Tuya smoke detectors (seen on a Wi-Fi "Smoke alarm") show **smoke detected** in Home Assistant every few hours, with no smoke, and stay that way. The detector does not beep and the Tuya app shows nothing.

**Why** (found in the Tuya debug log): at its routine check-in the detector sends its smoke data point with a value its own definition does not list, for example `"23"`, where the definition knows only `"1"`/`"alarm"` and `"2"`/`"normal"`. The Tuya SDK used by the official integration turns any value it cannot map into the **first value of the list**, which for these detectors is `"alarm"`. The detector never sends "normal" afterwards (for it there was no alarm), so the false alarm stays.

## What Tuya Keep does

### 1. Keeps the last value (no account, no internet)

- It watches every sensor of the official Tuya integration and remembers its **last real value**, with the time it was last seen.
- **Companion sensors (the default):** next to each Tuya number sensor it adds its own sensor, *"&lt;name&gt; last value"*, on the same device. While the Tuya sensor reports, the companion shows the same value (`live: true`). When the Tuya sensor turns **Unknown**, the companion keeps showing the last value, marked as not live (`live: false`, `last_seen`, a history icon), and after the age limit it turns Unknown too. It restores itself across restarts the standard Home Assistant way. **The Tuya entities themselves are never written to**, as the Home Assistant Tuya maintainers advise. Point a dashboard card or an automation at the companion when you want the last known value, and at the Tuya sensor when you want only live readings.
- **In place (optional, the first versions' way):** the last value is written back into the Tuya sensor itself, marked with two attributes. Entries made before version 1.4.0 keep this mode until you change it.
- It works for **every current and future Tuya device**, with nothing to set per device. On the first start it takes the last values from the Home Assistant **recorder history**, so it helps from the very first restart.

### 2. Ignores unknown values (the false alarms fix)

- It sits in front of the Tuya SDK's report handler and **ignores a value that is neither in the data point's own mapping nor in its range**. The state simply stays as it was.
- Real values (`"1"`, `"alarm"`, `"2"`, `"normal"` and so on) pass untouched, so **a real alarm is never hidden**.
- It works for every Tuya device with such a data point, not only smoke detectors. On by default.
- The proper fix for a given product is a quirk in [tuya-device-handlers](https://github.com/home-assistant-libs/tuya-device-handlers) ([adding missing ENUM values](https://github.com/home-assistant-libs/tuya-device-handlers/blob/main/docs/adding-missing-enum-values.md)), and the SDK's fallback belongs in [tuya-device-sharing-sdk](https://github.com/tuya/tuya-device-sharing-sdk). This guard covers every product until then.

### 3. Shows what is going on

Live sensors on its page: which sensors show a kept value now, **which went silent** (no report for hours: often an empty battery or a device out of range, which Home Assistant itself does not notice), which came up "Unknown" at the last start, and which unknown values were ignored. See [What you see in Home Assistant](#what-you-see-in-home-assistant).

### 4. Optional: real values from the Tuya cloud

With the keys of your own (free) Tuya IoT cloud project, a few seconds after a restart it reads the **real last values** of the devices whose status came back empty, and hands them to the official integration's own update path, as if the devices had just reported them. Only number readings are taken from the cloud, never alarm or on/off states. If anything fails, part 1 stays in charge. See [below](#optional-real-values-from-the-tuya-cloud).

## Install

### HACS (recommended)

1. HACS → three dots menu → **Custom repositories**.
2. Add `https://github.com/ronenmaster/ha-tuya-keep`, type **Integration**.
3. Find **Tuya Keep** in HACS and download it.
4. **Restart Home Assistant.**

New versions show up in HACS by themselves (HACS checks custom repositories every few hours; *three dots → Update information* checks at once). After an update, restart Home Assistant.

### Manual

1. Download this repository (Code → Download ZIP) and unpack it.
2. Copy the folder `custom_components/tuya_keep` into your Home Assistant configuration folder, so that you have `<config>/custom_components/tuya_keep/manifest.json` and the other files next to it. `<config>` is the folder that holds `configuration.yaml` (`/config` in Home Assistant OS; in a Docker install, the host folder mapped to `/config`).
3. **Restart Home Assistant.**

## Set up

1. **Settings → Devices & services → Add integration → Tuya Keep.**
2. Confirm. That's all: one entry covers all Tuya devices.

The official Tuya integration must be set up as usual. Tuya Keep only works next to it.

## Settings

**Settings → Devices & services → Tuya Keep → Configure.** Saving reloads Tuya Keep; no restart is needed.

| Setting | Default | What it does |
|---|---|---|
| How to keep the last value | Companion sensors | *Companion sensors* (recommended): a "last value" sensor next to each Tuya sensor, the Tuya sensors untouched. *In the Tuya sensor itself*: the value is written back into it (the first versions' way; entries made before 1.4.0 keep it). |
| Keep a value for at most | 24 h | A kept value older than this is not shown: the companion (in place mode: the Tuya sensor) shows Unknown until the device reports. 0 = no limit. |
| Also keep text values | off | Off: only numbers (temperature, humidity, battery, power...). On: also text states such as `low` or `charging`. |
| Integrations to watch | `tuya` | Sensors of these integrations are watched. `tuya` is the official Tuya integration. |
| Sensors to leave alone | none | These sensors are never restored. |
| Ignore unknown values (false alarms) | on | A value a device does not define is ignored instead of showing as the first value of its list (for smoke detectors, "alarm"). Real values are never touched. |
| Silent after | 6 h | A watched sensor with no new value for this long is listed in *Silent sensors*. 0 = off. |
| Read real values from the Tuya cloud | not used | The optional cloud part. |

## What you see in Home Assistant

**Settings → Devices & services → Tuya Keep → the *Tuya Keep* service** shows live sensors:

| Sensor | Shows |
|---|---|
| *&lt;name&gt; last value* (one per Tuya number sensor, on its device) | Companion mode: the last real value of that sensor; `live` says whether it is live right now, `last_seen` when it was really reported. |
| Showing a kept value now | How many sensors show a kept value right now. Its attributes list them by name, with the time each value was really seen. |
| Watched sensors | How many sensors are watched; the attributes list them. |
| Values restored since start | How many times a value was put back since Home Assistant started. |
| Last restore | When a value was last put back. |
| Silent sensors | Watched sensors with no new value for longer than *Silent after*, with the time each was last seen. |
| Came up unknown at the last start | Which sensors came up "Unknown" at the last start of Home Assistant or reload of Tuya. When this stays 0 after several restarts, the official integration has probably fixed problem 1. |
| Unknown values ignored | How many unknown values were ignored since the start; the attribute `recent` lists the last ones (device, data point, value, time). |
| Values filled from the cloud, Cloud status | Only with the cloud part: how many real values came from the cloud, and whether the last read worked (`last_error` says why not). |

The long lists are kept out of the recorder, so they do not grow the database.

**In place mode only:** a kept value written into a Tuya sensor carries these attributes (Developer tools → States):

```yaml
tuya_keep_restored: true
tuya_keep_last_seen: "2026-01-01T10:15:00+00:00"   # when the device really reported it
```

They disappear when the device reports again. You can use them in templates:

```jinja
{% if state_attr('sensor.my_sensor_temperature', 'tuya_keep_restored') %}
  last known value, seen {{ relative_time(as_datetime(state_attr('sensor.my_sensor_temperature', 'tuya_keep_last_seen'))) }} ago
{% endif %}
```

**Diagnostics:** Tuya Keep → three dots → *Download diagnostics* shows counts only. It never includes keys or entity names.

## Optional: real values from the Tuya cloud

This needs a free developer account on the Tuya IoT platform and about 10 minutes. You can skip it.

1. Go to [platform.tuya.com](https://platform.tuya.com) (Tuya IoT platform) and sign up (a separate account from the app).
2. **Cloud → Development → Create Cloud Project**: Industry *Smart Home*, development method **Smart Home**, data center the **same region as your Smart Life / Tuya app account** (most of Europe, the Middle East and Africa use *Central Europe*; the Americas *Western America* or *Eastern America*; China *China*; India *India*).
3. In the project, open **Service API** and make sure **IoT Core** is authorized (it usually is by default).
4. **Devices → Link Tuya App Account → Add App Account**, then scan the QR code with the Smart Life (or Tuya) app: *Me → scan icon*. This step is required: without it the cloud answers "permission deny".
5. On the project's **Overview** page copy the **Access ID** and the **Access Secret**.
6. In Home Assistant: **Tuya Keep → Configure → Read real values from the Tuya cloud**: choose the data center and paste the two keys. They are checked before saving.

Tuya Keep only **reads** (one call per device that needs it, only after a restart or reload) and never sends commands. Tuya's free "IoT Core" plan may need to be renewed from time to time in the Tuya IoT platform (Cloud → Cloud Services).

## How it works (details)

- **Which sensors:** every `sensor` entity whose integration is in *Integrations to watch* (default `tuya`), minus *Sensors to leave alone*, checked live, so devices added later are covered at once.
- **Remembering:** each real state (and each repeated report of the same value) updates the kept value and its time, saved in `.storage/tuya_keep.last_values` (at most once a minute, and on shutdown). Values not seen for 30 days are dropped.
- **Companion sensors:** each is a `RestoreSensor` of this integration, linked to the Tuya sensor's device, following the Tuya sensor's state changes and reports; numbers only unless text values are kept. New Tuya sensors get one at once, removed ones lose it.
- **In place restoring:** when a watched sensor becomes `unknown`, the kept value is written into Home Assistant's state machine with the sensor's own attributes plus the two markers. The "unknown" state reaches every other listener first, so nothing gets the two out of order. If a sensor has no kept value yet, the newest real value in the recorder history (last 10 days, within the age limit) is used.
- **Unknown values:** each loaded Tuya manager's report handler is wrapped at run time (and wrapped again after a reload of Tuya; put back on unload). For devices with a local data point strategy, a report item whose `enum` value is in neither the data point's `enumMappingMap` nor its range is dropped before the SDK converts it. A report with nothing left is not passed on.
- **Silent sensors:** a watched sensor whose last real value is older than *Silent after*, checked every 5 minutes.
- **Cloud part:** `GET /v2.0/cloud/thing/{device_id}/shadow/properties` of Tuya's OpenAPI, signed with your project keys, only for devices missing number values, and only number (Integer) values the device has none of. The values go through the Tuya SDK's own report handler. Network errors are retried up to 3 times; a refusal is not.
- **Never touched:** `unavailable` states (the device is offline: that is the truth), other integrations, excluded sensors, values older than the limit.

## Limits

- Companion mode: a dashboard or automation that should keep working through a restart has to point at the *last value* companion, not at the Tuya sensor.
- A kept value is the **last known** value, not a live one. That's why it is marked and has an age limit.
- Only `sensor` entities are restored. Binary sensors (door, motion, smoke...) are not, on purpose: an old "clear" or "detected" could be dangerous.
- The unknown value guard covers devices that report by data point id (most Wi-Fi devices); a value that really meant something but is not in the device's own definition is ignored too. A false alarm that was already showing before you installed Tuya Keep clears at the next restart.
- A sensor that never reported while Tuya Keep (or the recorder) was running has nothing to restore.
- The cloud part depends on Tuya's OpenAPI; if Tuya changes it, that part stops helping and the rest continues.

## Privacy

- Everything except the optional cloud part works inside your Home Assistant. Nothing leaves your home.
- The cloud part talks only to Tuya's official API, with your own project keys, and only reads. Keys are stored like every other integration's keys and are hidden from diagnostics.
- No telemetry, no analytics, no other servers.

## When to remove it

- **Problem 1:** watch [home-assistant/core#178105](https://github.com/home-assistant/core/issues/178105), and the sensor *Came up unknown at the last start*: when it stays 0 after several restarts, the fix has probably landed.
- **Problem 2:** it is in the Tuya SDK's enum conversion; until that changes, removing Tuya Keep brings the false alarms back (or turn off only the other parts in its settings).

To remove: **Settings → Devices & services → Tuya Keep → three dots → Delete** (its stored values file goes with it), remove it from HACS, restart Home Assistant. Nothing in the official integration needs to be undone.

## Reporting a problem to Home Assistant

Before you report a Tuya problem to Home Assistant, **reproduce it with Tuya Keep disabled** (Settings → Devices & services → Tuya Keep → three dots → Disable, then restart), as the maintainers ask: Tuya Keep changes how Tuya values arrive. Problems with Tuya Keep itself go to [this repository's issues](https://github.com/ronenmaster/ha-tuya-keep/issues), not to Home Assistant's.

## Troubleshooting

| What you see | What to check |
|---|---|
| The Tuya sensor is Unknown after a restart | Expected in companion mode: the Tuya sensor is left as Home Assistant has it. Look at its *last value* companion (`sensor.<name>_last`). |
| The companion (or, in place mode, the sensor) is still Unknown | Was there a real value in the last 24 h (or your limit)? A brand new sensor has nothing to keep yet. Is the sensor's integration `tuya`? |
| A smoke detector still shows smoke | A false alarm from before the update clears at the next restart. Afterwards, check *Unknown values ignored*: its `recent` attribute shows what was ignored. A real alarm (`"1"` or `"alarm"`) is never ignored. |
| Values are restored but very old | Lower *Keep a value for at most*, or set up the cloud part. |
| Companions are called "Tuya Keep ... last value" | Update to 1.4.1 or later and restart: they move to their Tuya device and take its name. |
| A sensor is listed as silent | Check its battery and range; the time it was last seen is in the attributes. |
| Cloud: "The Tuya cloud refused these keys" | The data center must match the project. Copy the keys again from the Overview page. |
| Cloud saves, but values are still kept ones | Link the app account to the project (step 4). Check diagnostics: `cloud.last_error`. |
| I want to see what it does | Add to `configuration.yaml`: `logger:` → `logs:` → `custom_components.tuya_keep: debug`. |

## Development

```bash
pip install pytest-homeassistant-custom-component tuya-device-sharing-sdk ha-ffmpeg
pytest
```

45 tests. They include end to end runs with the real official Tuya integration and the real Tuya SDK, with the cloud replaced by an offline stand-in: one returns an empty status (problem 1), one sends the detector's `"23"` (problem 2: without the guard the smoke sensor turns on, with it the state stays, and `"1"`/`"2"` still work).

## Disclaimer

Not affiliated with, endorsed by or connected to Tuya or Home Assistant. "Tuya" and "Smart Life" are names of their owners, used only to describe what this works with.

## License

[MIT](LICENSE)
