# Firewalla for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)

Monitor one or multiple Firewalla MSP-managed devices from Home Assistant.
Built against the [Firewalla MSP API v2](https://docs.firewalla.net/) for Home Assistant 2024.1+.

---

## Features

| Feature | Default | Toggle |
|---|---|---|
| Box online/offline status | ✅ Always | — |
| Device online/offline sensors | ✅ Always | — |
| Device Tracker (presence detection) | ✅ On | Options → Device Tracker |
| IP / MAC / Network sensors per device | ✅ Always | — |
| Bandwidth (download/upload) per device | ❌ Off | Options → Bandwidth Sensors |
| Active alarm count + details | ❌ Off | Options → Alarm Sensors |
| Individual alarm binary sensors | ❌ Off | Options → Alarm Sensors |
| Firewall rule switch (active/paused toggle) | ❌ Off | Options → Rule Sensors |
| Per-flow traffic sensors | ❌ Off | Options → Flow Sensors |
| Automatic stale device cleanup | ✅ 30 days | Options → Stale Device Removal |

### Actions (Services)

Call these from automations, scripts, or **Developer Tools → Actions**:

| Service | Description |
|---|---|
| `firewalla.delete_alarm` | Delete/dismiss an alarm (requires Alarm Sensors enabled) |
| `firewalla.rename_device` | Rename a network device (requires MSP 2.9+) |

Firewall rules are paused and resumed using the native `switch.turn_off` / `switch.turn_on`
services targeting the rule's switch entity — no custom service required.

---

## Installation

### Option 1 — HACS (Recommended)

HACS gives you one-click installs and automatic update notifications.

**If you don't have HACS yet:**
1. Follow the [HACS installation guide](https://hacs.xyz/docs/use/download/download/) to install it in Home Assistant.

**Add this repository to HACS:**
1. In Home Assistant, go to **HACS** in the sidebar
2. Click the three-dot menu (⋮) in the top-right corner
3. Select **Custom repositories**
4. In the **Repository** field paste:
   ```
   https://github.com/shanelord01/hass-firewalla-ng
   ```
5. Set **Type** to **Integration** and click **Add**
6. Search for **Firewalla** in HACS and click **Download**
7. Restart Home Assistant when prompted

### Option 2 — Manual

1. Download this repository as a ZIP (click **Code → Download ZIP** on GitHub)
2. Unzip it and copy the `custom_components/firewalla` folder into your Home Assistant
   `config/custom_components/` directory
   *(create `custom_components` if it doesn't exist)*
3. Restart Home Assistant

---

## Setup

After installing and restarting:

1. Go to **Settings → Devices & Services**
2. Click **+ Add Integration** and search for **Firewalla**
3. Enter your **MSP Subdomain** — the part before `.firewalla.net`
   *(e.g. enter `mycompany` for `mycompany.firewalla.net`)*
4. Enter your **API Token**
   — In the Firewalla MSP portal go to **Account Settings → Create New Token**, give it a name, and copy the token
5. Choose which optional features to enable (you can change these later)
6. Click **Submit**

---

## Options

All options can be changed after setup via **Settings → Devices & Services → Firewalla → Configure**:

| Option | Description | Default |
|---|---|---|
| Poll Interval | How often to query the API (seconds) | 300s (5 min) |
| Enable Alarm Sensors | Alarm count + per-alarm binary sensors | Off |
| Enable Rule Sensors | Active/paused switch per firewall rule | Off |
| Enable Flow Sensors | Per-flow transfer sensor (can create many entities) | Off |
| Enable Bandwidth Sensors | Download/upload totals per device | Off |
| Enable Device Tracker | Presence detection via ScannerEntity | On |
| Stale Device Removal | Days before absent devices are removed from HA | 30 |

---

## Using the Actions (Services)

### Controlling Firewall Rules

When **Enable Rule Sensors** is on, each firewall rule gets a **switch entity** on the box
device card. The switch reflects live rule state — **On = Active**, **Off = Paused** — and can
be toggled directly from the dashboard or targeted in automations using the standard switch
services:

```yaml
# Pause a rule
action: switch.turn_off
target:
  entity_id: switch.firewalla_block_netflix_rule

# Resume a rule
action: switch.turn_on
target:
  entity_id: switch.firewalla_block_netflix_rule
```

The rule entity ID will match the pattern `switch.firewalla_<action>_<target>_rule`.
You can find the exact entity ID in **Settings → Devices & Services → [your Firewalla box] → entities**.

### Delete an Alarm

Requires **Alarm Sensors** to be enabled in options.

In **Developer Tools → Actions**, select `Firewalla: Delete Alarm` and use the entity picker
to choose the alarm's binary sensor — no need to find internal IDs.

In automations or scripts:

```yaml
action: firewalla.delete_alarm
target:
  entity_id: binary_sensor.firewalla_alarm_intrusion_detected
```

### Rename a Device

Requires **Firewalla MSP 2.9+**.

In **Developer Tools → Actions**, select `Firewalla: Rename Device` and use the device picker
to choose the network device, then enter the new name.

In automations or scripts:

```yaml
action: firewalla.rename_device
target:
  device_id: a1b2c3d4e5f6g7h8
data:
  name: "My Laptop"
```

The `device_id` here is the Home Assistant device ID, visible in the URL when viewing the
device page under **Settings → Devices & Services**.

---

## Migrating from v2.2.x

If you have existing automations using `firewalla.pause_rule` or `firewalla.resume_rule`,
update them to use the native switch services:

| Old | New |
|---|---|
| `firewalla.pause_rule` with `rule_id: "abc123"` | `switch.turn_off` targeting the rule switch entity |
| `firewalla.resume_rule` with `rule_id: "abc123"` | `switch.turn_on` targeting the rule switch entity |

The rule switch entity ID can be found in the Firewalla box device card after enabling
Rule Sensors. The `rule_id` value is still exposed as an attribute on the switch entity
if you need it for reference.

---

## Stale Device Cleanup

Devices not seen via the API for the configured number of days (default 30)
are automatically removed from the Home Assistant device registry.

**Protected devices** — those referenced by automations, scenes, or scripts —
are never removed automatically. You can still manually delete them via
**Settings → Devices & Services → [device] → Delete**.

---

## Debug Logging

```yaml
# configuration.yaml
logger:
  default: info
  logs:
    custom_components.firewalla: debug
```

---

## Credits

- Original integration: [blueharford/hass-firewalla](https://github.com/blueharford/hass-firewalla)
- Refactored: [DaneManes/hass-firewalla](https://github.com/DaneManes/hass-firewalla)
- Rewritten for HA 2024.1+: [shanelord01/hass-firewalla-ng](https://github.com/shanelord01/hass-firewalla-ng)
- Services and API fixes: [TechButton/hass-firewalla-ng](https://github.com/TechButton/hass-firewalla-ng)

---

## Changelog

### v2.3.0
- Replace rule toggle button with a native **switch entity** per firewall rule — On = Active, Off = Paused
- Switch state is reflected live in the dashboard; icon changes contextually (shield/shield-off)
- Remove `firewalla.pause_rule` and `firewalla.resume_rule` custom services — use `switch.turn_off` / `switch.turn_on` instead (standard HA services work in all automations and scripts)
- Upgrade `firewalla.delete_alarm` to use a **target entity selector** — pick the alarm from a dropdown in Developer Tools instead of entering a raw ID
- Upgrade `firewalla.rename_device` to use a **target device selector** — pick the device from a dropdown in Developer Tools instead of entering a raw MAC address

### v2.2.1
- Fix rule active sensor not rendering shield icon — missing `_attr_translation_key` on `FirewallaRuleActiveSensor`

### v2.2.0
- Fix flow fetching: API query parameter was `count` instead of `limit` — flows now correctly respect the configured limit
- Add `firewalla.pause_rule` service — pause an active firewall rule from HA automations
- Add `firewalla.resume_rule` service — resume a paused firewall rule from HA automations
- Add `firewalla.delete_alarm` service — dismiss an alarm from HA automations
- Add `firewalla.rename_device` service — rename a network device from HA automations (MSP 2.9+)
- Services are available in Developer Tools and work across all configured Firewalla accounts

### v2.1.7
- Fix orphaned entities for Bandwidth and Device Tracker when disabled

### v2.1.6
- Fix orphaned entities showing as Unavailable when features are disabled

### v2.1.5
- Fix circular import preventing the integration from appearing in Settings → Devices & Services → Add Integration after installing v2.1.4

### v2.1.4
- Fix box device names duplicating "Firewalla" when the box name already contains it
- Fix rule sensors all displaying as "Active" with no context — now shows action and target
- Fix alarm sensors all displaying as "Active" with no context — now shows alarm message
- Fix device manufacturer showing as "Firewalla" in some cases

### v2.1.3
- Fix device manufacturer displaying as "Firewalla" for all network devices — now shows hardware vendor

### v2.1.2
- Fix alarm count sensor unique_id collision when multiple MSP accounts are configured
- Fix coordinator API failure detection

### v2.1.1
- Multi-box support with per-box filtering during setup and in options
- Network devices now nest under their parent Firewalla box in the HA device hierarchy
- Fixed manufacturer showing as "Unknown" for device sensors

### v2.0.0
- Full rewrite for Home Assistant 2024.1+
- Dedicated `FirewallaCoordinator` with stale device cleanup
- `async_remove_config_entry_device` support
- Full `icons.json`, `strings.json`, and options flow support
