"""Sensors for the Parcel Tracking integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
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
from .mailbox import MailboxCoordinator, MailParcelView

MAX_EVENTS = 10
FINISHED = (ParcelStatus.DELIVERED, ParcelStatus.RETURNED)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ParcelTrackingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one sensor per package plus summary sensors."""
    coordinator = entry.runtime_data
    if isinstance(coordinator, MailboxCoordinator):
        _async_setup_mailbox(hass, entry, coordinator, async_add_entities)
        return
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


# Mailbox entries -----------------------------------------------------------

MAIL_CARRIER_NAMES = {**CARRIER_NAMES, "amazon": "Amazon"}


@callback
def _async_setup_mailbox(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: MailboxCoordinator,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add parcel sensors as mails reveal parcels, remove them when gone."""
    known: dict[str, MailParcelSensor] = {}

    @callback
    def _sync() -> None:
        current = coordinator.data or {}
        new = [
            MailParcelSensor(coordinator, key) for key in current if key not in known
        ]
        for sensor in new:
            known[sensor.key] = sensor
        if new:
            async_add_entities(new)
        registry = er.async_get(hass)
        for key in [k for k in known if k not in current]:
            sensor = known.pop(key)
            if sensor.entity_id:
                registry.async_remove(sensor.entity_id)

    # Parcels removed while Home Assistant was stopped.
    registry = er.async_get(hass)
    prefix = f"{entry.entry_id}_mail_"
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.unique_id.startswith(prefix) and entity.unique_id.removeprefix(
            prefix
        ) not in (coordinator.data or {}):
            registry.async_remove(entity.entity_id)

    async_add_entities(
        [
            MailSummarySensor(coordinator, "active_parcels"),
            MailSummarySensor(coordinator, "parcels_today"),
        ]
    )
    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))


class MailboxEntity(CoordinatorEntity[MailboxCoordinator]):
    """Common base: one device per mailbox."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MailboxCoordinator) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            model="Postfach",
            entry_type=DeviceEntryType.SERVICE,
        )


class MailParcelSensor(MailboxEntity, SensorEntity):
    """Status of a parcel found in the mailbox."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [status.value for status in ParcelStatus]
    _attr_translation_key = "parcel"

    def __init__(self, coordinator: MailboxCoordinator, key: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.key = key
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_mail_{key}"
        self._last_name = self._name_of(coordinator.data[key])

    @staticmethod
    def _name_of(view: MailParcelView) -> str:
        carrier = MAIL_CARRIER_NAMES.get(view.carrier, view.carrier)
        return view.name or (
            f"{carrier} {view.tracking_number}"
            if view.tracking_number
            else f"Amazon {view.order_id}"
        )

    @property
    def name(self) -> str:
        """Follow the name when a later mail reveals the shop."""
        if view := self._view:
            self._last_name = self._name_of(view)
        return self._last_name

    @property
    def _view(self) -> MailParcelView | None:
        return (self.coordinator.data or {}).get(self.key)

    @property
    def available(self) -> bool:
        """Available while the parcel is tracked."""
        return super().available and self._view is not None

    @property
    def native_value(self) -> str | None:
        """Return the normalized status."""
        return view.status.value if (view := self._view) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return details of the parcel."""
        if (view := self._view) is None:
            return {}
        attrs: dict[str, Any] = {
            "carrier": MAIL_CARRIER_NAMES.get(view.carrier, view.carrier),
            "tracking_number": view.tracking_number,
            "order_id": view.order_id,
            "status_text": view.status_text,
            "mail_subject": view.mail_subject,
            "mail_date": view.mail_date.isoformat() if view.mail_date else None,
            "live_status": view.live is not None,
        }
        if view.tracking_number and view.carrier in TRACKING_URLS:
            attrs["tracking_url"] = TRACKING_URLS[view.carrier].format(
                view.tracking_number
            )
        elif view.order_id:
            attrs["tracking_url"] = (
                "https://www.amazon.de/gp/your-account/order-details"
                f"?orderID={view.order_id}"
            )
        if live := view.live:
            eta = live.estimated_delivery
            attrs.update(
                location=live.location,
                last_update=live.last_update.isoformat() if live.last_update else None,
                estimated_delivery=eta.isoformat() if eta else None,
                events=[e.as_dict() for e in live.events[:MAX_EVENTS]],
            )
        return attrs


class MailSummarySensor(MailboxEntity, SensorEntity):
    """Number of active parcels, or of parcels arriving today."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: MailboxCoordinator, kind: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._kind = kind
        self._attr_translation_key = kind
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{kind}"

    def _matching(self) -> list[MailParcelView]:
        views = (self.coordinator.data or {}).values()
        if self._kind == "parcels_today":
            return [v for v in views if v.status is ParcelStatus.OUT_FOR_DELIVERY]
        return [v for v in views if v.status not in FINISHED]

    @property
    def native_value(self) -> int:
        """Return the number of parcels."""
        return len(self._matching())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """List the parcels."""
        return {
            "parcels": [
                {
                    "name": v.name or v.tracking_number or v.order_id,
                    "carrier": MAIL_CARRIER_NAMES.get(v.carrier, v.carrier),
                    "status": v.status.value,
                    "status_text": v.status_text,
                }
                for v in self._matching()
            ]
        }
