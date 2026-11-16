"""Coordinator for mailbox entries: parcels found in e-mails plus live status."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    CarrierClient,
    CarrierError,
    CarrierNotFoundError,
    ParcelStatus,
    TrackingResult,
    create_client,
)
from .const import (
    CONF_CARRIER,
    CONF_CARRIERS,
    CONF_FOLDERS,
    CONF_KEEP_DELIVERED_DAYS,
    CONF_LOOKBACK_DAYS,
    CONF_SCAN_INTERVAL,
    DEFAULT_FOLDERS,
    DEFAULT_KEEP_DELIVERED_DAYS,
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_MAIL_SCAN_INTERVAL,
    DOMAIN,
    KEYLESS_CARRIERS,
    MAIL_CARRIERS,
)
from .mail.imap import (
    FolderState,
    MailboxAuthError,
    MailboxClient,
    MailboxConfig,
    MailboxError,
)
from .mail.parser import MailParcel, is_relevant, parse_mail

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
# Parcels without news for this long are dropped (e.g. no delivery mail came).
STALE_DAYS = 30
# Removed parcels are remembered this long so a rescan does not bring them back.
IGNORE_DAYS = 60


@dataclass(slots=True)
class MailParcelView:
    """What the sensors show for one parcel."""

    key: str
    carrier: str
    tracking_number: str | None
    order_id: str | None
    name: str | None
    status: ParcelStatus
    status_text: str | None
    mail_subject: str | None
    mail_date: datetime | None
    live: TrackingResult | None


def parse_folders(value: str) -> list[str]:
    """Split the comma separated folder option."""
    return [f.strip() for f in value.split(",") if f.strip()] or [DEFAULT_FOLDERS]


def mail_group(parcel: MailParcel) -> str:
    """Return the carrier option a parcel falls under.

    Everything from an Amazon order counts as Amazon, even when DHL delivers.
    """
    if parcel.order_id or parcel.carrier in ("amazon", "amazon_logistics"):
        return "amazon"
    return parcel.carrier


class MailboxCoordinator(DataUpdateCoordinator[dict[str, MailParcelView]]):
    """Scans one mailbox and keeps the parcels it found."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        options = entry.options
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_mailbox_{entry.title}",
            update_interval=timedelta(
                minutes=options.get(CONF_SCAN_INTERVAL, DEFAULT_MAIL_SCAN_INTERVAL)
            ),
        )
        self._mailbox = MailboxClient(
            MailboxConfig(
                host=entry.data[CONF_HOST],
                port=entry.data[CONF_PORT],
                username=entry.data[CONF_USERNAME],
                password=entry.data[CONF_PASSWORD],
                folders=parse_folders(options.get(CONF_FOLDERS, DEFAULT_FOLDERS)),
            )
        )
        self._carriers = set(options.get(CONF_CARRIERS, MAIL_CARRIERS))
        self._lookback = timedelta(
            days=options.get(CONF_LOOKBACK_DAYS, DEFAULT_LOOKBACK_DAYS)
        )
        self._keep_delivered = timedelta(
            days=options.get(CONF_KEEP_DELIVERED_DAYS, DEFAULT_KEEP_DELIVERED_DAYS)
        )
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.mailbox_{entry.entry_id}"
        )
        self.packages: dict[str, dict[str, Any]] = {}
        self._ignored: dict[str, str] = {}
        self._folders: dict[str, FolderState] = {}
        self._live: dict[str, TrackingResult] = {}
        self._keyless_clients: dict[str, CarrierClient] = {}

    async def _async_setup(self) -> None:
        """Restore parcels found by earlier scans."""
        stored = await self._store.async_load() or {}
        self.packages = stored.get("packages", {})
        self._ignored = stored.get("ignored", {})
        self._folders = {
            name: FolderState.from_dict(state)
            for name, state in stored.get("folders", {}).items()
        }

    async def _async_update_data(self) -> dict[str, MailParcelView]:
        """Scan for new mails, then refresh the live status."""
        await self._async_scan()
        self._cleanup()
        await self._async_update_live()
        await self._store.async_save(self._serialize())
        return self._views()

    # Mail scanning -------------------------------------------------------

    def _scan(self, since: datetime) -> list[MailParcel]:
        """Fetch and parse new mails. Runs in the executor."""
        mails = self._mailbox.fetch_new(since.date(), self._folders, is_relevant)
        parcels = []
        for msg in mails:
            try:
                parcels.extend(parse_mail(msg))
            except Exception:  # noqa: BLE001 - one odd mail must not stop the scan
                _LOGGER.debug(
                    "Could not parse mail %s", msg.get("Subject"), exc_info=True
                )
        return parcels

    async def _async_scan(self) -> None:
        since = dt_util.now() - self._lookback
        try:
            parcels = await self.hass.async_add_executor_job(self._scan, since)
        except MailboxAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except MailboxError as err:
            if self.data is None and not self.packages:
                raise UpdateFailed(f"Mailbox not readable: {err}") from err
            # Keep showing what we have; the next scan catches up.
            _LOGGER.warning(
                "Reading mailbox %s failed: %s", self.config_entry.title, err
            )
            return
        for parcel in sorted(parcels, key=lambda p: p.date or dt_util.utcnow()):
            if mail_group(parcel) in self._carriers:
                self._merge(parcel)

    def _merge(self, parcel: MailParcel) -> None:
        if parcel.key in self._ignored:
            return
        date = (parcel.date or dt_util.utcnow()).isoformat()
        record = self.packages.get(parcel.key)
        if record is None:
            self.packages[parcel.key] = {
                "carrier": parcel.carrier,
                "tracking_number": parcel.tracking_number,
                "order_id": parcel.order_id,
                "name": parcel.name,
                "status": parcel.status.value,
                "subject": parcel.status_text,
                "updated": date,
                "first_seen": date,
                "delivered_at": date
                if parcel.status is ParcelStatus.DELIVERED
                else None,
            }
            return
        # Later mails may add what earlier ones lacked.
        if parcel.tracking_number and not record["tracking_number"]:
            record["tracking_number"] = parcel.tracking_number
            record["carrier"] = parcel.carrier
        if parcel.name and not record["name"]:
            record["name"] = parcel.name
        if _parse(date) >= _parse(record["updated"]):
            record.update(
                status=parcel.status.value, subject=parcel.status_text, updated=date
            )
            if parcel.status is ParcelStatus.DELIVERED:
                record["delivered_at"] = record.get("delivered_at") or date

    def _cleanup(self) -> None:
        now = dt_util.utcnow()
        for key, record in list(self.packages.items()):
            delivered = record.get("delivered_at")
            if (delivered and now - _parse(delivered) > self._keep_delivered) or (
                now - _parse(record["updated"]) > timedelta(days=STALE_DAYS)
            ):
                self._forget(key)
        for key, since in list(self._ignored.items()):
            if now - _parse(since) > timedelta(days=IGNORE_DAYS):
                del self._ignored[key]

    def _forget(self, key: str) -> None:
        self.packages.pop(key, None)
        self._live.pop(key, None)
        self._ignored[key] = dt_util.utcnow().isoformat()

    # Live status ---------------------------------------------------------

    def _client_for(self, carrier: str) -> CarrierClient | None:
        """Use a configured carrier entry, else a keyless client."""
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if (
                entry.state is ConfigEntryState.LOADED
                and entry.data.get(CONF_CARRIER) == carrier
            ):
                return entry.runtime_data.client
        if carrier not in KEYLESS_CARRIERS:
            return None
        if carrier not in self._keyless_clients:
            self._keyless_clients[carrier] = create_client(
                carrier, async_get_clientsession(self.hass), {}
            )
        return self._keyless_clients[carrier]

    async def _async_update_live(self) -> None:
        for key, record in self.packages.items():
            number = record.get("tracking_number")
            if not number or record.get("delivered_at"):
                continue
            client = self._client_for(record["carrier"])
            if client is None:
                continue
            try:
                result = await client.async_track(number)
            except CarrierNotFoundError:
                continue
            except CarrierError as err:
                _LOGGER.debug("Live status for %s failed: %s", number, err)
                continue
            if result.status in (ParcelStatus.NOT_FOUND, ParcelStatus.UNKNOWN):
                continue
            self._live[key] = result
            if result.status is ParcelStatus.DELIVERED:
                record["delivered_at"] = (
                    result.last_update or dt_util.utcnow()
                ).isoformat()

    # Views and actions ---------------------------------------------------

    def _views(self) -> dict[str, MailParcelView]:
        views = {}
        for key, record in self.packages.items():
            live = self._live.get(key)
            # A delivery confirmed by either source wins; otherwise the carrier
            # knows more than the last mail.
            if live and not record.get("delivered_at"):
                status, text = live.status, live.status_text
            else:
                status = (
                    ParcelStatus.DELIVERED
                    if record.get("delivered_at")
                    else ParcelStatus(record["status"])
                )
                text = live.status_text if live else record.get("subject")
            views[key] = MailParcelView(
                key=key,
                carrier=record["carrier"],
                tracking_number=record.get("tracking_number"),
                order_id=record.get("order_id"),
                name=record.get("name"),
                status=status,
                status_text=text,
                mail_subject=record.get("subject"),
                mail_date=_parse(record["updated"]),
                live=live,
            )
        return views

    def _serialize(self) -> dict[str, Any]:
        return {
            "packages": self.packages,
            "ignored": self._ignored,
            "folders": {name: state.as_dict() for name, state in self._folders.items()},
        }

    async def _async_publish(self) -> None:
        await self._store.async_save(self._serialize())
        self.async_set_updated_data(self._views())

    def find(self, value: str) -> str | None:
        """Return the key of a parcel by key, tracking or order number."""
        for key, record in self.packages.items():
            if value in (
                key.upper(),
                record.get("tracking_number"),
                record.get("order_id"),
            ):
                return key
        return None

    async def async_remove(self, value: str) -> bool:
        """Stop tracking a parcel; later mails about it are ignored."""
        if (key := self.find(value)) is None:
            return False
        self._forget(key)
        await self._async_publish()
        return True

    async def async_remove_delivered(self) -> None:
        """Stop tracking all delivered parcels."""
        delivered = [
            key
            for key, view in (self.data or {}).items()
            if view.status is ParcelStatus.DELIVERED
        ]
        for key in delivered:
            self._forget(key)
        if delivered:
            await self._async_publish()


def _parse(value: str) -> datetime:
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        return dt_util.utcnow()
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt_util.UTC)
