"""Services to manage tracked packages from automations."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .api import CarrierError, ParcelStatus
from .api.detect import detect_carriers
from .const import (
    CARRIER_NAMES,
    CONF_CARRIER,
    CONF_NAME,
    CONF_PACKAGES,
    CONF_TRACKING_NUMBER,
    DOMAIN,
    SERVICE_ADD_PACKAGE,
    SERVICE_ADD_PACKAGE_FROM_IMAGE,
    SERVICE_DETECT_CARRIER,
    SERVICE_REMOVE_DELIVERED,
    SERVICE_REMOVE_PACKAGE,
    normalize_tracking_number,
)
from .coordinator import ParcelTrackingConfigEntry
from .image import async_extract_tracking_numbers

_LOGGER = logging.getLogger(__name__)

CONF_IMAGE = "image"
CONF_AI_TASK_ENTITY = "ai_task_entity"
CONF_ADD = "add"

_CARRIER = vol.In(list(CARRIER_NAMES))

ADD_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TRACKING_NUMBER): cv.string,
        vol.Optional(CONF_CARRIER): _CARRIER,
        vol.Optional(CONF_NAME): cv.string,
    }
)
ADD_FROM_IMAGE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_IMAGE): vol.Schema(
            {vol.Required("media_content_id"): cv.string}, extra=vol.ALLOW_EXTRA
        ),
        vol.Optional(CONF_AI_TASK_ENTITY): cv.entity_domain("ai_task"),
        vol.Optional(CONF_NAME): cv.string,
        vol.Optional(CONF_ADD, default=True): cv.boolean,
    }
)
DETECT_SCHEMA = vol.Schema({vol.Required(CONF_TRACKING_NUMBER): cv.string})
REMOVE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TRACKING_NUMBER): cv.string,
        vol.Optional(CONF_CARRIER): _CARRIER,
    }
)
REMOVE_DELIVERED_SCHEMA = vol.Schema({vol.Optional(CONF_CARRIER): _CARRIER})


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


async def async_resolve_carrier(
    hass: HomeAssistant, number: str, preferred: str | None = None
) -> tuple[ParcelTrackingConfigEntry, str]:
    """Find the configured carrier for a tracking number.

    Returns the config entry and how it was found: "pattern" when only one
    configured carrier matches the number format, "api" when a carrier's API
    knows the number, "guess" when several match and none knows it yet.
    """
    configured = {entry.data[CONF_CARRIER]: entry for entry in _entries(hass)}
    candidates = detect_carriers(number)
    if preferred and preferred in configured:
        candidates = [preferred, *(c for c in candidates if c != preferred)]
    # Unknown format: ask every configured carrier.
    usable = (
        [c for c in candidates if c in configured] if candidates else list(configured)
    )

    if not usable:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="no_matching_carrier",
            translation_placeholders={
                "tracking_number": number,
                "carriers": ", ".join(CARRIER_NAMES[c] for c in candidates) or "-",
            },
        )
    if len(usable) == 1 and candidates:
        return configured[usable[0]], "pattern"

    for carrier in usable:
        try:
            result = await configured[carrier].runtime_data.client.async_track(number)
        except CarrierError as err:
            _LOGGER.debug("Probing %s for %s failed: %s", carrier, number, err)
            continue
        if result.status is not ParcelStatus.NOT_FOUND:
            return configured[carrier], "api"
    return configured[usable[0]], "guess"


def _add_to_entry(
    hass: HomeAssistant,
    entry: ParcelTrackingConfigEntry,
    number: str,
    name: str | None,
) -> None:
    """Add a package, or rename it if it is already tracked."""
    packages = list(entry.options.get(CONF_PACKAGES, []))
    for index, package in enumerate(packages):
        if package[CONF_TRACKING_NUMBER] == number:
            if not name or package.get(CONF_NAME) == name:
                return
            packages[index] = {**package, CONF_NAME: name}
            break
    else:
        packages.append({CONF_TRACKING_NUMBER: number, CONF_NAME: name})
    _set_packages(hass, entry, packages)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the services."""

    async def add_package(call: ServiceCall) -> ServiceResponse:
        number = normalize_tracking_number(call.data[CONF_TRACKING_NUMBER])
        if carrier := call.data.get(CONF_CARRIER):
            entries = _entries(hass, carrier)
            if not entries:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="carrier_not_configured",
                    translation_placeholders={"carrier": CARRIER_NAMES[carrier]},
                )
            entry, detection = entries[0], "given"
        else:
            entry, detection = await async_resolve_carrier(hass, number)
        _add_to_entry(hass, entry, number, call.data.get(CONF_NAME))
        return {
            "tracking_number": number,
            "carrier": entry.data[CONF_CARRIER],
            "detection": detection,
        }

    async def add_package_from_image(call: ServiceCall) -> ServiceResponse:
        numbers, hint = await async_extract_tracking_numbers(
            hass, call.data[CONF_IMAGE], call.data.get(CONF_AI_TASK_ENTITY)
        )
        if not numbers:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="no_tracking_number_in_image",
            )
        name = call.data.get(CONF_NAME)
        found: list[dict[str, Any]] = []
        for index, number in enumerate(numbers):
            item: dict[str, Any] = {"tracking_number": number, "added": False}
            try:
                entry, detection = await async_resolve_carrier(hass, number, hint)
            except ServiceValidationError as err:
                item["error"] = str(err)
                found.append(item)
                continue
            item.update(carrier=entry.data[CONF_CARRIER], detection=detection)
            if call.data[CONF_ADD]:
                label = f"{name} ({index + 1})" if name and len(numbers) > 1 else name
                _add_to_entry(hass, entry, number, label)
                item["added"] = True
            found.append(item)
        return {"carrier_hint": hint, "packages": found}

    async def detect_carrier(call: ServiceCall) -> ServiceResponse:
        number = normalize_tracking_number(call.data[CONF_TRACKING_NUMBER])
        response: dict[str, Any] = {
            "tracking_number": number,
            "candidates": detect_carriers(number),
        }
        try:
            entry, detection = await async_resolve_carrier(hass, number)
        except ServiceValidationError:
            response["carrier"] = None
        else:
            response.update(carrier=entry.data[CONF_CARRIER], detection=detection)
        return response

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

    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_PACKAGE,
        add_package,
        ADD_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_PACKAGE_FROM_IMAGE,
        add_package_from_image,
        ADD_FROM_IMAGE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DETECT_CARRIER,
        detect_carrier,
        DETECT_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_REMOVE_PACKAGE, remove_package, REMOVE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_REMOVE_DELIVERED, remove_delivered, REMOVE_DELIVERED_SCHEMA
    )
