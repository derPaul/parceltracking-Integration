"""Exceptions raised by the carrier clients."""

from __future__ import annotations


class CarrierError(Exception):
    """Base class for all carrier errors."""


class CarrierConnectionError(CarrierError):
    """The carrier API could not be reached."""


class CarrierAuthError(CarrierError):
    """The configured credentials were rejected."""


class CarrierRateLimitError(CarrierError):
    """The carrier API rate limit was exceeded."""


class CarrierNotFoundError(CarrierError):
    """The carrier does not know the tracking number (yet)."""


class CarrierApiError(CarrierError):
    """The carrier API returned an unexpected response."""

    def __init__(self, message: str, status: int | None = None) -> None:
        """Store the HTTP status alongside the message."""
        super().__init__(message)
        self.status = status
