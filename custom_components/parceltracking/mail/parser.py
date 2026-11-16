"""Find parcels in shipping e-mails. Independent of Home Assistant."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from urllib.parse import unquote

from ..api.detect import detect_carriers
from ..api.models import ParcelStatus

# Sender domains of the carriers themselves (matched as suffix).
SENDER_DOMAINS = {
    "dhl.de": "dhl",
    "dhl.com": "dhl",
    "deutschepost.de": "dhl",
    "dpd.de": "dpd",
    "dpd.com": "dpd",
    "gls-group.eu": "gls",
    "gls-group.com": "gls",
    "gls-pakete.de": "gls",
    "myhermes.de": "hermes",
    "hermesworld.com": "hermes",
    "hermes-europe.eu": "hermes",
    "ups.com": "ups",
}
AMAZON_DOMAIN = re.compile(
    r"(^|\.)amazon\.(de|com|co\.uk|fr|it|es|nl|at|pl|se|com\.be)$"
)

# Tracking links: the number is taken from the URL of the carrier's own page.
_URL_CHARS = r"[^\s\"'<>]*?"
LINK_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "dhl",
        re.compile(
            rf"dhl\.(?:de|com){_URL_CHARS}(?:piececode|idc|tracking-id|AWB|"
            r"trackingnumber)=([A-Z0-9]{8,35})",
            re.IGNORECASE,
        ),
    ),
    (
        "dpd",
        re.compile(
            rf"dpd\.(?:de|com){_URL_CHARS}(?:parcel/|query=|parcelno=|pknr=)"
            r"(\d{14}[A-Z]?)",
            re.IGNORECASE,
        ),
    ),
    (
        "gls",
        re.compile(
            rf"gls-(?:group\.(?:eu|com)|pakete\.de){_URL_CHARS}"
            r"(?:match=|trackingnumber=|parcelno=)([A-Z0-9]{8,12})",
            re.IGNORECASE,
        ),
    ),
    (
        "hermes",
        re.compile(
            rf"(?:myhermes\.de|hermesworld\.com){_URL_CHARS}"
            r"(?:sendungsinformation#|trackingid=|suche=|shipmentid=|parcelid=)"
            r"([A-Z0-9]{8,20})",
            re.IGNORECASE,
        ),
    ),
    (
        "ups",
        re.compile(
            rf"ups\.com{_URL_CHARS}(?:tracknum|inquirynumber1|tracknums)="
            r"(1Z[A-Z0-9]{16})",
            re.IGNORECASE,
        ),
    ),
]
# Formats distinctive enough to be taken from anywhere in the text.
STANDALONE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ups", re.compile(r"\b(1Z[0-9A-Z]{16})\b")),
    ("amazon_logistics", re.compile(r"\b(TB[ACM]\d{12})\b")),
]
# "Sendungsnummer: 1234 5678 9012" and friends. The label is matched case
# insensitively, the number only in upper case so following words end it.
LABELED_NUMBER = re.compile(
    r"(?i:sendungsnummer|sendungs-?nr\.?|trackingnummer|tracking-?nummer|"
    r"tracking[- ]?id|tracking number|paketnummer|paket-?nr\.?|"
    r"sendungsverfolgungsnummer|paketscheinnummer|tracking code)"
    # Each group of characters must end at a word boundary, so the capital
    # of a following word ("23 Viel Spaß") is not taken for a check letter.
    r"\s*[:#.]?\s*([A-Z0-9]+\b(?: [A-Z0-9]+\b){0,7})"
)
AMAZON_ORDER = re.compile(r"\b(\d{3}-\d{7}-\d{7})\b")
QUOTED_NAME = re.compile(r"[„\"“»](.+?)[“\"”«]")

CARRIER_MENTIONS = {
    "dhl": re.compile(r"\bDHL\b|Deutsche Post", re.IGNORECASE),
    "dpd": re.compile(r"\bDPD\b", re.IGNORECASE),
    "gls": re.compile(r"\bGLS\b", re.IGNORECASE),
    "hermes": re.compile(r"\bHermes\b", re.IGNORECASE),
    "ups": re.compile(r"\bUPS\b"),
}

# Checked in order, so "wird heute zugestellt" and "konnte nicht zugestellt
# werden" are not taken for delivered. Future tense ("wird morgen zugestellt")
# matches nothing here and falls back to in transit.
STATUS_PATTERNS: list[tuple[ParcelStatus, re.Pattern[str]]] = [
    (status, re.compile(pattern, re.IGNORECASE))
    for status, pattern in (
        (
            ParcelStatus.EXCEPTION,
            r"nicht (?:zugestellt|geliefert)|zustellversuch|verzögert|verspätet|"
            r"delivery attempt|delayed",
        ),
        (
            ParcelStatus.OUT_FOR_DELIVERY,
            r"kommt heute|heute (?:zugestellt|geliefert)|(?:zustellung|lieferung) "
            r"heute|in zustellung|zustellfahrzeug|out for delivery|arriving today",
        ),
        (
            ParcelStatus.READY_FOR_PICKUP,
            r"abholbereit|liegt (?:\w+ )?zur abholung|kann abgeholt werden|"
            r"ready for pickup",
        ),
        (
            ParcelStatus.DELIVERED,
            r"^\W*(?:zugestellt|geliefert)\b|\b(?:wurde|ist|bereits|erfolgreich)\b"
            r"[^.!?\n]{0,40}\b(?:zugestellt|geliefert|abgegeben)\b|\bdelivered\b",
        ),
        (
            ParcelStatus.RETURNED,
            r"zurück an den absender|returned to sender",
        ),
        (
            ParcelStatus.IN_TRANSIT,
            r"versandt|versendet|verschickt|unterwegs|auf dem weg|"
            r"versandbestätigung|shipped|on its way",
        ),
        (
            ParcelStatus.PRE_TRANSIT,
            r"^\W*bestellt\b|angekündigt|bestellbestätigung|wird vorbereitet|"
            r"\bordered\b",
        ),
    )
]

# Subjects worth downloading the full mail for (shops send shipping mails too).
RELEVANT_SUBJECT = re.compile(
    r"versand|versendet|verschickt|sendung|paket|lieferung|geliefert|zugestellt|"
    r"zustellung|unterwegs|tracking|bestell|shipped|shipment|deliver|order",
    re.IGNORECASE,
)


@dataclass(slots=True)
class MailParcel:
    """A parcel mentioned in an e-mail."""

    key: str
    carrier: str
    tracking_number: str | None
    order_id: str | None
    name: str | None
    status: ParcelStatus
    status_text: str
    date: datetime | None


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (ValueError, LookupError, UnicodeError):
        return value


def sender_of(msg: Message) -> tuple[str, str]:
    """Return display name and lower-case domain of the sender."""
    name, address = parseaddr(_decode(msg.get("From")))
    return name.strip(), address.rpartition("@")[2].lower()


def sender_carrier(domain: str) -> str | None:
    """Return the carrier a sender domain belongs to."""
    if AMAZON_DOMAIN.search(domain):
        return "amazon"
    for suffix, carrier in SENDER_DOMAINS.items():
        if domain == suffix or domain.endswith(f".{suffix}"):
            return carrier
    return None


def is_relevant(msg: Message) -> bool:
    """Cheap check on the headers whether a mail may be about a parcel."""
    _, domain = sender_of(msg)
    return bool(
        sender_carrier(domain) or RELEVANT_SUBJECT.search(_decode(msg.get("Subject")))
    )


def _bodies(msg: Message) -> tuple[str, str]:
    """Return (plain text, raw html) of a mail."""
    plain: list[str] = []
    rich: list[str] = []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_maintype() != "text" or part.get_filename():
            continue
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except LookupError:
            text = payload.decode("utf-8", errors="replace")
        (rich if part.get_content_subtype() == "html" else plain).append(text)
    return "\n".join(plain), "\n".join(rich)


def _html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</td>", "\n", raw)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    return re.sub(r"[ \t\xa0]+", " ", text)


def status_from_text(text: str) -> ParcelStatus | None:
    """Return the status a subject or text announces, if any."""
    for status, pattern in STATUS_PATTERNS:
        if pattern.search(text):
            return status
    return None


def _labeled_numbers(text: str) -> list[str]:
    numbers = []
    for match in LABELED_NUMBER.finditer(text):
        tokens = match.group(1).split()
        # Drop trailing words until the rest looks like a tracking number.
        while tokens:
            candidate = "".join(tokens)
            if detect_carriers(candidate):
                numbers.append(candidate)
                break
            tokens.pop()
    return numbers


def _pick_carrier(
    number: str, link_carrier: str | None, sender: str | None, text: str
) -> str | None:
    candidates = detect_carriers(number)
    if link_carrier:
        return link_carrier
    if sender in candidates:
        return sender
    mentioned = [c for c, pattern in CARRIER_MENTIONS.items() if pattern.search(text)]
    for carrier in candidates:
        if carrier in mentioned:
            return carrier
    return candidates[0] if candidates else None


def find_tracking_numbers(
    text: str, sender: str | None = None
) -> list[tuple[str, str]]:
    """Return (carrier, tracking number) pairs found in a mail text."""
    # Redirect links often carry the carrier URL percent-encoded.
    searchable = unquote(unquote(text))
    found: dict[str, str | None] = {}
    for carrier, pattern in LINK_PATTERNS:
        for match in pattern.finditer(searchable):
            found[match.group(1).upper()] = carrier
    for carrier, pattern in STANDALONE_PATTERNS:
        for match in pattern.finditer(searchable):
            found.setdefault(match.group(1), carrier)
    for number in _labeled_numbers(searchable):
        found.setdefault(number, None)

    result = []
    for number, link_carrier in found.items():
        if carrier := _pick_carrier(number, link_carrier, sender, searchable):
            result.append((carrier, number))
    return result


def parse_mail(msg: Message) -> list[MailParcel]:
    """Return the parcels a mail is about."""
    subject = _decode(msg.get("Subject")).strip()
    display_name, domain = sender_of(msg)
    sender = sender_carrier(domain)
    try:
        date = parsedate_to_datetime(msg["Date"]) if msg.get("Date") else None
    except (TypeError, ValueError):
        date = None

    plain, rich = _bodies(msg)
    text = plain or _html_to_text(rich)
    numbers = find_tracking_numbers(f"{subject}\n{text}\n{rich}", sender)
    # The subject is the most reliable; the start of the text helps for shop
    # mails with generic subjects. Further down are ads and footers.
    status = (
        status_from_text(subject)
        or status_from_text(text.strip()[:500])
        or ParcelStatus.IN_TRANSIT
    )

    if sender == "amazon":
        return _parse_amazon(subject, text, numbers, status, date)

    # A shop's name is a better label than the tracking number.
    name = display_name if display_name and not sender else None
    return [
        MailParcel(
            key=number,
            carrier=carrier,
            tracking_number=number,
            order_id=None,
            name=name,
            status=status,
            status_text=subject,
            date=date,
        )
        for carrier, number in numbers
    ]


def _parse_amazon(
    subject: str,
    text: str,
    numbers: list[tuple[str, str]],
    status: ParcelStatus,
    date: datetime | None,
) -> list[MailParcel]:
    """Amazon mails are about orders; a carrier number is attached if present."""
    orders = list(dict.fromkeys(AMAZON_ORDER.findall(f"{subject}\n{text}")))
    name_match = QUOTED_NAME.search(subject)
    name = name_match.group(1).strip() if name_match else None
    if not orders:
        return [
            MailParcel(number, carrier, number, None, name, status, subject, date)
            for carrier, number in numbers
        ]
    carrier, number = (
        numbers[0]
        if len(orders) == 1 and len(numbers) == 1
        else (
            "amazon",
            None,
        )
    )
    return [
        MailParcel(
            key=f"amazon-{order}",
            carrier=carrier,
            tracking_number=number,
            order_id=order,
            name=name,
            status=status,
            status_text=subject,
            date=date,
        )
        for order in orders
    ]
