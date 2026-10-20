"""Tests for the carrier response parsers."""

from __future__ import annotations

import json
from datetime import date, datetime

from custom_components.parceltracking.api.amazon import parse_amazon
from custom_components.parceltracking.api.amazon_logistics import (
    parse_amazon_logistics,
)
from custom_components.parceltracking.api.dhl import parse_dhl
from custom_components.parceltracking.api.dpd import parse_dpd
from custom_components.parceltracking.api.gls import parse_gls
from custom_components.parceltracking.api.hermes import parse_hermes
from custom_components.parceltracking.api.models import ParcelStatus
from custom_components.parceltracking.api.ups import parse_ups


def test_dhl_in_transit() -> None:
    data = {
        "shipments": [
            {
                "id": "123",
                "status": {
                    "timestamp": "2026-10-20T09:15:00+02:00",
                    "location": {
                        "address": {"addressLocality": "Leipzig", "countryCode": "DE"}
                    },
                    "statusCode": "transit",
                    "description": "Die Sendung wurde im Paketzentrum bearbeitet.",
                },
                "estimatedTimeOfDelivery": "2026-10-21T12:00:00+02:00",
                "events": [
                    {
                        "timestamp": "2026-10-20T09:15:00+02:00",
                        "location": {"address": {"addressLocality": "Leipzig"}},
                        "description": "Die Sendung wurde im Paketzentrum bearbeitet.",
                    },
                    {
                        "timestamp": "2026-10-19T18:00:00+02:00",
                        "description": "Die Sendung wurde elektronisch angekündigt.",
                    },
                ],
            }
        ]
    }
    result = parse_dhl(data, "123")
    assert result.status is ParcelStatus.IN_TRANSIT
    assert result.location == "Leipzig, DE"
    assert result.estimated_delivery == datetime.fromisoformat(
        "2026-10-21T12:00:00+02:00"
    )
    assert len(result.events) == 2
    assert result.events[0].description.startswith("Die Sendung wurde im Paketzentrum")


def test_dhl_out_for_delivery_from_text() -> None:
    data = {
        "shipments": [
            {
                "status": {
                    "timestamp": "2026-10-20T08:00:00+02:00",
                    "statusCode": "transit",
                    "description": "Die Sendung wurde in das Zustellfahrzeug geladen.",
                }
            }
        ]
    }
    assert parse_dhl(data, "1").status is ParcelStatus.OUT_FOR_DELIVERY


def test_dhl_empty() -> None:
    assert parse_dhl({"shipments": []}, "1").status is ParcelStatus.NOT_FOUND


def test_ups_delivered() -> None:
    data = {
        "trackResponse": {
            "shipment": [
                {
                    "package": [
                        {
                            "trackingNumber": "1Z999",
                            "currentStatus": {
                                "description": "Zugestellt",
                                "code": "011",
                            },
                            "deliveryDate": [{"type": "DEL", "date": "20261020"}],
                            "activity": [
                                {
                                    "location": {
                                        "address": {
                                            "city": "Berlin",
                                            "countryCode": "DE",
                                        }
                                    },
                                    "status": {
                                        "type": "D",
                                        "description": "Zugestellt ",
                                    },
                                    "date": "20261020",
                                    "time": "143000",
                                },
                                {
                                    "status": {"type": "I", "description": "Abfahrt"},
                                    "date": "20261019",
                                    "time": "220000",
                                },
                            ],
                        }
                    ]
                }
            ]
        }
    }
    result = parse_ups(data, "1Z999")
    assert result.status is ParcelStatus.DELIVERED
    assert result.status_text == "Zugestellt"
    assert result.location == "Berlin, DE"
    assert result.last_update is not None
    assert (result.last_update.hour, result.last_update.minute) == (14, 30)
    assert result.estimated_delivery is None


def test_ups_eta_and_not_found() -> None:
    data = {
        "trackResponse": {
            "shipment": [
                {
                    "package": [
                        {
                            "deliveryDate": [{"type": "SDD", "date": "20261022"}],
                            "deliveryTime": {"type": "EOD", "endTime": "190000"},
                            "activity": [
                                {
                                    "status": {
                                        "type": "M",
                                        "description": "Label erstellt",
                                    },
                                    "date": "20261020",
                                    "time": "100000",
                                }
                            ],
                        }
                    ]
                }
            ]
        }
    }
    result = parse_ups(data, "1Z")
    assert result.status is ParcelStatus.PRE_TRANSIT
    assert result.estimated_delivery is not None
    assert result.estimated_delivery.day == 22
    assert (
        parse_ups({"trackResponse": {"shipment": [{"warnings": []}]}}, "1Z").status
        is ParcelStatus.NOT_FOUND
    )


