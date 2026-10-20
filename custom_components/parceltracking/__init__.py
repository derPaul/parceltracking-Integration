"""Parcel tracking for DHL, DPD, GLS, Hermes, UPS and Amazon."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import create_client
from .const import CONF_CARRIER, DOMAIN
from .coordinator import ParcelTrackingConfigEntry, ParcelTrackingCoordinator
from .services import async_setup_services

PLATFORMS = [Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration services."""
    async_setup_services(hass)
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: ParcelTrackingConfigEntry
) -> bool:
    """Set up one carrier from a config entry."""
    client = create_client(
        entry.data[CONF_CARRIER], async_get_clientsession(hass), entry.data
    )
    coordinator = ParcelTrackingCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    _async_remove_stale_entities(hass, entry, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: ParcelTrackingConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(
    hass: HomeAssistant, entry: ParcelTrackingConfigEntry
) -> None:
    """Reload when packages or options change."""
    await hass.config_entries.async_reload(entry.entry_id)


def _async_remove_stale_entities(
    hass: HomeAssistant,
    entry: ParcelTrackingConfigEntry,
    coordinator: ParcelTrackingCoordinator,
) -> None:
    """Remove sensors of packages that are no longer tracked."""
    registry = er.async_get(hass)
    prefix = f"{entry.entry_id}_parcel_"
    wanted = {f"{prefix}{p['tracking_number']}" for p in coordinator.packages}
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.unique_id.startswith(prefix) and entity.unique_id not in wanted:
            registry.async_remove(entity.entity_id)
