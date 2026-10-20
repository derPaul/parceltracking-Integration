"""Read tracking numbers from an image with Home Assistant's AI Task."""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
)

from .api.detect import detect_carriers
from .const import CARRIER_NAMES, DOMAIN, normalize_tracking_number

_LOGGER = logging.getLogger(__name__)

INSTRUCTIONS = """\
Find all parcel tracking numbers (German: Sendungsnummer) in the attached image.
It may show a shipping label, a delivery note, a shipping confirmation e-mail or
a screenshot of a shop or carrier app. Possible carriers: DHL / Deutsche Post,
DPD, GLS, Hermes, UPS and Amazon.

Rules:
- Return every tracking number exactly as printed, without spaces.
- If a number is printed below a barcode, use the printed digits.
- Ignore order numbers, customer numbers, invoice numbers, postal codes,
  phone numbers, dates and prices.
- If the carrier is recognizable (logo or text), set carrier to one of:
  amazon_logistics (delivered by Amazon itself, e.g. TBA... or DE + 10 digits),
  amazon (Amazon Shipping for sellers), dhl, dpd, gls, hermes, ups.
  Otherwise use unknown.
- If there is no tracking number, return an empty list.
"""

STRUCTURE = vol.Schema(
    {
        vol.Required(
            "tracking_numbers", description="All tracking numbers found"
        ): TextSelector(TextSelectorConfig(multiple=True)),
        vol.Required(
            "carrier", description="Carrier shown in the image"
        ): SelectSelector(SelectSelectorConfig(options=[*CARRIER_NAMES, "unknown"])),
    }
)

# Fallback when a model answers with free text instead of the structure.
_CANDIDATE = re.compile(r"\b[0-9A-Z][0-9A-Z ]{6,30}[0-9A-Z]\b")


def _clean(raw_numbers: Any, hint: str | None) -> list[str]:
    """Normalize and drop everything that does not look like a tracking number."""
    if isinstance(raw_numbers, str):
        raw_numbers = _CANDIDATE.findall(raw_numbers.upper())
    numbers: list[str] = []
    for raw in raw_numbers or []:
        number = normalize_tracking_number(str(raw))
        if number in numbers or not 8 <= len(number) <= 35:
            continue
        # Keep unknown formats only if the model was sure about the carrier.
        if detect_carriers(number) or hint:
            numbers.append(number)
    return numbers


async def async_extract_tracking_numbers(
    hass: HomeAssistant, image: dict[str, Any], ai_task_entity: str | None
) -> tuple[list[str], str | None]:
    """Return the tracking numbers in an image and the recognized carrier."""
    if "ai_task" not in hass.config.components:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="ai_task_unavailable"
        )
    # Imported lazily: ai_task is only an optional dependency.
    from homeassistant.components import ai_task  # noqa: PLC0415

    try:
        result = await ai_task.async_generate_data(
            hass,
            task_name="Parcel tracking number",
            entity_id=ai_task_entity,
            instructions=INSTRUCTIONS,
            structure=STRUCTURE,
            attachments=[
                {
                    "media_content_id": image["media_content_id"],
                    "media_content_type": image.get("media_content_type", "image/*"),
                }
            ],
        )
    except HomeAssistantError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="image_analysis_failed",
            translation_placeholders={"error": str(err)},
        ) from err

    data = result.data
    _LOGGER.debug("AI Task answered: %s", data)
    if isinstance(data, dict):
        raw_numbers = data.get("tracking_numbers")
        hint = data.get("carrier")
    else:
        raw_numbers, hint = data, None
    if hint not in CARRIER_NAMES:
        hint = None
    return _clean(raw_numbers, hint), hint
