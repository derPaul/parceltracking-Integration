"""DPD via the parcel life cycle endpoint used by tracking.dpd.de.

DPD offers no public tracking API with personal tokens for consumers, so
this endpoint is used without credentials.
"""

from __future__ import annotations

from typing import Any

from .base import CarrierClient
from .models import ParcelStatus, TrackingEvent, TrackingResult, parse_datetime

TRACK_URL = "https://tracking.dpd.de/rest/plc/de_DE/{}"

STATUS_MAP = {
    "ACCEPTED": ParcelStatus.PRE_TRANSIT,
    "AT_SENDING_DEPOT": ParcelStatus.IN_TRANSIT,
    "ON_THE_ROAD": ParcelStatus.IN_TRANSIT,
    "AT_DELIVERY_DEPOT": ParcelStatus.IN_TRANSIT,
    "OUT_FOR_DELIVERY": ParcelStatus.OUT_FOR_DELIVERY,
    "PICKUP": ParcelStatus.READY_FOR_PICKUP,
    "DELIVERED": ParcelStatus.DELIVERED,
    "RETURNED": ParcelStatus.RETURNED,
}


class DpdClient(CarrierClient):
    """DPD tracking client."""

    async def async_validate_credentials(self) -> None:
        """No credentials needed."""

    async def async_track(self, tracking_number: str) -> TrackingResult:
        """Return the current tracking state of a parcel."""
        data = await self._request(
            "GET",
            TRACK_URL.format(tracking_number),
            headers={"Accept": "application/json"},
        )
        return parse_dpd(data, tracking_number)


def _content(value: Any) -> str:
    content = (value or {}).get("content") or []
    return " ".join(str(c).strip() for c in content if c).strip()


def parse_dpd(data: Any, tracking_number: str) -> TrackingResult:
    """Convert a DPD parcel life cycle response."""
    lifecycle = ((data or {}).get("parcellifecycleResponse") or {}).get(
        "parcelLifeCycleData"
    )
    if not lifecycle:
        return TrackingResult.not_found(tracking_number)

    events = [
        TrackingEvent(
            timestamp=parse_datetime(scan.get("date")),
            description=_content(scan.get("scanDescription")),
            location=(scan.get("scanData") or {}).get("location"),
        )
        for scan in (lifecycle.get("scanInfo") or {}).get("scan") or []
    ]

    status = ParcelStatus.UNKNOWN
    status_text = None
    for info in lifecycle.get("statusInfo") or []:
        if info.get("isCurrentStatus"):
            status = STATUS_MAP.get(str(info.get("status")), ParcelStatus.IN_TRANSIT)
            status_text = info.get("label") or _content(info.get("description")) or None
            break

    if status is ParcelStatus.UNKNOWN and not events:
        return TrackingResult.not_found(tracking_number)

    return TrackingResult.from_events(
        tracking_number, status, events, status_text=status_text
    )
