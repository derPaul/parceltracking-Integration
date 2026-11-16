"""Read new mails from an IMAP mailbox. Blocking; run it in an executor."""

from __future__ import annotations

import base64
import email
import email.policy
import imaplib
import re
import ssl
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from email.message import Message

TIMEOUT = 30
BATCH = 25
# Upper bound per scan so a first run over a huge mailbox stays bounded.
MAX_MAILS = 300

_UID = re.compile(rb"UID (\d+)")


class MailboxError(Exception):
    """The mailbox could not be read."""


class MailboxAuthError(MailboxError):
    """The mail server rejected the login."""


class MailboxFolderError(MailboxError):
    """A configured folder does not exist."""


@dataclass
class FolderState:
    """What was already looked at in one folder."""

    uidvalidity: int | None = None
    seen: set[int] = field(default_factory=set)

    def as_dict(self) -> dict:
        """Return a JSON serializable dict."""
        return {"uidvalidity": self.uidvalidity, "seen": sorted(self.seen)}

    @classmethod
    def from_dict(cls, data: dict | None) -> FolderState:
        """Restore from as_dict()."""
        data = data or {}
        return cls(data.get("uidvalidity"), set(data.get("seen") or []))


@dataclass
class MailboxConfig:
    """Connection settings of a mailbox."""

    host: str
    port: int
    username: str
    password: str
    folders: list[str]
    use_ssl: bool = True


def encode_folder(name: str) -> str:
    """Encode a folder name in IMAP's modified UTF-7 (RFC 3501), e.g. Einkäufe."""
    result: list[str] = []
    pending: list[str] = []

    def flush() -> None:
        if pending:
            raw = "".join(pending).encode("utf-16-be")
            encoded = base64.b64encode(raw).decode().rstrip("=").replace("/", ",")
            result.append(f"&{encoded}-")
            pending.clear()

    for char in name:
        if 0x20 <= ord(char) <= 0x7E:
            flush()
            result.append("&-" if char == "&" else char)
        else:
            pending.append(char)
    flush()
    return "".join(result)


def _quote(folder: str) -> str:
    folder = encode_folder(folder)
    return '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _since(day: date) -> str:
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    return f"{day.day:02d}-{months[day.month - 1]}-{day.year}"


class MailboxClient:
    """Fetches new, relevant mails from an IMAP server."""

    def __init__(self, config: MailboxConfig) -> None:
        """Initialize the client."""
        self._config = config

    def _connect(self) -> imaplib.IMAP4:
        cfg = self._config
        try:
            if cfg.use_ssl:
                conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(
                    cfg.host,
                    cfg.port,
                    ssl_context=ssl.create_default_context(),
                    timeout=TIMEOUT,
                )
            else:
                conn = imaplib.IMAP4(cfg.host, cfg.port, timeout=TIMEOUT)
                conn.starttls(ssl_context=ssl.create_default_context())
        except (OSError, imaplib.IMAP4.error) as err:
            raise MailboxError(f"Cannot connect to {cfg.host}: {err}") from err
        try:
            conn.login(cfg.username, cfg.password)
        except imaplib.IMAP4.error as err:
            _logout(conn)
            raise MailboxAuthError(str(err)) from err
        return conn

    def test(self) -> None:
        """Log in and open every folder; raise MailboxError on failure."""
        conn = self._connect()
        try:
            for folder in self._config.folders:
                typ, _ = conn.select(_quote(folder), readonly=True)
                if typ != "OK":
                    raise MailboxFolderError(f"Folder {folder} not found")
        finally:
            _logout(conn)

    def fetch_new(
        self,
        since: date,
        states: dict[str, FolderState],
        is_relevant: Callable[[Message], bool],
    ) -> list[Message]:
        """Return relevant mails since a day that were not seen before.

        Updates states in place. Only headers are downloaded for irrelevant
        mails; full mails only when is_relevant(headers) says so.
        """
        conn = self._connect()
        mails: list[Message] = []
        try:
            for folder in self._config.folders:
                state = states.setdefault(folder, FolderState())
                mails.extend(
                    self._fetch_folder(conn, folder, since, state, is_relevant)
                )
        except (OSError, imaplib.IMAP4.error) as err:
            raise MailboxError(str(err)) from err
        finally:
            _logout(conn)
        return mails

    def _fetch_folder(
        self,
        conn: imaplib.IMAP4,
        folder: str,
        since: date,
        state: FolderState,
        is_relevant: Callable[[Message], bool],
    ) -> list[Message]:
        typ, _ = conn.select(_quote(folder), readonly=True)
        if typ != "OK":
            raise MailboxFolderError(f"Folder {folder} not found")
        uidvalidity = _uidvalidity(conn)
        if uidvalidity != state.uidvalidity:
            state.uidvalidity = uidvalidity
            state.seen = set()

        typ, data = conn.uid("search", None, "SINCE", _since(since))
        if typ != "OK":
            raise MailboxError(f"Search in {folder} failed")
        in_window = {int(uid) for uid in (data[0] or b"").split()}
        # Forget UIDs that left the window so the state stays small.
        state.seen &= in_window
        new = sorted(in_window - state.seen, reverse=True)[:MAX_MAILS]

        relevant = [
            uid
            for uid, headers in _fetch(
                conn, new, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])"
            )
            if is_relevant(headers)
        ]
        mails = [msg for _, msg in _fetch(conn, relevant, "(BODY.PEEK[])")]
        state.seen.update(new)
        return mails


def _fetch(
    conn: imaplib.IMAP4, uids: Iterable[int], query: str
) -> list[tuple[int, Message]]:
    uids = list(uids)
    result = []
    for start in range(0, len(uids), BATCH):
        chunk = ",".join(str(uid) for uid in uids[start : start + BATCH])
        typ, data = conn.uid("fetch", chunk, query)
        if typ != "OK":
            raise MailboxError("Fetching mails failed")
        for index, item in enumerate(data):
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            # Most servers send the UID before the literal, some after it.
            match = _UID.search(item[0])
            if (
                not match
                and index + 1 < len(data)
                and isinstance(data[index + 1], bytes)
            ):
                match = _UID.search(data[index + 1])
            if match:
                msg = email.message_from_bytes(item[1], policy=email.policy.compat32)
                result.append((int(match.group(1)), msg))
    return result


def _uidvalidity(conn: imaplib.IMAP4) -> int | None:
    _, data = conn.response("UIDVALIDITY")
    try:
        return int(data[0]) if data and data[0] else None
    except (TypeError, ValueError):
        return None


def _logout(conn: imaplib.IMAP4) -> None:
    try:
        conn.logout()
    except (OSError, imaplib.IMAP4.error):
        pass
