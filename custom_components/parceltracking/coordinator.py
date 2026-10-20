"""Data update coordinator for the Parcel Tracking integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    CarrierAuthError,
    CarrierClient,
    CarrierError,
    CarrierNotFoundError,
    ParcelStatus,
    TrackingResult,
)
from .const import CONF_PACKAGES, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

type ParcelTrackingConfigEntry = ConfigEntry[ParcelTrackingCoordinator]


class ParcelTrackingCoordinator(DataUpdateCoordinator[dict[str, TrackingResult]]):
    """Polls all parcels of one carrier."""

    config_entry: ParcelTrackingConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ParcelTrackingConfigEntry,
        client: CarrierClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{entry.title}",
            update_interval=timedelta(
                minutes=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.client = client

    @property
    def packages(self) -> list[dict[str, Any]]:
        """Return the configured packages."""
        return self.config_entry.options.get(CONF_PACKAGES, [])

    async def _async_update_data(self) -> dict[str, TrackingResult]:
        """Fetch the state of every package."""
        previous = self.data or {}
        results: dict[str, TrackingResult] = {}
        errors: list[str] = []

        for package in self.packages:
            number = package["tracking_number"]
            # Delivered parcels don't change anymore; save API quota.
            if (old := previous.get(number)) and old.status is ParcelStatus.DELIVERED:
                results[number] = old
                continue
            try:
                results[number] = await self.client.async_track(number)
            except CarrierNotFoundError:
                results[number] = TrackingResult.not_found(number)
            except CarrierAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except CarrierError as err:
                _LOGGER.debug("Updating %s failed: %s", number, err)
                errors.append(f"{number}: {err}")
                if old:
                    results[number] = old

        if errors and not results:
            raise UpdateFailed("; ".join(errors))
        return results
