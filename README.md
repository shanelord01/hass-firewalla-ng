# Firewalla for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)

Monitor one or multiple Firewalla MSP-managed devices from Home Assistant. Built against the [Firewalla MSP API v2](https://docs.firewalla.net/) for Home Assistant 2024.1+.

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
| Firewall rule active/paused sensors | ❌ Off | Options → Rule Sensors |
| Per-flow traffic sensors | ❌ Off | Options → Flow Sensors |
| Automatic stale device cleanup | ✅ 30 days | Options → Stale Device Removal |

---

## Installation

### HACS (Recommended)

1. Open HACS → Integrations → three-dot menu → **Custom repositories**
2. Add `https://github.com/shanelord01/hass-firewalla-ng` as type **Integration**
3. Search for **Firewalla** and install
4. Restart Home Assistant

### Manual

1. Copy the `custom_components/firewalla` folder into your HA `custom_components` directory
2. Restart Home Assistant

---

## Configuration

1. **Settings → Devices & Services → Add Integration → Firewalla**
2. Enter your **MSP Subdomain** — the part before `.firewalla.net`
   *(e.g. `mycompany` for `mycompany.firewalla.net`)*
3. Enter your **API Token** — create one under **Account Settings → Create New Token** in the MSP portal
4. Adjust optional features as needed

---

## Options

All options are configurable post-install via **Settings → Devices & Services → Firewalla → Configure**:

| Option | Description |
|---|---|
| Poll Interval | How often to query the API (min 30s, recommended 300s) |
| Enable Alarm Sensors | Alarm count sensor + per-alarm binary sensors |
| Enable Rule Sensors | Active/paused binary sensor per firewall rule |
| Enable Flow Sensors | Per-flow transfer sensor (can create many entities) |
| Enable Bandwidth Sensors | Download/upload totals per device |
| Enable Device Tracker | Presence detection via ScannerEntity |
| Stale Device Removal | Days before absent devices are removed (1–365) |

---

## Stale Device Cleanup

Devices not seen via the API for the configured number of days (default 30)
are automatically removed from the Home Assistant device registry.

**Protected devices** — those referenced by automations, scenes, or scripts —
are never removed automatically. HA enforces this via the standard
`async_remove_config_entry_device` hook. You can still manually delete them via
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
```

## v2.x.x - Full rewrite for Home Assistant 2024.1+ / 2026.2+

### What's New v2.1.7
- Fix orphaned entities for Bandwidth and Device Tracker when disabled

### What's New v2.1.6
- Fix orphaned entities showing as Unavailable when features are disabled

### What's New v2.1.5
- Fix circular import preventing the integration from appearing in Settings → Devices & Services → Add Integration after installing v2.1.4.

### What's New v2.1.4
- Fix box device names duplicating "Firewalla" when the box name returned by the API already contains it (e.g. "Firewalla name Firewalla" → "name Firewalla")
- Fix rule sensors all displaying as "Active" with no context — now shows the rule action and target (e.g. "Block: youtube.com")
- Fix alarm sensors all displaying as "Active" with no context — now shows the alarm message (e.g. "Alarm: New device detected on your network")
- Fix device manufacturer still showing as "Firewalla" in some cases — aligned sensor.py to use macVendor consistent with binary_sensor.py

### What's New v2.1.3
- Fix device manufacturer displaying as "Firewalla" for all network devices — now correctly shows the hardware vendor (e.g. "Apple, Inc.", "Samsung", "Espressif") sourced from the macVendor field returned by the Firewalla MSP API

### What's New v2.1.2
- Fix alarm count sensor unique_id collision when multiple MSP accounts are configured
- Fix coordinator API failure detection to correctly catch empty responses (not just None)

### What's New v2.1.1
- Multi-box support — accounts with multiple Firewalla units can now select which boxes to monitor during setup, or change the selection later via Options
- Network devices now nest under their parent Firewalla box in the HA device hierarchy
- Fixed manufacturer showing as "Unknown" for device sensors (now correctly shows vendor name or "Firewalla")

### What's New v2.0.4
- Fix manual device deletion — offline devices can now be removed via the UI
- Fix entity display names — resolved random and Unavailable labels by correcting translations file path

### What's New v2.0.3
- Update HACS Description
- Updated logos as per Home Assistant Brand Standards to deploy in HA 2026.3
- Show "Firewalla" as the manufacturer rather than "Unknown" for devices

### Breaking Changes
- Requires Home Assistant 2024.1 or later
- Remove and re-add the integration after upgrading

### What's New v2.0.0
- Dedicated `FirewallaCoordinator` class with proper stale device cleanup
- Automatic removal of devices absent for configurable number of days (default 30)
- Devices used in automations are never removed automatically
- `async_remove_config_entry_device` support — manually delete devices from the UI
- MAC address cached at entity creation so automations survive device going offline
- Full `icons.json` support for contextual entity icons
- `strings.json` / `translations/en.json` rewritten with options flow labels and entity translations
- Config and options flow validation with min/max range on poll interval
- All entities use `_attr_has_entity_name = True` and `_attr_translation_key` (HA 2024+ naming)
- API client correctly handles both bare list and envelope response formats from MSP API
- Graceful fallback to cached data on API failure
