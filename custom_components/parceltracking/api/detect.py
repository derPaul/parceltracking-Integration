"""Guess the carrier from the format of a tracking number."""

from __future__ import annotations

import re

# Ordered by how likely the carrier is for a format; several carriers share
# plain digit formats (14 digits: DPD or Hermes, 12 digits: DHL or GLS).
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (carrier, re.compile(pattern))
    for carrier, pattern in (
        ("ups", r"1Z[0-9A-Z]{16}"),
        ("amazon_logistics", r"TB[ACM]\d{12}"),
        # Amazon Logistics in Europe: country code + 10 digits, e.g. DE5960468380
        ("amazon_logistics", r"(?:DE|AT|UK|GB|FR|IT|ES|NL|BE|PL|SE)\d{10}"),
        ("amazon", r"TB[ACM]\d{12}"),
        ("amazon", r"(?:UK|IT|ES|FR)\d{10}"),
        ("dhl", r"JJD\d{15,21}"),
        ("dhl", r"JVGL\d{6,20}"),
        ("dhl", r"[A-Z]{2}\d{9}DE"),  # Deutsche Post (UPU S10)
        ("dhl", r"\d{20}"),
        ("dhl", r"\d{12}"),
        ("dhl", r"\d{10}"),  # DHL Express
        ("gls", r"\d{11,12}"),
        ("gls", r"(?=[A-Z0-9]*[A-Z])[A-Z0-9]{8}"),  # GLS TrackID
        ("dpd", r"\d{14}[A-Z]?"),
        ("hermes", r"\d{14}"),
        ("hermes", r"\d{16}"),
    )
]


def detect_carriers(tracking_number: str) -> list[str]:
    """Return the carriers whose format matches, most likely first."""
    carriers: list[str] = []
    for carrier, pattern in PATTERNS:
        if pattern.fullmatch(tracking_number) and carrier not in carriers:
            carriers.append(carrier)
    return carriers
