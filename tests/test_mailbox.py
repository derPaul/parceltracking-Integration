"""Tests for mailbox entries."""

from __future__ import annotations

from datetime import timedelta
from email.message import EmailMessage
from email.utils import format_datetime
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.parceltracking.api import (
    CarrierApiError,
    CarrierNotFoundError,
    ParcelStatus,
    TrackingResult,
)
from custom_components.parceltracking.const import DOMAIN
from custom_components.parceltracking.mail.imap import MailboxAuthError

IMAP = "custom_components.parceltracking.mail.imap.MailboxClient"
COORDINATOR = "custom_components.parceltracking.mailbox"


@pytest.fixture(autouse=True)
def daytime(freezer: FrozenDateTimeFactory) -> FrozenDateTimeFactory:
    """Carriers are not asked at night; run the tests at midday local time."""
    freezer.move_to(dt_util.now().replace(hour=12, minute=0, second=0, microsecond=0))
    return freezer


DHL_CLIENT = "custom_components.parceltracking.api.dhl.DhlClient"
DPD_CLIENT = "custom_components.parceltracking.api.dpd.DpdClient"

MAILBOX_INPUT = {
    "provider": "auto",
    "username": "ich@example.com",
    "password": "geheim",
    "folders": "INBOX",
    "carriers": ["amazon", "dhl", "dpd", "gls", "hermes", "ups"],
}


def _mail(sender: str, subject: str, text: str, minutes_ago: int = 60) -> EmailMessage:
    # Relative to now: delivered parcels are cleaned up after a few days.
    msg = EmailMessage()
    msg["From"] = sender
    msg["Subject"] = subject
    msg["Date"] = format_datetime(dt_util.now() - timedelta(minutes=minutes_ago))
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
    minutes_ago=0,
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


async def _start_mailbox_flow(hass: HomeAssistant) -> dict:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "mailbox"}
    )
    assert result["step_id"] == "mailbox"
    return result


async def test_config_flow_mailbox_detects_provider(hass: HomeAssistant) -> None:
    result = await _start_mailbox_flow(hass)
    user_input = {**MAILBOX_INPUT, "username": " ich@gmail.com "}

    with patch(f"{IMAP}.test", side_effect=MailboxAuthError("nope")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input
        )
    # Gmail rejects normal passwords: say that an app password is needed.
    assert result["errors"] == {"base": "invalid_auth_app_password"}

    with (
        patch(f"{IMAP}.test"),
        patch("custom_components.parceltracking.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "ich@gmail.com"
    assert result["data"] == {
        "entry_type": "mailbox",
        "provider": "gmail",
        "host": "imap.gmail.com",
        "port": 993,
        "username": "ich@gmail.com",
        "password": "geheim",
    }
    assert result["options"]["carriers"] == MAILBOX_INPUT["carriers"]


async def test_config_flow_mailbox_custom_server(hass: HomeAssistant) -> None:
    result = await _start_mailbox_flow(hass)
    # Unknown domain with "automatic": ask for the server.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**MAILBOX_INPUT, "username": "ich@meinedomain.example"}
    )
    assert result["step_id"] == "mailbox_server"

    with patch(f"{IMAP}.test", side_effect=MailboxAuthError("nope")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": "mail.meinedomain.example", "port": 993}
        )
    assert result["errors"] == {"base": "invalid_auth"}

    with (
        patch(f"{IMAP}.test"),
        patch("custom_components.parceltracking.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": "mail.meinedomain.example", "port": 143}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["provider"] == "custom"
    assert (result["data"]["host"], result["data"]["port"]) == (
        "mail.meinedomain.example",
        143,
    )


async def test_config_flow_mailbox_chosen_provider_and_bad_address(
    hass: HomeAssistant,
) -> None:
    result = await _start_mailbox_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**MAILBOX_INPUT, "username": "ich"}
    )
    assert result["errors"] == {"username": "invalid_email"}

    # WEB.DE chosen explicitly; a rejected login points to the IMAP switch.
    with patch(f"{IMAP}.test", side_effect=MailboxAuthError("nope")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {**MAILBOX_INPUT, "provider": "webde", "username": "ich@web.de"},
        )
    assert result["errors"] == {"base": "invalid_auth_enable_imap"}


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


async def test_old_out_for_delivery_counts_as_delivered(hass: HomeAssistant) -> None:
    now = dt_util.now()
    stale = _mail(
        "DHL <noreply@dhl.de>",
        "Ihre Hanse Haus GmbH Sendung kommt heute",
        "Sendungsnummer: 00340434465863744643",
    )
    stale.replace_header("Date", format_datetime(now - timedelta(days=1)))
    fresh = _mail(
        "DHL <noreply@dhl.de>",
        "Ihre Amazon Sendung kommt heute",
        "Sendungsnummer: JJD000390016524712800",
    )
    fresh.replace_header("Date", format_datetime(now))
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(f"{IMAP}.fetch_new", return_value=[stale, fresh]):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # Yesterday's "kommt heute" without newer news: assumed delivered.
    old = _state(hass, entry, "00340434465863744643")
    assert old.state == "delivered"
    assert old.attributes["status_text"].startswith("Vermutlich zugestellt")
    assert old.name.endswith("Hanse Haus")
    assert _state(hass, entry, "JJD000390016524712800").state == "out_for_delivery"
    assert _summary(hass, entry, "parcels_today") == "1"


