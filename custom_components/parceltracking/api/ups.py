"""UPS via the official Tracking API (developer.ups.com, OAuth client credentials)."""

from __future__ import annotations

import time
import uuid
from typing import Any

import aiohttp

from .base import CarrierClient
from .errors import CarrierAuthError
from .models import (
    ParcelStatus,
    TrackingEvent,
    TrackingResult,
    join_location,
    parse_datetime,
)

TOKEN_URL = "https://onlinetools.ups.com/security/v1/oauth/token"
TRACK_URL = "https://onlinetools.ups.com/api/track/v1/details/{}"

# UPS activity status types.
STATUS_MAP = {
    "M": ParcelStatus.PRE_TRANSIT,
    "MV": ParcelStatus.PRE_TRANSIT,
    "P": ParcelStatus.IN_TRANSIT,
    "I": ParcelStatus.IN_TRANSIT,
    "O": ParcelStatus.OUT_FOR_DELIVERY,
    "D": ParcelStatus.DELIVERED,
    "DO": ParcelStatus.DELIVERED,
    "DD": ParcelStatus.DELIVERED,
    "X": ParcelStatus.EXCEPTION,
    "RS": ParcelStatus.RETURNED,
}
ETA_TYPES = ("SDD", "RDD", "CMT")


class UpsClient(CarrierClient):
    """UPS tracking client."""

    _token: str | None = None
    _token_expires: float = 0

    async def _async_get_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires:
            return self._token
        data = await self._request(
            "POST",
            TOKEN_URL,
            auth_request=True,
            data={"grant_type": "client_credentials"},
            auth=aiohttp.BasicAuth(
                self._credentials["client_id"], self._credentials["client_secret"]
            ),
        )
        if not data or "access_token" not in data:
            raise CarrierAuthError("UPS returned no access token")
        self._token = data["access_token"]
        self._token_expires = time.monotonic() + int(data.get("expires_in", 3600)) - 60
        return self._token

    async def async_validate_credentials(self) -> None:
        """Fetching a token is enough to verify client id and secret."""
        self._token = None
        await self._async_get_token()

    async def async_track(self, tracking_number: str) -> TrackingResult:
        """Return the current tracking state of a parcel."""
        try:
            data = await self._async_fetch(tracking_number)
        except CarrierAuthError:
            # The token may have been revoked early; retry once with a fresh one.
            self._token = None
            data = await self._async_fetch(tracking_number)
        return parse_ups(data, tracking_number)

    async def _async_fetch(self, tracking_number: str) -> Any:
        token = await self._async_get_token()
        return await self._request(
            "GET",
            TRACK_URL.format(tracking_number),
            headers={
                "Authorization": f"Bearer {token}",
                "transId": uuid.uuid4().hex,
                "transactionSrc": "homeassistant",
            },
            params={"locale": "de_DE", "returnSignature": "false"},
        )


def _datetime(date_str: str | None, time_str: str | None) -> Any:
    if not date_str:
        return None
    return parse_datetime(f"{date_str}{time_str or '000000'}", "%Y%m%d%H%M%S")


def _event(activity: dict[str, Any]) -> TrackingEvent:
    address = (activity.get("location") or {}).get("address") or {}
    return TrackingEvent(
        timestamp=_datetime(activity.get("date"), activity.get("time")),
        description=(activity.get("status") or {}).get("description", "").strip(),
        location=join_location(address.get("city"), address.get("countryCode")),
    )


def parse_ups(data: Any, tracking_number: str) -> TrackingResult:
    """Convert a UPS API response."""
    shipments = ((data or {}).get("trackResponse") or {}).get("shipment") or []
    packages = (shipments[0].get("package") if shipments else None) or []
    if not packages:
        return TrackingResult.not_found(tracking_number)
    package = packages[0]
    activities = package.get("activity") or []
    events = [_event(a) for a in activities]

    status_type = (
        (activities[0].get("status") or {}).get("type") if activities else None
    )
    status = STATUS_MAP.get(status_type or "", ParcelStatus.UNKNOWN)

    eta = None
    for entry in package.get("deliveryDate") or []:
        if entry.get("type") in ETA_TYPES:
            end_time = (package.get("deliveryTime") or {}).get("endTime")
            eta = _datetime(entry.get("date"), end_time)
            break

    current = package.get("currentStatus") or {}
    return TrackingResult.from_events(
        tracking_number,
        status,
        events,
        status_text=current.get("description") or None,
        estimated_delivery=eta,
    )
