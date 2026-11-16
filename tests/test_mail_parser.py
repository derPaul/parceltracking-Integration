"""Tests for finding parcels in e-mails."""

from __future__ import annotations

import email
import email.policy
from email.message import EmailMessage

import pytest

from custom_components.parceltracking.api.models import ParcelStatus
from custom_components.parceltracking.mail.parser import (
    find_tracking_numbers,
    is_relevant,
    parse_mail,
    status_from_text,
)


def _mail(
    sender: str, subject: str, text: str = "", html: str | None = None
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["Subject"] = subject
    msg["Date"] = "Mon, 16 Nov 2026 10:00:00 +0100"
    msg.set_content(text)
    if html is not None:
        msg.add_alternative(html, subtype="html")
    return msg


def test_dhl_mail_out_for_delivery() -> None:
    msg = _mail(
        "DHL Paket <noreply@dhl.de>",
        "Ihre DHL Sendung kommt heute",
        "Hallo, Ihre Sendung 00340434161094042557 wird heute zugestellt.",
        '<a href="https://www.dhl.de/de/privatkunden/pakete-empfangen/verfolgen.html'
        '?piececode=00340434161094042557">Sendung verfolgen</a>',
    )
    [parcel] = parse_mail(msg)
    assert parcel.carrier == "dhl"
    assert parcel.tracking_number == "00340434161094042557"
    assert parcel.status is ParcelStatus.OUT_FOR_DELIVERY
    assert parcel.name is None
    assert parcel.date is not None


def test_shop_mail_with_encoded_dpd_link() -> None:
    # Shops wrap links in click trackers that carry the target URL encoded.
    link = (
        "https://click.shop.example/r?u=https%3A%2F%2Ftracking.dpd.de%2Fstatus%2F"
        "de_DE%2Fparcel%2F01234567890123"
    )
    msg = _mail(
        "Schuhladen <service@schuhladen.example>",
        "Deine Bestellung ist unterwegs",
        "",
        f'<p>Deine Bestellung wurde versandt.</p><a href="{link}">Verfolgen</a>',
    )
    [parcel] = parse_mail(msg)
    assert (parcel.carrier, parcel.tracking_number) == ("dpd", "01234567890123")
    assert parcel.name == "Schuhladen"
    assert parcel.status is ParcelStatus.IN_TRANSIT


def test_labeled_number_uses_mentioned_carrier() -> None:
    # 14 digits fit DPD and Hermes; the text names Hermes.
    msg = _mail(
        "Bücherwurm <info@buecher.example>",
        "Versandbestätigung",
        "Wir haben dein Paket an Hermes übergeben.\n"
        "Sendungsnummer: 0123 4567 8901 23 Viel Spaß beim Lesen!",
    )
    [parcel] = parse_mail(msg)
    assert (parcel.carrier, parcel.tracking_number) == ("hermes", "01234567890123")


def test_hermes_gls_ups_links() -> None:
    text = (
        "https://www.myhermes.de/empfangen/sendungsverfolgung/sendungsinformation"
        "#H1001234567890123456 "
        "https://gls-group.eu/DE/de/paketverfolgung?match=ZXY12AB3 "
        "https://www.ups.com/track?loc=de_DE&tracknum=1Z999AA10123456784"
    )
    assert set(find_tracking_numbers(text)) == {
        ("hermes", "H1001234567890123456"),
        ("gls", "ZXY12AB3"),
        ("ups", "1Z999AA10123456784"),
    }


def test_amazon_shipped_and_delivered() -> None:
    shipped = _mail(
        "Amazon.de <versandbestaetigung@amazon.de>",
        "Versandt: „Kabellose Kopfhörer Pro“ und 1 weiterer Artikel",
        "Bestellung #302-1234567-1234567\nIhr Paket wird mit DHL geliefert.\n"
        "https://www.dhl.de/de/privatkunden/pakete-empfangen/verfolgen.html"
        "?piececode=JJD000390007827538398",
    )
    [parcel] = parse_mail(shipped)
    assert parcel.key == "amazon-302-1234567-1234567"
    assert parcel.order_id == "302-1234567-1234567"
    assert (parcel.carrier, parcel.tracking_number) == (
        "dhl",
        "JJD000390007827538398",
    )
    assert parcel.name == "Kabellose Kopfhörer Pro"
    assert parcel.status is ParcelStatus.IN_TRANSIT

    delivered = _mail(
        "Amazon.de <shipment-tracking@amazon.de>",
        "Zugestellt: Ihr Amazon.de-Paket",
        "Ihr Paket mit der Bestellnummer 302-1234567-1234567 wurde zugestellt.",
    )
    [parcel] = parse_mail(delivered)
    assert parcel.key == "amazon-302-1234567-1234567"
    assert parcel.carrier == "amazon"
    assert parcel.status is ParcelStatus.DELIVERED


def test_amazon_order_without_tracking() -> None:
    msg = _mail(
        "Amazon.de <auto-confirm@amazon.de>",
        "Bestellt: „Gartenschlauch 20 m“",
        "Vielen Dank für Ihre Bestellung 028-7654321-7654321.",
    )
    [parcel] = parse_mail(msg)
    assert parcel.carrier == "amazon"
    assert parcel.tracking_number is None
    assert parcel.status is ParcelStatus.PRE_TRANSIT


def test_newsletter_yields_nothing() -> None:
    msg = _mail(
        "Newsletter <news@shop.example>",
        "Unsere Angebote der Woche",
        "Jetzt 20 % sparen! Rufen Sie uns an: 0800 123456789. Kundennummer 1234567890",
    )
    assert parse_mail(msg) == []
    assert not is_relevant(msg)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Ihr Paket wurde zugestellt", ParcelStatus.DELIVERED),
        ("Zugestellt: Ihr Amazon.de-Paket", ParcelStatus.DELIVERED),
        ("Ihr Paket wird morgen zugestellt", None),
        ("Ihre Sendung wird heute zugestellt", ParcelStatus.OUT_FOR_DELIVERY),
        ("Paket konnte nicht zugestellt werden", ParcelStatus.EXCEPTION),
        ("Ihr Paket liegt zur Abholung bereit", ParcelStatus.READY_FOR_PICKUP),
        ("Ihre Bestellung wurde versandt", ParcelStatus.IN_TRANSIT),
        ("Ihre Sendung wurde angekündigt", ParcelStatus.PRE_TRANSIT),
        ("Neue Packstation in Ihrer Nähe", None),
    ],
)
def test_status_from_text(text: str, expected: ParcelStatus | None) -> None:
    assert status_from_text(text) is expected