async def test_old_out_for_delivery_without_working_live_status(
    hass: HomeAssistant,
) -> None:
    """DPD has a keyless live query; when it fails, the old mail still retires."""
    yesterday = format_datetime(dt_util.now() - timedelta(days=1))
    stale = _mail(
        "DPD <noreply@dpd.de>",
        "Ihre Sendung befindet sich im Zustellfahrzeug",
        "https://tracking.dpd.de/status/de_DE/parcel/01105033079302",
    )
    stale.replace_header("Date", yesterday)
    other = _mail(
        "DPD <noreply@dpd.de>",
        "Ihre Sendung befindet sich im Zustellfahrzeug",
        "https://tracking.dpd.de/status/de_DE/parcel/01234567890123",
    )
    other.replace_header("Date", yesterday)

    async def track(number: str) -> TrackingResult:
        if number == "01105033079302":
            raise CarrierApiError("HTTP 500")
        return TrackingResult(number, ParcelStatus.IN_TRANSIT, "Im Depot")

    entry = _entry()
    entry.add_to_hass(hass)
    with (
        patch(f"{IMAP}.fetch_new", return_value=[stale, other]),
        patch(f"{DPD_CLIENT}.async_track", side_effect=track),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # Live query fails: yesterday's "Zustellfahrzeug" counts as delivered.
    failing = _state(hass, entry, "01105033079302")
    assert failing.state == "delivered"
    assert failing.attributes["status_text"].startswith("Vermutlich zugestellt")
    # Live query works and knows better: its status wins.
    assert _state(hass, entry, "01234567890123").state == "in_transit"
    assert _summary(hass, entry, "parcels_today") == "0"


async def _dpd_mail(number: str, subject: str = "Deine Bestellung wurde versandt"):
    return _mail(
        "Shop <info@shop.example>",
        subject,
        f"https://tracking.dpd.de/status/de_DE/parcel/{number}",
    )


async def test_live_queries_follow_the_parcel_status(
    hass: HomeAssistant, daytime: FrozenDateTimeFactory
) -> None:
    """Out for delivery: every update. In transit: hourly. Night: never."""
    mails = [await _dpd_mail("01234567890123"), await _dpd_mail("09999999999999")]

    async def track(number: str) -> TrackingResult:
        status = (
            ParcelStatus.OUT_FOR_DELIVERY
            if number == "01234567890123"
            else ParcelStatus.IN_TRANSIT
        )
        return TrackingResult(number, status, "…")

    entry = _entry()
    entry.add_to_hass(hass)
    with (
        patch(f"{IMAP}.fetch_new", return_value=mails),
        patch(f"{DPD_CLIENT}.async_track", side_effect=track) as dpd,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert sorted(c.args[0] for c in dpd.call_args_list) == [
            "01234567890123",
            "09999999999999",
        ]

        async def update(minutes: int) -> list[str]:
            dpd.reset_mock()
            daytime.tick(timedelta(minutes=minutes))
            await entry.runtime_data.async_refresh()
            return [c.args[0] for c in dpd.call_args_list]

        # 15 minutes later only the parcel out for delivery is asked again.
        assert await update(15) == ["01234567890123"]
        assert await update(15) == ["01234567890123"]
        # After an hour the one in transit is due as well.
        assert sorted(await update(30)) == ["01234567890123", "09999999999999"]
        # At night nobody is asked.
        daytime.move_to(dt_util.now().replace(hour=23))
        assert await update(15) == []


async def test_daily_budget_limits_dhl_queries(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    numbers = ["00340434161094042557", "00340434161094042558", "00340434161094042559"]
    mails = [
        _mail(
            "DHL <noreply@dhl.de>", "Ihre Sendung kommt heute", f"Sendungsnummer: {n}"
        )
        for n in numbers
    ]
    dhl = MockConfigEntry(
        domain=DOMAIN,
        title="DHL",
        unique_id="dhl",
        data={"carrier": "dhl", "api_key": "x"},
        options={"packages": [], "scan_interval": 30},
    )
    dhl.add_to_hass(hass)

    async def track(number: str) -> TrackingResult:
        return TrackingResult(number, ParcelStatus.OUT_FOR_DELIVERY, "In Zustellung")

    entry = _entry()
    entry.add_to_hass(hass)
    with (
        patch(f"{COORDINATOR}.DAILY_LIVE_LIMITS", {"dhl": 2}),
        patch(f"{IMAP}.fetch_new", return_value=mails),
        patch(f"{DHL_CLIENT}.async_track", side_effect=track) as dhl_track,
    ):
        # Setting up one entry sets up the whole integration, mailbox included.
        assert await hass.config_entries.async_setup(dhl.entry_id)
        await hass.async_block_till_done()
        # The mailbox may come up before the DHL entry; the next update asks DHL.
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
    assert dhl_track.call_count == 2
    assert "Daily limit of 2 live queries to dhl reached" in caplog.text
    # The third parcel shows the mail status meanwhile.
    states = [_state(hass, entry, n).attributes["live_status"] for n in numbers]
    assert sorted(states) == [False, True, True]


async def test_unknown_parcel_on_its_way_is_logged_once(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    mails = [
        await _dpd_mail(
            "01105033079302", "Ihre Sendung befindet sich im Zustellfahrzeug"
        ),
        await _dpd_mail(
            "01234567890123", "Ihre Sendung befindet sich im Zustellfahrzeug"
        ),
    ]
    entry = _entry()
    entry.add_to_hass(hass)
    with (
        patch(f"{IMAP}.fetch_new", return_value=mails),
        patch(f"{DPD_CLIENT}.async_track", side_effect=CarrierNotFoundError("x")),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    notes = [r for r in caplog.records if "does not know" in r.getMessage()]
    # Both parcels are noted, but only the first as a warning.
    assert [r.levelname for r in notes] == ["WARNING", "DEBUG"]
    assert "dpd" in notes[0].getMessage()