def test_dpd() -> None:
    data = {
        "parcellifecycleResponse": {
            "parcelLifeCycleData": {
                "statusInfo": [
                    {"status": "ACCEPTED", "isCurrentStatus": False},
                    {
                        "status": "AT_DELIVERY_DEPOT",
                        "label": "Im Zustelldepot",
                        "isCurrentStatus": True,
                    },
                    {"status": "DELIVERED", "isCurrentStatus": False},
                ],
                "scanInfo": {
                    "scan": [
                        {
                            "date": "2026-10-19T20:00:00",
                            "scanData": {"location": "Depot Aschaffenburg"},
                            "scanDescription": {"content": ["Paket übernommen"]},
                        },
                        {
                            "date": "2026-10-20T06:10:00",
                            "scanData": {"location": "Depot Berlin"},
                            "scanDescription": {"content": ["Im Zustelldepot"]},
                        },
                    ]
                },
            }
        }
    }
    result = parse_dpd(data, "0123")
    assert result.status is ParcelStatus.IN_TRANSIT
    assert result.status_text == "Im Zustelldepot"
    assert result.location == "Depot Berlin"
    assert result.events[0].timestamp is not None
    assert result.events[0].timestamp.utcoffset() is not None
    assert parse_dpd({}, "0123").status is ParcelStatus.NOT_FOUND


def test_gls() -> None:
    data = {
        "tuStatus": [
            {
                "progressBar": {
                    "statusInfo": "DELIVEREDPS",
                    "statusText": "Im PaketShop",
                },
                "history": [
                    {
                        "date": "2026-10-20",
                        "time": "11:02:00",
                        "address": {"city": "Neuenstein", "countryName": "Deutschland"},
                        "evtDscr": "Das Paket wurde im GLS PaketShop zugestellt.",
                    }
                ],
            }
        ]
    }
    result = parse_gls(data, "ZXY")
    assert result.status is ParcelStatus.READY_FOR_PICKUP
    assert result.location == "Neuenstein, Deutschland"
    assert result.status_text == "Im PaketShop"
    assert parse_gls({"tuStatus": []}, "ZXY").status is ParcelStatus.NOT_FOUND


def test_hermes() -> None:
    data = {
        "status": {
            "parcelStatus": "DELIVERED",
            "text": {"longText": "Die Sendung wurde zugestellt."},
        },
        "parcelHistory": [
            {
                "timestamp": "2026-10-20T12:00:00",
                "statusHistoryText": "Zugestellt",
                "city": "Hamburg",
            },
            {
                "timestamp": "2026-10-19T08:00:00",
                "statusHistoryText": "Sendung sortiert",
            },
        ],
    }
    result = parse_hermes(data, "H1")
    assert result.status is ParcelStatus.DELIVERED
    assert result.status_text == "Die Sendung wurde zugestellt."
    assert result.location == "Hamburg"
    assert parse_hermes({}, "H1").status is ParcelStatus.NOT_FOUND


def test_amazon() -> None:
    data = {
        "payload": {
            "trackingId": "UK0123",
            "summary": {"status": "OutForDelivery"},
            "promisedDeliveryDate": "2026-10-20T18:00:00Z",
            "eventHistory": [
                {"eventCode": "PickupDone", "eventTime": "2026-10-19T10:00:00Z"},
                {
                    "eventCode": "OutForDelivery",
                    "eventTime": "2026-10-20T07:00:00Z",
                    "location": {"city": "London", "countryCode": "GB"},
                },
            ],
        }
    }
    result = parse_amazon(data, "UK0123")
    assert result.status is ParcelStatus.OUT_FOR_DELIVERY
    assert result.status_text == "In Zustellung"
    assert result.location == "London, GB"
    assert isinstance(result.estimated_delivery, (datetime, date))


def test_amazon_logistics() -> None:
    # The tracker embeds progressTracker and eventHistory as JSON strings.
    data = {
        "progressTracker": json.dumps(
            {
                "summary": {
                    "status": "OutForDelivery",
                    "metadata": {
                        "expectedDeliveryDate": {"stringValue": "2026-10-20T19:00:00Z"}
                    },
                }
            }
        ),
        "eventHistory": json.dumps(
            {
                "eventHistory": [
                    {
                        "eventCode": "ArrivedAtCarrierFacility",
                        "eventTime": "2026-10-19T21:00:00Z",
                        "location": {"city": "Mönchengladbach", "countryCode": "DE"},
                    },
                    {
                        "eventCode": "OutForDelivery",
                        "eventTime": "2026-10-20T06:30:00Z",
                        "location": {"city": "Köln", "countryCode": "DE"},
                    },
                ]
            }
        ),
    }
    result = parse_amazon_logistics(data, "TBA123456789012")
    assert result.status is ParcelStatus.OUT_FOR_DELIVERY
    assert result.status_text == "In Zustellung"
    assert result.location == "Köln, DE"
    assert result.events[1].description == "Im Versandzentrum angekommen"
    assert result.estimated_delivery is not None


def test_amazon_logistics_status_from_events_and_empty() -> None:
    data = {
        "eventHistory": {
            "eventHistory": [
                {"eventCode": "Departed", "eventTime": "2026-10-19T10:00:00Z"},
                {"eventCode": "Delivered", "eventTime": "2026-10-20T12:00:00Z"},
            ]
        }
    }
    assert parse_amazon_logistics(data, "TBA1").status is ParcelStatus.DELIVERED
    empty = {"progressTracker": "{}", "eventHistory": '{"eventHistory": []}'}
    assert parse_amazon_logistics(empty, "TBA1").status is ParcelStatus.NOT_FOUND
