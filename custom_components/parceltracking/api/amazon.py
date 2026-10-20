"""Amazon via the Amazon Shipping API v2 (Selling Partner API).

Only shipments sent with Amazon Shipping can be tracked; regular Amazon
orders have no public tracking API.
"""

from __future__ import annotations

import re
import time
from typing import Any

from .base import CarrierClient
from .errors import CarrierApiError, CarrierAuthError, CarrierNotFoundError
from .models import (
    ParcelStatus,
    TrackingEvent,
    TrackingResult,
    join_location,
    parse_datetime,
)

TOKEN_URL = "https://api.amazon.com/auth/o2/token"
ENDPOINTS = {
    "eu": "https://sellingpartnerapi-eu.amazon.com",
    "na": "https://sellingpartnerapi-na.amazon.com",
    "fe": "https://sellingpartnerapi-fe.amazon.com",
}

STATUS_MAP = {
    "PreTransit": ParcelStatus.PRE_TRANSIT,
    "ReadyForReceive": ParcelStatus.PRE_TRANSIT,
    "PickupDone": ParcelStatus.IN_TRANSIT,
    "InTransit": ParcelStatus.IN_TRANSIT,
    "Departed": ParcelStatus.IN_TRANSIT,
    "ArrivedAtCarrierFacility": ParcelStatus.IN_TRANSIT,
    "OutForDelivery": ParcelStatus.OUT_FOR_DELIVERY,
    "AvailableForPickup": ParcelStatus.READY_FOR_PICKUP,
    "AwaitingCustomerPickup": ParcelStatus.READY_FOR_PICKUP,
    "Delivered": ParcelStatus.DELIVERED,
    "DeliveryAttempted": ParcelStatus.EXCEPTION,
    "Undeliverable": ParcelStatus.EXCEPTION,
    "Lost": ParcelStatus.EXCEPTION,
    "Rejected": ParcelStatus.EXCEPTION,
    "PickupCancelled": ParcelStatus.EXCEPTION,
    "ReturnInitiated": ParcelStatus.RETURNED,
    "Returned": ParcelStatus.RETURNED,
    "ReturnInTransit": ParcelStatus.RETURNED,
}


class AmazonClient(CarrierClient):
    """Amazon Shipping tracking client using Login with Amazon tokens."""

    _token: str | None = None
    _token_expires: float = 0

    async def _async_get_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires:
            return self._token
        data = await self._request(
            "POST",
            TOKEN_URL,
            auth_request=True,
            data={
                "grant_type": "refresh_token",
                "refresh_token": self._credentials["refresh_token"],
                "client_id": self._credentials["client_id"],
                "client_secret": self._credentials["client_secret"],
            },
        )
        if not data or "access_token" not in data:
            raise CarrierAuthError("Login with Amazon returned no access token")
        self._token = data["access_token"]
        self._token_expires = time.monotonic() + int(data.get("expires_in", 3600)) - 60
        return self._token

    async def async_validate_credentials(self) -> None:
        """Fetching a token verifies client id, secret and refresh token."""
        self._token = None
        await self._async_get_token()

    async def async_track(self, tracking_number: str) -> TrackingResult:
        """Return the current tracking state of a parcel."""
        token = await self._async_get_token()
        base = ENDPOINTS[self._credentials.get("region", "eu")]
        try:
            data = await self._request(
                "GET",
                f"{base}/shipping/v2/tracking",
                headers={
                    "x-amz-access-token": token,
                    "x-amzn-shipping-business-id": self._credentials["business_id"],
                    "Accept": "application/json",
                },
                params={
                    "trackingId": tracking_number,
                    "carrierId": self._credentials["carrier_id"],
                },
            )
        except CarrierApiError as err:
            if err.status == 400:
                raise CarrierNotFoundError(tracking_number) from err
            raise
        return parse_amazon(data, tracking_number)


EVENT_TEXTS = {
    "PreTransit": "Sendung angekündigt",
    "ReadyForReceive": "Sendung angekündigt",
    "PickupDone": "Sendung abgeholt",
    "InTransit": "Unterwegs",
    "Departed": "Hat das Versandzentrum verlassen",
    "ArrivedAtCarrierFacility": "Im Versandzentrum angekommen",
    "OutForDelivery": "In Zustellung",
    "AvailableForPickup": "Abholbereit",
    "AwaitingCustomerPickup": "Abholbereit",
    "Delivered": "Zugestellt",
    "DeliveryAttempted": "Zustellversuch",
    "Undeliverable": "Nicht zustellbar",
    "Lost": "Verloren",
    "Rejected": "Annahme verweigert",
    "PickupCancelled": "Abholung storniert",
    "ReturnInitiated": "Rücksendung eingeleitet",
    "Returned": "Zurückgesendet",
    "ReturnInTransit": "Rücksendung unterwegs",
}


def describe(code: str | None) -> str:
    """Return a German text for an Amazon event code.

    Unknown codes are split into words: 'SomeNewCode' -> 'Some new code'.
    """
    if not code:
        return ""
    return EVENT_TEXTS.get(code) or re.sub(r"(?<!^)(?=[A-Z])", " ", code).capitalize()


def parse_amazon(data: Any, tracking_number: str) -> TrackingResult:
    """Convert an Amazon Shipping getTracking response."""
    payload = (data or {}).get("payload")
    if not payload:
        return TrackingResult.not_found(tracking_number)

    events = []
    for item in payload.get("eventHistory") or []:
        loc = item.get("location") or {}
        events.append(
            TrackingEvent(
                timestamp=parse_datetime(item.get("eventTime")),
                description=describe(item.get("eventCode")),
                location=join_location(loc.get("city"), loc.get("countryCode")),
            )
        )

    code = (payload.get("summary") or {}).get("status")
    if not code and payload.get("eventHistory"):
        code = payload["eventHistory"][-1].get("eventCode")

    return TrackingResult.from_events(
        tracking_number,
        STATUS_MAP.get(code or "", ParcelStatus.UNKNOWN),
        events,
        status_text=describe(code) or None,
        estimated_delivery=parse_datetime(payload.get("promisedDeliveryDate")),
    )
