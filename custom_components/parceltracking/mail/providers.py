"""IMAP settings of common mail providers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MailProvider:
    """Where a provider's IMAP server is and what its login needs."""

    host: str
    domains: tuple[str, ...]
    # Which hint to show when the login is rejected:
    # app_password, enable_imap, mail_password, microsoft or generic.
    login_hint: str = "generic"
    port: int = 993


PROVIDERS: dict[str, MailProvider] = {
    "gmail": MailProvider(
        "imap.gmail.com", ("gmail.com", "googlemail.com"), "app_password"
    ),
    "outlook": MailProvider(
        "outlook.office365.com",
        (
            "outlook.com",
            "outlook.de",
            "hotmail.com",
            "hotmail.de",
            "live.com",
            "live.de",
            "msn.com",
        ),
        "microsoft",
    ),
    "webde": MailProvider("imap.web.de", ("web.de",), "enable_imap"),
    "gmx": MailProvider(
        "imap.gmx.net", ("gmx.de", "gmx.net", "gmx.at", "gmx.ch"), "enable_imap"
    ),
    "tonline": MailProvider(
        "secureimap.t-online.de", ("t-online.de", "magenta.de"), "mail_password"
    ),
    "icloud": MailProvider(
        "imap.mail.me.com", ("icloud.com", "me.com", "mac.com"), "app_password"
    ),
    "yahoo": MailProvider(
        "imap.mail.yahoo.com", ("yahoo.com", "yahoo.de"), "app_password"
    ),
    "ionos": MailProvider("imap.ionos.de", ("ionos.de",)),
    "posteo": MailProvider("posteo.de", ("posteo.de", "posteo.net")),
    "mailbox_org": MailProvider("imap.mailbox.org", ("mailbox.org",)),
    "freenet": MailProvider("mx.freenet.de", ("freenet.de",)),
}

AUTO = "auto"
CUSTOM = "custom"


def provider_for(address: str) -> str | None:
    """Return the provider of an e-mail address, if it is a known one."""
    domain = address.rpartition("@")[2].strip().lower()
    for key, provider in PROVIDERS.items():
        if domain in provider.domains:
            return key
    return None
