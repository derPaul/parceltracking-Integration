"""Tests for mailbox entries."""

from __future__ import annotations

from email.message import EmailMessage
from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.parceltracking.api import ParcelStatus, TrackingResult
from custom_components.parceltracking.const import DOMAIN
from custom_components.parceltracking.mail.imap import MailboxAuthError

IMAP = "custom_components.parceltracking.mail.imap.MailboxClient"
DPD_CLIENT = "custom_components.parceltracking.api.dpd.DpdClient"

MAILBOX_INPUT = {
    "host": "imap.example.com",
    "port": 993,
    "username": "ich@example.com",
    "password": "geheim",
    "folders": "INBOX",
    "carriers": ["amazon", "dhl", "dpd", "gls", "hermes", "ups"],
}


def _mail(
    sender: str, subject: str, text: str, date: str = "16 Nov 2026 10:00"
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["Subject"] = subject
    msg["Date"] = f"Mon, {date}:00 +0100"
    msg.set_content(text)
    return msg


DHL_MAIL = _mail(
    "DHL <noreply@dhl.de>",
    "Ihre DHL Sendung ist unterwegs",
    "Sendungsnummer: 00340434161094042557",
)
DPD_MAIL = _mail(
    "Shop <info@shop.example>",
    "Deine Bestellung wurde versandt",
    "https://tracking.dpd.de/status/de_DE/parcel/01234567890123",
)
AMAZON_SHIPPED = _mail(
    "Amazon.de <versandbestaetigung@amazon.de>",
    "Versandt: „Gartenschlauch“",
    "Bestellung 302-1234567-1234567",
)
AMAZON_DELIVERED = _mail(
    "Amazon.de <shipment-tracking@amazon.de>",
    "Zugestellt: Ihr Amazon.de-Paket",
    "Bestellung 302-1234567-1234567 wurde zugestellt.",
    date="16 Nov 2026 15:00",
)


def _entry(carriers: list[str] | None = None) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="ich@example.com",
        unique_id="mailbox_ich@example.com@imap.example.com",
        data={
            "entry_type": "mailbox",
            "host": "imap.example.com",
            "port": 993,
            "username": "ich@example.com",
            "password": "geheim",
        },
        options={
            "folders": "INBOX",
            "carriers": carriers or MAILBOX_INPUT["carriers"],
            "lookback_days": 14,
            "keep_delivered_days": 3,
            "scan_interval": 15,
        },
    )


def _state(hass: HomeAssistant, entry: MockConfigEntry, key: str):
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_mail_{key}"
    )
    return hass.states.get(entity_id) if entity_id else None


def _summary(hass: HomeAssistant, entry: MockConfigEntry, kind: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_{kind}"
    )
    return hass.states.get(entity_id).state


async def _dpd_track(number: str) -> TrackingResult:
    return TrackingResult(
        tracking_number=number,
        status=ParcelStatus.OUT_FOR_DELIVERY,
        status_text="In Zustellung",
    )


async def test_config_flow_mailbox(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "mailbox"}
    )
    assert result["step_id"] == "mailbox"

    with patch(f"{IMAP}.test", side_effect=MailboxAuthError("nope")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], MAILBOX_INPUT
        )
    assert result["errors"] == {"base": "invalid_auth"}

    with (
        patch(f"{IMAP}.test"),
        patch("custom_components.parceltracking.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], MAILBOX_INPUT
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "ich@example.com"
    assert result["data"]["entry_type"] == "mailbox"
    assert "folders" not in result["data"]
    assert result["options"]["carriers"] == MAILBOX_INPUT["carriers"]


async def test_mailbox_finds_parcels_and_live_status(hass: HomeAssistant) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    with (
        patch(f"{IMAP}.fetch_new", return_value=[DHL_MAIL, DPD_MAIL, AMAZON_SHIPPED]),
        patch(f"{DPD_CLIENT}.async_track", side_effect=_dpd_track) as dpd,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    # DHL: no DHL credentials configured, so the mail status is shown.
    dhl = _state(hass, entry, "00340434161094042557")
    assert dhl.state == "in_transit"
    assert dhl.attributes["live_status"] is False
    # DPD needs no key: live status from the carrier.
    dpd_state = _state(hass, entry, "01234567890123")
    assert dpd_state.state == "out_for_delivery"
    assert dpd_state.attributes["live_status"] is True
    assert dpd_state.name.endswith("Shop")
    dpd.assert_called_once_with("01234567890123")
    # Amazon order, named after the article.
    amazon = _state(hass, entry, "amazon-302-1234567-1234567")
    assert amazon.state == "in_transit"
    assert amazon.name.endswith("Gartenschlauch")
    assert _summary(hass, entry, "active_parcels") == "3"
    assert _summary(hass, entry, "parcels_today") == "1"

    # A later delivery mail updates the order; no reload needed.
    with (
        patch(f"{IMAP}.fetch_new", return_value=[AMAZON_DELIVERED]),
        patch(f"{DPD_CLIENT}.async_track", side_effect=_dpd_track),
    ):
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
    assert _state(hass, entry, "amazon-302-1234567-1234567").state == "delivered"
    assert _summary(hass, entry, "active_parcels") == "2"

    # Removing a parcel removes its sensor; the same mail does not bring it back.
    await hass.services.async_call(
        DOMAIN,
        "remove_package",
        {"tracking_number": "00340434161094042557"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert _state(hass, entry, "00340434161094042557") is None
    with (
        patch(f"{IMAP}.fetch_new", return_value=[DHL_MAIL]),
        patch(f"{DPD_CLIENT}.async_track", side_effect=_dpd_track),
    ):
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
    assert _state(hass, entry, "00340434161094042557") is None

    # Parcels survive a restart of the entry.
    with (
        patch(f"{IMAP}.fetch_new", return_value=[]),
        patch(f"{DPD_CLIENT}.async_track", side_effect=_dpd_track),
    ):
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert _state(hass, entry, "01234567890123").state == "out_for_delivery"
    assert _state(hass, entry, "amazon-302-1234567-1234567").state == "delivered"


async def test_mailbox_only_selected_carriers(hass: HomeAssistant) -> None:
    entry = _entry(carriers=["amazon"])
    entry.add_to_hass(hass)
    with patch(f"{IMAP}.fetch_new", return_value=[DHL_MAIL, DPD_MAIL, AMAZON_SHIPPED]):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert _state(hass, entry, "amazon-302-1234567-1234567") is not None
    assert _state(hass, entry, "00340434161094042557") is None
    assert _state(hass, entry, "01234567890123") is None


async def test_mailbox_auth_failure_starts_reauth(hass: HomeAssistant) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(f"{IMAP}.fetch_new", side_effect=MailboxAuthError("no")):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    [flow] = [
        f
        for f in hass.config_entries.flow.async_progress()
        if f["context"]["source"] == "reauth"
    ]
    assert flow["step_id"] == "reauth_mailbox"
