"""Tests for config flow, setup, sensors and services."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.parceltracking.api import (
    CarrierAuthError,
    CarrierNotFoundError,
    ParcelStatus,
    TrackingResult,
)
from custom_components.parceltracking.const import DOMAIN

DHL_CLIENT = "custom_components.parceltracking.api.dhl.DhlClient"


def _entry(packages: list[dict] | None = None) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="DHL",
        unique_id="dhl",
        data={"carrier": "dhl", "api_key": "secret"},
        options={"packages": packages or [], "scan_interval": 30},
    )


async def _track(number: str) -> TrackingResult:
    if number == "UNKNOWN1":
        raise CarrierNotFoundError(number)
    return TrackingResult(
        tracking_number=number,
        status=ParcelStatus.DELIVERED if number == "DONE1" else ParcelStatus.IN_TRANSIT,
        status_text="Unterwegs",
    )


async def test_config_flow_dhl(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"carrier": "dhl"}
    )
    assert result["step_id"] == "credentials"

    with patch(f"{DHL_CLIENT}.async_track", side_effect=CarrierAuthError("bad")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"api_key": "wrong"}
        )
    assert result["errors"] == {"base": "invalid_auth"}

    with (
        patch(f"{DHL_CLIENT}.async_track", side_effect=CarrierNotFoundError("x")),
        patch("custom_components.parceltracking.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"api_key": "good"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"carrier": "dhl", "api_key": "good"}
    assert result["options"]["packages"] == []


async def test_config_flow_dpd_without_credentials(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with patch("custom_components.parceltracking.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"carrier": "dpd"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "DPD"


async def test_setup_sensors_and_services(hass: HomeAssistant) -> None:
    entry = _entry(
        [
            {"tracking_number": "ABC123", "name": "Kopfhörer"},
            {"tracking_number": "DONE1", "name": None},
            {"tracking_number": "UNKNOWN1", "name": None},
        ]
    )
    entry.add_to_hass(hass)
    with patch(f"{DHL_CLIENT}.async_track", side_effect=_track) as track:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED

        state = hass.states.get("sensor.dhl_kopfhorer")
        assert state is not None
        assert state.state == "in_transit"
        assert state.attributes["tracking_number"] == "ABC123"
        assert "piececode=ABC123" in state.attributes["tracking_url"]
        assert hass.states.get("sensor.dhl_done1").state == "delivered"
        assert hass.states.get("sensor.dhl_unknown1").state == "not_found"
        assert hass.states.get("sensor.dhl_active_parcels").state == "2"

        # Delivered parcels are not polled again.
        track.reset_mock()
        await entry.runtime_data.async_refresh()
        polled = {call.args[0] for call in track.call_args_list}
        assert polled == {"ABC123", "UNKNOWN1"}

        await hass.services.async_call(
            DOMAIN,
            "add_package",
            {"carrier": "dhl", "tracking_number": "new 456", "name": "Buch"},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert (
            hass.states.get("sensor.dhl_buch").attributes["tracking_number"] == "NEW456"
        )

        await hass.services.async_call(DOMAIN, "remove_delivered", {}, blocking=True)
        await hass.async_block_till_done()
        registry = er.async_get(hass)
        assert registry.async_get("sensor.dhl_done1") is None
        assert [p["tracking_number"] for p in entry.options["packages"]] == [
            "ABC123",
            "UNKNOWN1",
            "NEW456",
        ]

        await hass.services.async_call(
            DOMAIN, "remove_package", {"tracking_number": "abc123"}, blocking=True
        )
        await hass.async_block_till_done()
        assert registry.async_get("sensor.dhl_kopfhorer") is None


async def test_auth_failure_starts_reauth(hass: HomeAssistant) -> None:
    entry = _entry([{"tracking_number": "ABC123", "name": None}])
    entry.add_to_hass(hass)
    with patch(
        f"{DHL_CLIENT}.async_track", AsyncMock(side_effect=CarrierAuthError("no"))
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == "reauth" for flow in flows)


async def test_options_flow_add_package(hass: HomeAssistant) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(f"{DHL_CLIENT}.async_track", side_effect=_track):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] is FlowResultType.MENU
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "add_package"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"tracking_number": " xyz 789 ", "name": "Schuhe"}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["packages"] == [
        {"tracking_number": "XYZ789", "name": "Schuhe"}
    ]
    assert hass.states.get("sensor.dhl_schuhe").state == "in_transit"
