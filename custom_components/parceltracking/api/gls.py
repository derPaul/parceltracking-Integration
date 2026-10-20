"""GLS via the official Track & Trace API (API key from gls-group.eu)."""

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

TRACK_URL = "https://api-tt.de.gls-group.com/app/service/open/rest/DE/de/rstt001"

STATUS_MAP = {
    "PREADVICE": ParcelStatus.PRE_TRANSIT,
    "INTRANSIT": ParcelStatus.IN_TRANSIT,
    "INWAREHOUSE": ParcelStatus.IN_TRANSIT,
    "INDELIVERY": ParcelStatus.OUT_FOR_DELIVERY,
    "CANPICKUP": ParcelStatus.READY_FOR_PICKUP,
    "DELIVEREDPS": ParcelStatus.READY_FOR_PICKUP,
    "DELIVERED": ParcelStatus.DELIVERED,
    "NOTDELIVERED": ParcelStatus.EXCEPTION,
    "FINAL": ParcelStatus.DELIVERED,
}


class GlsClient(CarrierClient):
    """GLS tracking client, authenticated with an x-api-key."""

    async def async_track(self, tracking_number: str) -> TrackingResult:
        """Return the current tracking state of a parcel."""
        data = await self._request(
            "GET",
            TRACK_URL,
            headers={
                "x-api-key": self._credentials["api_key"],
                "Accept": "application/json",
            },
            params={"match": tracking_number},
        )
        return parse_gls(data, tracking_number)


def parse_gls(data: Any, tracking_number: str) -> TrackingResult:
    """Convert a GLS rstt001 response."""
    units = (data or {}).get("tuStatus") or []
    if not units:
        return TrackingResult.not_found(tracking_number)
    unit = units[0]

    events = []
    for item in unit.get("history") or []:
        address = item.get("address") or {}
        stamp = f"{item.get('date', '')}T{item.get('time') or '00:00:00'}"
        events.append(
            TrackingEvent(
                timestamp=parse_datetime(stamp),
                description=item.get("evtDscr") or "",
                location=join_location(address.get("city"), address.get("countryName")),
            )
        )

    progress = unit.get("progressBar") or {}
    status = STATUS_MAP.get(
        str(progress.get("statusInfo", "")).upper(), ParcelStatus.UNKNOWN
    )
    if progress.get("retourFlag"):
        status = ParcelStatus.RETURNED

    return TrackingResult.from_events(
        tracking_number, status, events, status_text=progress.get("statusText")
    )
