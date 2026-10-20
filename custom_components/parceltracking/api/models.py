"""Carrier independent data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

# Most carriers report local German time without an offset.
DEFAULT_TZ = ZoneInfo("Europe/Berlin")


class ParcelStatus(StrEnum):
    """Normalized parcel status shared by all carriers."""

    NOT_FOUND = "not_found"
    PRE_TRANSIT = "pre_transit"
    IN_TRANSIT = "in_transit"
    OUT_FOR_DELIVERY = "out_for_delivery"
    READY_FOR_PICKUP = "ready_for_pickup"
    DELIVERED = "delivered"
    EXCEPTION = "exception"
    RETURNED = "returned"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class TrackingEvent:
    """A single scan / status event of a parcel."""

    timestamp: datetime | None
    description: str
    location: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the event as a JSON serializable dict."""
        return {
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "description": self.description,
            "location": self.location,
        }


@dataclass(slots=True)
class TrackingResult:
    """Current tracking state of a parcel."""

    tracking_number: str
    status: ParcelStatus
    status_text: str | None = None
    location: str | None = None
    last_update: datetime | None = None
    estimated_delivery: datetime | date | None = None
    events: list[TrackingEvent] = field(default_factory=list)

    @classmethod
    def not_found(cls, tracking_number: str) -> TrackingResult:
        """Return a result for a parcel the carrier does not know (yet)."""
        return cls(tracking_number=tracking_number, status=ParcelStatus.NOT_FOUND)

    @classmethod
    def from_events(
        cls,
        tracking_number: str,
        status: ParcelStatus,
        events: list[TrackingEvent],
        status_text: str | None = None,
        estimated_delivery: datetime | date | None = None,
    ) -> TrackingResult:
        """Build a result, deriving text/location/time from the newest event."""
        events = sort_events(events)
        latest = events[0] if events else None
        return cls(
            tracking_number=tracking_number,
            status=refine_status(
                status, status_text or (latest and latest.description)
            ),
            status_text=status_text or (latest.description if latest else None),
            location=latest.location if latest else None,
            last_update=latest.timestamp if latest else None,
            estimated_delivery=estimated_delivery,
            events=events,
        )


def sort_events(events: list[TrackingEvent]) -> list[TrackingEvent]:
    """Sort events newest first; events without timestamp go last."""
    return sorted(
        events,
        key=lambda e: e.timestamp.timestamp() if e.timestamp else float("-inf"),
        reverse=True,
    )


_OUT_FOR_DELIVERY_HINTS = (
    "out for delivery",
    "in zustellung",
    "zustellfahrzeug",
    "wird heute zugestellt",
    "ist unterwegs zu ihnen",
    "auf dem weg zu ihnen",
    "zustelltour",
)
_PICKUP_HINTS = (
    "abholbereit",
    "zur abholung",
    "kann abgeholt werden",
    "ready for pickup",
    "available for pickup",
    "in der packstation",
    "im paketshop",
)


def refine_status(status: ParcelStatus, text: str | None) -> ParcelStatus:
    """Refine coarse carrier states using the human readable status text."""
    if not text or status not in (ParcelStatus.IN_TRANSIT, ParcelStatus.UNKNOWN):
        return status
    lowered = text.lower()
    if any(hint in lowered for hint in _OUT_FOR_DELIVERY_HINTS):
        return ParcelStatus.OUT_FOR_DELIVERY
    if any(hint in lowered for hint in _PICKUP_HINTS):
        return ParcelStatus.READY_FOR_PICKUP
    return status


def parse_datetime(value: Any, fmt: str | None = None) -> datetime | None:
    """Parse a carrier timestamp; naive values are assumed to be German time."""
    if not value or not isinstance(value, str):
        return None
    value = value.strip()
    try:
        if fmt:
            parsed = datetime.strptime(value, fmt)
        else:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=DEFAULT_TZ)
    return parsed


def join_location(*parts: Any) -> str | None:
    """Join non empty location parts with a comma."""
    cleaned = [str(p).strip() for p in parts if p and str(p).strip()]
    return ", ".join(cleaned) or None
