"""Tests for carrier detection by tracking number format."""

from __future__ import annotations

import pytest

from custom_components.parceltracking.api.detect import detect_carriers


@pytest.mark.parametrize(
    ("number", "expected"),
    [
        ("1Z999AA10123456784", ["ups"]),
        ("TBA123456789012", ["amazon_logistics", "amazon"]),
        ("UK0123456789", ["amazon_logistics", "amazon"]),
        ("DE5960468380", ["amazon_logistics"]),
        ("JJD000390007827538398", ["dhl"]),
        ("RR123456789DE", ["dhl"]),
        ("00340434161094042557", ["dhl"]),
        ("340434161094", ["dhl", "gls"]),
        ("1234567890", ["dhl"]),
        ("12345678901", ["gls"]),
        ("ZXY12AB3", ["gls"]),
        ("01234567890123", ["dpd", "hermes"]),
        ("01234567890123A", ["dpd"]),
        ("1234567890123456", ["hermes"]),
        ("ABC", []),
        ("302-1234567-1234567", []),
    ],
)
def test_detect_carriers(number: str, expected: list[str]) -> None:
    assert detect_carriers(number) == expected
