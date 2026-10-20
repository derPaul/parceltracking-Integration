"""Amazon's own deliveries (TBA numbers) via the tracker behind track.amazon.de.

Amazon offers no API with personal tokens for regular orders. The public
tracking page works without login, so this endpoint is used without
credentials. It is not officially documented and may change.
"""

from __future__ import annotations

import json
from typing import Any

from .amazon import STATUS_MAP, describe
from .base import CarrierClient
from .models import (
    ParcelStatus,
    TrackingEvent,
    TrackingResult,
    join_location,
    parse_datetime,
)

TRACK_URL = "https://track.amazon.de/api/tracker/{}"


class AmazonLogisticsClient(CarrierClient):
    """Amazon Logistics tracking client."""

    async def async_validate_credentials(self) -> None:
        """No credentials needed."""

    async def async_track(self, tracking_number: str) -> TrackingResult:
        """Return the current tracking state of a parcel."""
        data = await self._request(
            "GET",
            TRACK_URL.format(tracking_number),
            headers={"Accept": "application/json"},
        )
        return parse_amazon_logistics(data, tracking_number)


def _load(value: Any) -> Any:
    """The tracker embeds some parts as JSON encoded strings."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def parse_amazon_logistics(data: Any, tracking_number: str) -> TrackingResult:
    """Convert a track.amazon.de tracker response."""
    if not isinstance(data, dict):
        return TrackingResult.not_found(tracking_number)

    history = _load(data.get("eventHistory"))
    if isinstance(history, dict):
        history = history.get("eventHistory")
    raw_events = [e for e in history or [] if isinstance(e, dict)]

    progress = _load(data.get("progressTracker"))
    summary = (progress.get("summary") if isinstance(progress, dict) else None) or {}

    events = []
    for item in raw_events:
        loc = item.get("location") or {}
        events.append(
            TrackingEvent(
                timestamp=parse_datetime(item.get("eventTime")),
                description=describe(item.get("eventCode")),
                location=join_location(loc.get("city"), loc.get("countryCode")),
            )
        )

    code = summary.get("status")
    if not code and raw_events:
        newest = max(raw_events, key=lambda e: str(e.get("eventTime") or ""))
        code = newest.get("eventCode")
    if not code and not events:
        return TrackingResult.not_found(tracking_number)

    eta = ((summary.get("metadata") or {}).get("expectedDeliveryDate") or {}).get(
        "stringValue"
    )
    return TrackingResult.from_events(
        tracking_number,
        STATUS_MAP.get(code or "", ParcelStatus.UNKNOWN),
        events,
        status_text=describe(code) or None,
        estimated_delivery=parse_datetime(eta),
    )
