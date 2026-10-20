"""Services to manage tracked packages from automations."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .api import ParcelStatus
from .const import (
    CARRIER_NAMES,
    CONF_CARRIER,
    CONF_NAME,
    CONF_PACKAGES,
    CONF_TRACKING_NUMBER,
    DOMAIN,
    SERVICE_ADD_PACKAGE,
    SERVICE_REMOVE_DELIVERED,
    SERVICE_REMOVE_PACKAGE,
    normalize_tracking_number,
)
from .coordinator import ParcelTrackingConfigEntry

ADD_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CARRIER): vol.In(list(CARRIER_NAMES)),
        vol.Required(CONF_TRACKING_NUMBER): cv.string,
        vol.Optional(CONF_NAME): cv.string,
    }
)
REMOVE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TRACKING_NUMBER): cv.string,
        vol.Optional(CONF_CARRIER): vol.In(list(CARRIER_NAMES)),
    }
)
REMOVE_DELIVERED_SCHEMA = vol.Schema(
    {vol.Optional(CONF_CARRIER): vol.In(list(CARRIER_NAMES))}
)


def _entries(
    hass: HomeAssistant, carrier: str | None = None
) -> list[ParcelTrackingConfigEntry]:
    return [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
        and (carrier is None or entry.data[CONF_CARRIER] == carrier)
    ]


def _set_packages(
    hass: HomeAssistant,
    entry: ParcelTrackingConfigEntry,
    packages: list[dict[str, Any]],
) -> None:
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_PACKAGES: packages}
    )


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the services."""

    async def add_package(call: ServiceCall) -> None:
        carrier = call.data[CONF_CARRIER]
        entries = _entries(hass, carrier)
        if not entries:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="carrier_not_configured",
                translation_placeholders={"carrier": CARRIER_NAMES[carrier]},
            )
        entry = entries[0]
        number = normalize_tracking_number(call.data[CONF_TRACKING_NUMBER])
        packages = list(entry.options.get(CONF_PACKAGES, []))
        name = call.data.get(CONF_NAME)
        for package in packages:
            if package[CONF_TRACKING_NUMBER] == number:
                if not name or package.get(CONF_NAME) == name:
                    return
                packages = [
                    {**p, CONF_NAME: name} if p is package else p for p in packages
                ]
                break
        else:
            packages.append({CONF_TRACKING_NUMBER: number, CONF_NAME: name})
        _set_packages(hass, entry, packages)

    async def remove_package(call: ServiceCall) -> None:
        number = normalize_tracking_number(call.data[CONF_TRACKING_NUMBER])
        found = False
        for entry in _entries(hass, call.data.get(CONF_CARRIER)):
            packages = entry.options.get(CONF_PACKAGES, [])
            remaining = [p for p in packages if p[CONF_TRACKING_NUMBER] != number]
            if len(remaining) != len(packages):
                found = True
                _set_packages(hass, entry, remaining)
        if not found:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="package_not_found",
                translation_placeholders={"tracking_number": number},
            )

    async def remove_delivered(call: ServiceCall) -> None:
        for entry in _entries(hass, call.data.get(CONF_CARRIER)):
            data = entry.runtime_data.data or {}
            packages = entry.options.get(CONF_PACKAGES, [])
            remaining = [
                p
                for p in packages
                if (res := data.get(p[CONF_TRACKING_NUMBER])) is None
                or res.status is not ParcelStatus.DELIVERED
            ]
            if len(remaining) != len(packages):
                _set_packages(hass, entry, remaining)

    hass.services.async_register(DOMAIN, SERVICE_ADD_PACKAGE, add_package, ADD_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_REMOVE_PACKAGE, remove_package, REMOVE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_REMOVE_DELIVERED, remove_delivered, REMOVE_DELIVERED_SCHEMA
    )
