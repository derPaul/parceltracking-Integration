"""Constants for the Parcel Tracking integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "parceltracking"

CONF_CARRIER: Final = "carrier"
CONF_PACKAGES: Final = "packages"
CONF_TRACKING_NUMBER: Final = "tracking_number"
CONF_NAME: Final = "name"
CONF_API_KEY: Final = "api_key"
CONF_CLIENT_ID: Final = "client_id"
CONF_CLIENT_SECRET: Final = "client_secret"
CONF_REFRESH_TOKEN: Final = "refresh_token"
CONF_REGION: Final = "region"
CONF_BUSINESS_ID: Final = "business_id"
CONF_CARRIER_ID: Final = "carrier_id"
CONF_SCAN_INTERVAL: Final = "scan_interval"

DEFAULT_SCAN_INTERVAL: Final = 30  # minutes

# Mailbox entries
CONF_ENTRY_TYPE: Final = "entry_type"
ENTRY_TYPE_MAILBOX: Final = "mailbox"
CONF_FOLDERS: Final = "folders"
CONF_PROVIDER: Final = "provider"
CONF_CARRIERS: Final = "carriers"
CONF_LOOKBACK_DAYS: Final = "lookback_days"
CONF_KEEP_DELIVERED_DAYS: Final = "keep_delivered_days"
DEFAULT_IMAP_PORT: Final = 993
DEFAULT_FOLDERS: Final = "INBOX"
DEFAULT_MAIL_SCAN_INTERVAL: Final = 15  # minutes
DEFAULT_LOOKBACK_DAYS: Final = 14
DEFAULT_KEEP_DELIVERED_DAYS: Final = 3
# Carriers a mailbox can watch; "amazon" covers all Amazon order mails.
MAIL_CARRIERS: Final = ["amazon", "dhl", "dpd", "gls", "hermes", "ups"]
# Carriers whose live status needs no credentials.
KEYLESS_CARRIERS: Final = ("dpd", "hermes", "amazon_logistics")

CARRIER_NAMES: Final = {
    "amazon_logistics": "Amazon",
    "amazon": "Amazon Shipping (Verkäufer)",
    "dhl": "DHL",
    "dpd": "DPD",
    "gls": "GLS",
    "hermes": "Hermes",
    "ups": "UPS",
}

TRACKING_URLS: Final = {
    "amazon": "https://track.amazon.com/tracking/{}",
    "amazon_logistics": "https://track.amazon.com/tracking/{}",
    "dhl": "https://www.dhl.de/de/privatkunden/pakete-empfangen/verfolgen.html?piececode={}",
    "dpd": "https://tracking.dpd.de/status/de_DE/parcel/{}",
    "gls": "https://gls-group.eu/DE/de/paketverfolgung?match={}",
    "hermes": "https://www.myhermes.de/empfangen/sendungsverfolgung/sendungsinformation#{}",
    "ups": "https://www.ups.com/track?loc=de_DE&tracknum={}",
}

AMAZON_REGIONS: Final = ["eu", "na", "fe"]
AMAZON_BUSINESS_IDS: Final = [
    "AmazonShipping_UK",
    "AmazonShipping_IT",
    "AmazonShipping_ES",
    "AmazonShipping_FR",
    "AmazonShipping_US",
    "AmazonShipping_IN",
    "AmazonShipping_JP",
]
DEFAULT_AMAZON_CARRIER_ID: Final = "AMZN_UK"

SERVICE_ADD_PACKAGE: Final = "add_package"
SERVICE_ADD_PACKAGE_FROM_IMAGE: Final = "add_package_from_image"
SERVICE_DETECT_CARRIER: Final = "detect_carrier"
SERVICE_REMOVE_PACKAGE: Final = "remove_package"
SERVICE_REMOVE_DELIVERED: Final = "remove_delivered"


def normalize_tracking_number(value: str) -> str:
    """Strip whitespace and upper-case a tracking number."""
    return "".join(value.split()).upper()
