"""Tests for the IMAP client against a fake server."""

from __future__ import annotations

import imaplib
from datetime import date
from unittest.mock import patch

import pytest

from custom_components.parceltracking.mail.imap import (
    FolderState,
    MailboxAuthError,
    MailboxClient,
    MailboxConfig,
    MailboxFolderError,
    encode_folder,
)
from custom_components.parceltracking.mail.parser import is_relevant

SHIPPING = (
    b"From: DHL <noreply@dhl.de>\r\nSubject: Ihre Sendung kommt heute\r\n"
    b"Date: Mon, 16 Nov 2026 10:00:00 +0100\r\n\r\nSendungsnummer: 00340434161094042557\r\n"
)
NEWSLETTER = (
    b"From: News <news@shop.example>\r\nSubject: Angebote der Woche\r\n"
    b"Date: Mon, 16 Nov 2026 09:00:00 +0100\r\n\r\nJetzt sparen!\r\n"
)


def _headers(raw: bytes) -> bytes:
    return raw.split(b"\r\n\r\n")[0] + b"\r\n\r\n"


class FakeIMAP:
    """Just enough of imaplib.IMAP4 for MailboxClient."""

    mails = {101: SHIPPING, 102: NEWSLETTER}
    fetched: list[str] = []
    uid_after_literal = False

    def __init__(self, *args, **kwargs) -> None:
        self.selected = None

    def login(self, user: str, password: str) -> None:
        if password != "geheim":
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")

    def select(self, folder: str, readonly: bool = False):
        self.selected = folder
        return ("OK", [b"2"]) if folder == '"INBOX"' else ("NO", [b"no such folder"])

    def response(self, code: str):
        return code, [b"42"]

    def uid(self, command: str, *args):
        if command == "search":
            assert args[1:] == ("SINCE", "02-Nov-2026")
            return "OK", [b" ".join(str(uid).encode() for uid in self.mails)]
        uids, query = args
        FakeIMAP.fetched.append(f"{uids} {query}")
        data = []
        for uid in (int(u) for u in uids.split(",")):
            body = (
                self.mails[uid] if "BODY.PEEK[]" in query else _headers(self.mails[uid])
            )
            if self.uid_after_literal:
                data += [(b"1 (BODY[] {%d}" % len(body), body), b" UID %d)" % uid]
            else:
                data += [(b"1 (UID %d BODY[] {%d}" % (uid, len(body)), body), b")"]
        return "OK", data

    def logout(self) -> None:
        pass


def _client(
    password: str = "geheim", folders: list[str] | None = None
) -> MailboxClient:
    return MailboxClient(
        MailboxConfig("imap.example.com", 993, "ich", password, folders or ["INBOX"])
    )


@pytest.fixture(autouse=True)
def fake_imap():
    FakeIMAP.fetched = []
    FakeIMAP.uid_after_literal = False
    with patch.object(imaplib, "IMAP4_SSL", FakeIMAP):
        yield


@pytest.mark.parametrize("uid_after_literal", [False, True])
def test_fetch_new_downloads_only_relevant_and_unseen(uid_after_literal: bool) -> None:
    FakeIMAP.uid_after_literal = uid_after_literal
    states: dict[str, FolderState] = {}
    mails = _client().fetch_new(date(2026, 11, 2), states, is_relevant)
    assert [m["Subject"] for m in mails] == ["Ihre Sendung kommt heute"]
    # Headers for both, the full mail only for the shipping mail.
    assert FakeIMAP.fetched == [
        "102,101 (BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])",
        "101 (BODY.PEEK[])",
    ]
    assert states["INBOX"].uidvalidity == 42
    assert states["INBOX"].seen == {101, 102}

    FakeIMAP.fetched = []
    assert _client().fetch_new(date(2026, 11, 2), states, is_relevant) == []
    assert FakeIMAP.fetched == []


def test_uidvalidity_change_rescans() -> None:
    states = {"INBOX": FolderState(uidvalidity=7, seen={101, 102})}
    mails = _client().fetch_new(date(2026, 11, 2), states, is_relevant)
    assert len(mails) == 1


def test_errors() -> None:
    with pytest.raises(MailboxAuthError):
        _client(password="falsch").test()
    with pytest.raises(MailboxFolderError):
        _client(folders=["Gibtsnicht"]).test()
    _client().test()


@pytest.mark.parametrize(
    ("name", "encoded"),
    [
        ("INBOX", "INBOX"),
        ("Einkäufe", "Eink&AOQ-ufe"),
        ("[Gmail]/Alle Nachrichten", "[Gmail]/Alle Nachrichten"),
        ("Q&A", "Q&-A"),
        ("Bestellungen/Übersicht", "Bestellungen/&ANw-bersicht"),
    ],
)
def test_encode_folder(name: str, encoded: str) -> None:
    assert encode_folder(name) == encoded
