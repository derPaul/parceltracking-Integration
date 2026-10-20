"""Config and options flow for the Parcel Tracking integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import CarrierAuthError, CarrierError, create_client
from .api.detect import detect_carriers
from .const import (
    AMAZON_BUSINESS_IDS,
    AMAZON_REGIONS,
    CARRIER_NAMES,
    CONF_API_KEY,
    CONF_BUSINESS_ID,
    CONF_CARRIER,
    CONF_CARRIER_ID,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_NAME,
    CONF_PACKAGES,
    CONF_REFRESH_TOKEN,
    CONF_REGION,
    CONF_SCAN_INTERVAL,
    CONF_TRACKING_NUMBER,
    DEFAULT_AMAZON_CARRIER_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    normalize_tracking_number,
)
from .coordinator import ParcelTrackingConfigEntry

_LOGGER = logging.getLogger(__name__)

_SECRET = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
_TEXT = TextSelector()


def credential_schema(carrier: str) -> vol.Schema:
    """Return the credential fields needed by a carrier."""
    if carrier in ("dhl", "gls"):
        return vol.Schema({vol.Required(CONF_API_KEY): _SECRET})
    if carrier == "hermes":
        return vol.Schema({vol.Optional(CONF_API_KEY): _SECRET})
    if carrier == "ups":
        return vol.Schema(
            {
                vol.Required(CONF_CLIENT_ID): _TEXT,
                vol.Required(CONF_CLIENT_SECRET): _SECRET,
            }
        )
    if carrier == "amazon":
        return vol.Schema(
            {
                vol.Required(CONF_CLIENT_ID): _TEXT,
                vol.Required(CONF_CLIENT_SECRET): _SECRET,
                vol.Required(CONF_REFRESH_TOKEN): _SECRET,
                vol.Required(CONF_REGION, default="eu"): SelectSelector(
                    SelectSelectorConfig(
                        options=AMAZON_REGIONS, translation_key=CONF_REGION
                    )
                ),
                vol.Required(
                    CONF_BUSINESS_ID, default=AMAZON_BUSINESS_IDS[0]
                ): SelectSelector(
                    SelectSelectorConfig(options=AMAZON_BUSINESS_IDS, custom_value=True)
                ),
                vol.Required(CONF_CARRIER_ID, default=DEFAULT_AMAZON_CARRIER_ID): _TEXT,
            }
        )
    return vol.Schema({})


async def _async_validate(
    hass, carrier: str, data: Mapping[str, Any]
) -> dict[str, str]:
    """Try the credentials; return form errors."""
    client = create_client(carrier, async_get_clientsession(hass), data)
    try:
        await client.async_validate_credentials()
    except CarrierAuthError:
        return {"base": "invalid_auth"}
    except CarrierError as err:
        _LOGGER.debug("Credential check for %s failed: %s", carrier, err)
        return {"base": "cannot_connect"}
    except Exception:
        _LOGGER.exception("Unexpected error while validating %s credentials", carrier)
        return {"base": "unknown"}
    return {}


class ParcelTrackingConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up one config entry per carrier."""

    VERSION = 1

    _carrier: str

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ParcelTrackingConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return ParcelTrackingOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick a carrier."""
        if user_input is not None:
            self._carrier = user_input[CONF_CARRIER]
            await self.async_set_unique_id(self._carrier)
            self._abort_if_unique_id_configured()
            return await self.async_step_credentials()

        configured = {entry.unique_id for entry in self._async_current_entries()}
        options = [
            SelectOptionDict(value=key, label=name)
            for key, name in CARRIER_NAMES.items()
            if key not in configured
        ]
        if not options:
            return self.async_abort(reason="all_configured")
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CARRIER): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.LIST
                        )
                    )
                }
            ),
        )

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for and validate the carrier credentials."""
        schema = credential_schema(self._carrier)
        errors: dict[str, str] = {}
        if user_input is None and not schema.schema:
            user_input = {}
        if user_input is not None:
            errors = await _async_validate(self.hass, self._carrier, user_input)
            if not errors:
                return self.async_create_entry(
                    title=CARRIER_NAMES[self._carrier],
                    data={CONF_CARRIER: self._carrier, **user_input},
                    options={
                        CONF_PACKAGES: [],
                        CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                    },
                )
        return self.async_show_form(
            step_id="credentials",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
            description_placeholders={"carrier": CARRIER_NAMES[self._carrier]},
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication after the credentials were rejected."""
        self._carrier = entry_data[CONF_CARRIER]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for new credentials."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await _async_validate(self.hass, self._carrier, user_input)
            if not errors:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(), data_updates=user_input
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=credential_schema(self._carrier),
            errors=errors,
            description_placeholders={"carrier": CARRIER_NAMES[self._carrier]},
        )


class ParcelTrackingOptionsFlow(OptionsFlow):
    """Manage packages and polling interval."""

    # Number the user already confirmed despite the carrier mismatch warning.
    _confirmed_number: str | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the menu."""
        menu = ["add_package", "settings"]
        if self._packages:
            menu.insert(1, "remove_package")
        return self.async_show_menu(step_id="init", menu_options=menu)

    @property
    def _packages(self) -> list[dict[str, Any]]:
        return list(self.config_entry.options.get(CONF_PACKAGES, []))

    def _save(self, **changes: Any) -> ConfigFlowResult:
        return self.async_create_entry(data={**self.config_entry.options, **changes})

    async def async_step_add_package(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a package."""
        errors: dict[str, str] = {}
        placeholders = {"detected": ""}
        if user_input is not None:
            number = normalize_tracking_number(user_input[CONF_TRACKING_NUMBER])
            packages = self._packages
            detected = detect_carriers(number)
            if not number:
                errors[CONF_TRACKING_NUMBER] = "invalid_tracking_number"
            elif any(p[CONF_TRACKING_NUMBER] == number for p in packages):
                errors[CONF_TRACKING_NUMBER] = "already_tracked"
            elif (
                detected
                and self.config_entry.data[CONF_CARRIER] not in detected
                and number != self._confirmed_number
            ):
                # Warn once; submitting the same number again adds it anyway.
                self._confirmed_number = number
                errors[CONF_TRACKING_NUMBER] = "wrong_carrier"
                placeholders["detected"] = ", ".join(CARRIER_NAMES[c] for c in detected)
            else:
                packages.append(
                    {
                        CONF_TRACKING_NUMBER: number,
                        CONF_NAME: (user_input.get(CONF_NAME) or "").strip() or None,
                    }
                )
                return self._save(**{CONF_PACKAGES: packages})
        return self.async_show_form(
            step_id="add_package",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_TRACKING_NUMBER): _TEXT,
                        vol.Optional(CONF_NAME): _TEXT,
                    }
                ),
                user_input,
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_remove_package(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove one or more packages."""
        if user_input is not None:
            remove = set(user_input[CONF_PACKAGES])
            return self._save(
                **{
                    CONF_PACKAGES: [
                        p
                        for p in self._packages
                        if p[CONF_TRACKING_NUMBER] not in remove
                    ]
                }
            )
        options = [
            SelectOptionDict(
                value=p[CONF_TRACKING_NUMBER],
                label=(
                    f"{p[CONF_NAME]} ({p[CONF_TRACKING_NUMBER]})"
                    if p.get(CONF_NAME)
                    else p[CONF_TRACKING_NUMBER]
                ),
            )
            for p in self._packages
        ]
        return self.async_show_form(
            step_id="remove_package",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PACKAGES): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the polling interval."""
        if user_input is not None:
            return self._save(
                **{CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL])}
            )
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=5,
                            max=1440,
                            step=5,
                            unit_of_measurement="min",
                            mode=NumberSelectorMode.BOX,
                        )
                    )
                }
            ),
        )