@pytest.mark.parametrize(
    ("subject", "name"),
    [
        ("Ihre Coolblue GmbH Sendung ist unterwegs", "Coolblue"),
        ("Ihre Hanse Haus GmbH Sendung kommt heute", "Hanse Haus"),
        ("Ihre Amazon Sendung kommt heute", "Amazon"),
        (
            "Ihre Miele & Cie. KG Zentraler Versand Sendung wurde im Briefkasten",
            "Miele",
        ),
        ("Ihr Paket von Zalando kommt heute", "Zalando"),
        ("Ihre DHL Sendung kommt heute", None),
    ],
)
def test_carrier_mail_names_shop_from_subject(subject: str, name: str | None) -> None:
    msg = _mail("DHL <noreply@dhl.de>", subject, "Sendungsnummer: 00340434465863744643")
    [parcel] = parse_mail(msg)
    assert parcel.name == name
    assert "\r" not in parcel.status_text and "  " not in parcel.status_text


def test_folded_subject_is_joined() -> None:
    raw = (
        b"From: DHL <noreply@dhl.de>\r\n"
        b"Subject: Ihre Miele & Cie. KG Zentraler Versand Sendung wurde im Briefkasten\r\n"
        b" zugestellt\r\n"
        b"Date: Mon, 16 Nov 2026 13:24:40 +0100\r\n\r\n"
        b"Sendungsnummer: 573719167174\r\n"
    )
    [parcel] = parse_mail(email.message_from_bytes(raw, policy=email.policy.compat32))
    assert parcel.status_text == (
        "Ihre Miele & Cie. KG Zentraler Versand Sendung wurde im Briefkasten zugestellt"
    )
    assert parcel.status is ParcelStatus.DELIVERED
    assert parcel.name == "Miele"
