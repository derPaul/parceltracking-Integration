"""Carrier API clients. Independent of Home Assistant."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import aiohttp

from .amazon import AmazonClient
from .amazon_logistics import AmazonLogisticsClient
from .base import CarrierClient
from .dhl import DhlClient
from .dpd import DpdClient
from .errors import (
    CarrierApiError,
    CarrierAuthError,
    CarrierConnectionError,
    CarrierError,
    CarrierNotFoundError,
    CarrierRateLimitError,
)
from .gls import GlsClient
from .hermes import HermesClient
from .models import ParcelStatus, TrackingEvent, TrackingResult
from .ups import UpsClient

CLIENTS: dict[str, type[CarrierClient]] = {
    "amazon": AmazonClient,
    "amazon_logistics": AmazonLogisticsClient,
    "dhl": DhlClient,
    "dpd": DpdClient,
    "gls": GlsClient,
    "hermes": HermesClient,
    "ups": UpsClient,
}


def create_client(
    carrier: str, session: aiohttp.ClientSession, credentials: Mapping[str, Any]
) -> CarrierClient:
    """Create the client for a carrier."""
    return CLIENTS[carrier](session, credentials)


__all__ = [
    "CLIENTS",
    "CarrierApiError",
    "CarrierAuthError",
    "CarrierClient",
    "CarrierConnectionError",
    "CarrierError",
    "CarrierNotFoundError",
    "CarrierRateLimitError",
    "ParcelStatus",
    "TrackingEvent",
    "TrackingResult",
    "create_client",
]
