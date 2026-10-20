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

CARRIER_NAMES: Final = {
    "amazon": "Amazon Shipping",
    "dhl": "DHL",
    "dpd": "DPD",
    "gls": "GLS",
    "hermes": "Hermes",
    "ups": "UPS",
}

TRACKING_URLS: Final = {
    "amazon": "https://track.amazon.de/tracking/{}",
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
SERVICE_REMOVE_PACKAGE: Final = "remove_package"
SERVICE_REMOVE_DELIVERED: Final = "remove_delivered"


def normalize_tracking_number(value: str) -> str:
    """Strip whitespace and upper-case a tracking number."""
    return "".join(value.split()).upper()
