"""DHL via the official "Shipment Tracking - Unified" API (developer.dhl.com)."""

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

TRACK_URL = "https://api-eu.dhl.com/track/shipments"

STATUS_MAP = {
    "pre-transit": ParcelStatus.PRE_TRANSIT,
    "transit": ParcelStatus.IN_TRANSIT,
    "delivered": ParcelStatus.DELIVERED,
    "failure": ParcelStatus.EXCEPTION,
    "unknown": ParcelStatus.UNKNOWN,
}


class DhlClient(CarrierClient):
    """DHL tracking client, authenticated with a DHL-API-Key."""

    async def async_track(self, tracking_number: str) -> TrackingResult:
        """Return the current tracking state of a parcel."""
        data = await self._request(
            "GET",
            TRACK_URL,
            headers={
                "DHL-API-Key": self._credentials["api_key"],
                "Accept": "application/json",
            },
            params={"trackingNumber": tracking_number, "language": "de"},
        )
        return parse_dhl(data, tracking_number)


def _location(item: dict[str, Any]) -> str | None:
    address = (item.get("location") or {}).get("address") or {}
    return join_location(address.get("addressLocality"), address.get("countryCode"))


def _event(item: dict[str, Any]) -> TrackingEvent:
    return TrackingEvent(
        timestamp=parse_datetime(item.get("timestamp")),
        description=item.get("description") or item.get("status") or "",
        location=_location(item),
    )


def parse_dhl(data: Any, tracking_number: str) -> TrackingResult:
    """Convert a DHL API response."""
    shipments = (data or {}).get("shipments") or []
    if not shipments:
        return TrackingResult.not_found(tracking_number)
    shipment = shipments[0]
    status = shipment.get("status") or {}
    events = [_event(e) for e in shipment.get("events") or []]
    if not events and status:
        events = [_event(status)]

    result = TrackingResult.from_events(
        tracking_number,
        STATUS_MAP.get(str(status.get("statusCode", "")).lower(), ParcelStatus.UNKNOWN),
        events,
        status_text=status.get("description") or status.get("status"),
        estimated_delivery=parse_datetime(shipment.get("estimatedTimeOfDelivery")),
    )
    if status.get("timestamp"):
        result.last_update = parse_datetime(status["timestamp"])
        result.location = _location(status) or result.location
    return result
