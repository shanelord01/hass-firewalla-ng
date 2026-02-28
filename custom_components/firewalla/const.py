"""Constants for the Firewalla integration."""
from typing import Final

DOMAIN: Final = "firewalla"
BRAND: Final = "Firewalla"
PLATFORMS: Final = ["sensor", "binary_sensor", "switch", "device_tracker"]

# Configuration keys
CONF_API_TOKEN: Final = "api_token"
CONF_SUBDOMAIN: Final = "subdomain"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_ENABLE_ALARMS: Final = "enable_alarms"
CONF_ENABLE_RULES: Final = "enable_rules"
CONF_ENABLE_FLOWS: Final = "enable_flows"
CONF_ENABLE_TRAFFIC: Final = "enable_traffic"
CONF_ENABLE_TARGET_LISTS: Final = "enable_target_lists"
CONF_TRACK_DEVICES: Final = "track_devices"
CONF_STALE_DAYS: Final = "stale_days"
CONF_BOX_FILTER: Final = "box_filter"

# Defaults
DEFAULT_SUBDOMAIN: Final = "api"
DEFAULT_API_URL: Final = "https://api.firewalla.net/v2"
DEFAULT_SCAN_INTERVAL: Final = 300  # 5 minutes
DEFAULT_STALE_DAYS: Final = 30      # days before a device is considered stale
DEFAULT_TIMEOUT: Final = 30

# Internal storage keys
COORDINATOR: Final = "coordinator"
API_CLIENT: Final = "client"

# Staleness tracking
STORAGE_KEY: Final = f"{DOMAIN}.device_seen"
STORAGE_VERSION: Final = 1

# Service names
SERVICE_DELETE_ALARM: Final = "delete_alarm"
SERVICE_RENAME_DEVICE: Final = "rename_device"
SERVICE_SEARCH_ALARMS: Final = "search_alarms"
SERVICE_SEARCH_FLOWS: Final = "search_flows"

# Entity attributes
ATTR_DEVICE_ID: Final = "device_id"
ATTR_DEVICE_NAME: Final = "device_name"
ATTR_NETWORK_ID: Final = "network_id"
ATTR_LAST_SEEN: Final = "last_seen"
ATTR_IP_ADDRESS: Final = "ip_address"
ATTR_MAC_ADDRESS: Final = "mac_address"
ATTR_ONLINE: Final = "online"
ATTR_BLOCKED: Final = "blocked"
ATTR_UPLOAD: Final = "upload"
ATTR_DOWNLOAD: Final = "download"
ATTR_BLOCKED_COUNT: Final = "blocked_count"
ATTR_ALARM_ID: Final = "alarm_id"
ATTR_RULE_ID: Final = "rule_id"
