"""Hermes Germany via the track & trace endpoint used by myhermes.de.

Hermes only hands out official API credentials (HSI) to business customers.
The consumer endpoint works without a key; if you have one it is sent as
x-api-key.
"""

from __future__ import annotations

from typing import Any

from .base import CarrierClient
from .models import (
    ParcelStatus,
    TrackingEvent,
    TrackingResult,
    join_location,
    parse_datetime,
)

TRACK_URL = "https://api.my-deliveries.de/tnt/parcelservice/parceldetails/{}"

# Matched as substrings against the upper-cased Hermes status code.
STATUS_HINTS = (
    ("RETOUR", ParcelStatus.RETURNED),
    ("RETURN", ParcelStatus.RETURNED),
    ("DELIVERED", ParcelStatus.DELIVERED),
    ("ZUGESTELLT", ParcelStatus.DELIVERED),
    ("OUT_FOR_DELIVERY", ParcelStatus.OUT_FOR_DELIVERY),
    ("IN_DELIVERY", ParcelStatus.OUT_FOR_DELIVERY),
    ("PARCELSHOP", ParcelStatus.READY_FOR_PICKUP),
    ("PICKUP", ParcelStatus.READY_FOR_PICKUP),
    ("ANNOUNCED", ParcelStatus.PRE_TRANSIT),
    ("NOTIFIED", ParcelStatus.PRE_TRANSIT),
    ("ANGEKUENDIGT", ParcelStatus.PRE_TRANSIT),
)


class HermesClient(CarrierClient):
    """Hermes tracking client."""

    async def async_validate_credentials(self) -> None:
        """Only validate when a key was entered."""
        if self._credentials.get("api_key"):
            await super().async_validate_credentials()

    async def async_track(self, tracking_number: str) -> TrackingResult:
        """Return the current tracking state of a parcel."""
        headers = {"Accept": "application/json"}
        if api_key := self._credentials.get("api_key"):
            headers["x-api-key"] = api_key
        data = await self._request(
            "GET", TRACK_URL.format(tracking_number), headers=headers
        )
        return parse_hermes(data, tracking_number)


def _first(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if item.get(key):
            return item[key]
    return None


def parse_hermes(data: Any, tracking_number: str) -> TrackingResult:
    """Convert a Hermes parcel details response."""
    if not isinstance(data, dict) or not data:
        return TrackingResult.not_found(tracking_number)

    events = [
        TrackingEvent(
            timestamp=parse_datetime(_first(item, "timestamp", "dateTime", "date")),
            description=_first(item, "statusHistoryText", "description", "statusText")
            or "",
            location=join_location(_first(item, "city", "location")),
        )
        for item in _first(data, "parcelHistory", "history", "events") or []
    ]

    raw_status = data.get("status")
    code = ""
    status_text = None
    if isinstance(raw_status, dict):
        code = str(_first(raw_status, "parcelStatus", "code", "status") or "")
        text = raw_status.get("text")
        status_text = (
            _first(text, "longText", "shortText") if isinstance(text, dict) else text
        )
    elif isinstance(raw_status, str):
        code = raw_status

    status = ParcelStatus.IN_TRANSIT if events else ParcelStatus.UNKNOWN
    upper = code.upper()
    for hint, mapped in STATUS_HINTS:
        if hint in upper:
            status = mapped
            break

    if status is ParcelStatus.UNKNOWN and not events and not status_text:
        return TrackingResult.not_found(tracking_number)

    eta = _first(data, "expectedDelivery", "deliveryForecast", "estimatedDelivery")
    if isinstance(eta, dict):
        eta = _first(eta, "date", "from", "dateFrom", "timeFrom")

    return TrackingResult.from_events(
        tracking_number,
        status,
        events,
        status_text=status_text,
        estimated_delivery=parse_datetime(eta),
    )
