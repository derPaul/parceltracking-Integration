"""Base class for carrier clients."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

import aiohttp

from .errors import (
    CarrierApiError,
    CarrierAuthError,
    CarrierConnectionError,
    CarrierNotFoundError,
    CarrierRateLimitError,
)
from .models import TrackingResult

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)

# A syntactically plausible number no carrier should know. Used to check
# credentials: "not found" means the key was accepted.
VALIDATION_TRACKING_NUMBER = "00000000000000000000"


class CarrierClient(ABC):
    """Talks to the tracking API of a single carrier."""

    def __init__(
        self, session: aiohttp.ClientSession, credentials: Mapping[str, Any]
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._credentials = credentials

    @abstractmethod
    async def async_track(self, tracking_number: str) -> TrackingResult:
        """Return the current tracking state of a parcel."""

    async def async_validate_credentials(self) -> None:
        """Raise CarrierAuthError if the credentials are rejected."""
        try:
            await self.async_track(VALIDATION_TRACKING_NUMBER)
        except CarrierNotFoundError:
            pass
        except CarrierApiError as err:
            # Many APIs answer an unknown number with 400; that still proves
            # the credentials were accepted.
            if err.status != 400:
                raise

    async def _request(
        self,
        method: str,
        url: str,
        *,
        auth_request: bool = False,
        **kwargs: Any,
    ) -> Any:
        """Perform a request and map HTTP errors to carrier errors.

        With auth_request=True (token endpoints) a 400 is treated as rejected
        credentials as well, since OAuth servers answer invalid_grant with 400.
        """
        try:
            async with self._session.request(
                method, url, timeout=REQUEST_TIMEOUT, **kwargs
            ) as resp:
                if resp.status in (401, 403) or (auth_request and resp.status == 400):
                    raise CarrierAuthError(f"Credentials rejected (HTTP {resp.status})")
                if resp.status == 404:
                    raise CarrierNotFoundError(url)
                if resp.status == 429:
                    raise CarrierRateLimitError("Rate limit exceeded")
                if resp.status >= 400:
                    body = await resp.text()
                    raise CarrierApiError(
                        f"HTTP {resp.status}: {body[:300]}", status=resp.status
                    )
                if resp.status == 204:
                    return None
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CarrierConnectionError(str(err) or type(err).__name__) from err
        except ValueError as err:
            raise CarrierApiError(f"Invalid JSON response: {err}") from err
