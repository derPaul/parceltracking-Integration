"""Sensors for the Parcel Tracking integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ParcelStatus, TrackingResult
from .const import (
    CARRIER_NAMES,
    CONF_CARRIER,
    CONF_NAME,
    CONF_TRACKING_NUMBER,
    DOMAIN,
    TRACKING_URLS,
)
from .coordinator import ParcelTrackingConfigEntry, ParcelTrackingCoordinator

MAX_EVENTS = 10
FINISHED = (ParcelStatus.DELIVERED, ParcelStatus.RETURNED)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ParcelTrackingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one sensor per package plus a summary sensor."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [ActiveParcelsSensor(coordinator)]
    entities.extend(ParcelSensor(coordinator, pkg) for pkg in coordinator.packages)
    async_add_entities(entities)


class ParcelTrackingEntity(CoordinatorEntity[ParcelTrackingCoordinator]):
    """Common base: one device per carrier."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ParcelTrackingCoordinator) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._carrier: str = entry.data[CONF_CARRIER]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=CARRIER_NAMES[self._carrier],
            manufacturer=CARRIER_NAMES[self._carrier],
            entry_type=DeviceEntryType.SERVICE,
        )


class ParcelSensor(ParcelTrackingEntity, SensorEntity):
    """Status of a single parcel."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [status.value for status in ParcelStatus]
    _attr_translation_key = "parcel"

    def __init__(
        self, coordinator: ParcelTrackingCoordinator, package: dict[str, Any]
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._tracking_number: str = package[CONF_TRACKING_NUMBER]
        self._attr_name = package.get(CONF_NAME) or self._tracking_number
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_parcel_{self._tracking_number}"
        )

    @property
    def _result(self) -> TrackingResult | None:
        return (self.coordinator.data or {}).get(self._tracking_number)

    @property
    def available(self) -> bool:
        """Available once the parcel has been fetched at least once."""
        return super().available and self._result is not None

    @property
    def native_value(self) -> str | None:
        """Return the normalized status."""
        return result.status.value if (result := self._result) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return details of the parcel."""
        attrs: dict[str, Any] = {
            "carrier": CARRIER_NAMES[self._carrier],
            "tracking_number": self._tracking_number,
            "tracking_url": TRACKING_URLS[self._carrier].format(self._tracking_number),
        }
        if (result := self._result) is None:
            return attrs
        eta = result.estimated_delivery
        attrs.update(
            {
                "status_text": result.status_text,
                "location": result.location,
                "last_update": result.last_update.isoformat()
                if result.last_update
                else None,
                "estimated_delivery": eta.isoformat() if eta else None,
                "events": [e.as_dict() for e in result.events[:MAX_EVENTS]],
            }
        )
        return attrs


class ActiveParcelsSensor(ParcelTrackingEntity, SensorEntity):
    """Number of parcels of this carrier that are still on their way."""

    _attr_translation_key = "active_parcels"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: ParcelTrackingCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_active_parcels"

    def _active(self) -> list[tuple[dict[str, Any], TrackingResult | None]]:
        data = self.coordinator.data or {}
        return [
            (pkg, data.get(pkg[CONF_TRACKING_NUMBER]))
            for pkg in self.coordinator.packages
            if (res := data.get(pkg[CONF_TRACKING_NUMBER])) is None
            or res.status not in FINISHED
        ]

    @property
    def native_value(self) -> int:
        """Return the number of active parcels."""
        return len(self._active())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """List the active parcels."""
        return {
            "parcels": [
                {
                    "name": pkg.get(CONF_NAME) or pkg[CONF_TRACKING_NUMBER],
                    "tracking_number": pkg[CONF_TRACKING_NUMBER],
                    "status": res.status.value if res else None,
                    "status_text": res.status_text if res else None,
                    "estimated_delivery": res.estimated_delivery.isoformat()
                    if res and res.estimated_delivery
                    else None,
                }
                for pkg, res in self._active()
            ]
        }
