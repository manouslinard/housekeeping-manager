"""Sending messages on Telegram, plus a dry-run mode that just prints."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from .config import Settings

log = logging.getLogger(__name__)


class SendError(Exception):
    pass


@dataclass
class Recipient:
    telegram_id: str | None = None   # Telegram chat id, known once the person has pressed Start
    phone: str = ""                  # only used to label the log line in dry-run, before the chat is linked


class Messenger:
    def __init__(self, settings: Settings, db):
        self.s = settings
        self.db = db

    async def send(self, rcpt: Recipient, body: str, job_id: int | None = None) -> None:
        # Bots can't message a phone number: the person must open the bot and press Start first.
        party = rcpt.telegram_id or rcpt.phone
        if self.s.dry_run:
            provider = "dry-run"
            log.info("[dry-run telegram -> %s] %s", party, body)
        elif not rcpt.telegram_id:
            raise SendError(f"{rcpt.phone or 'recipient'} has not linked Telegram yet")
        else:
            await self._telegram(rcpt.telegram_id, body)
            provider = "telegram"

        self.db.execute(
            "INSERT INTO messages(direction, channel, party, body, job_id, provider, created_at) "
            "VALUES ('out', 'telegram', ?, ?, ?, ?, ?)",
            (party, body, job_id, provider, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )

    def log_inbound(self, party: str, body: str) -> None:
        self.db.execute(
            "INSERT INTO messages(direction, channel, party, body, provider, created_at) "
            "VALUES ('in', 'telegram', ?, ?, 'telegram', ?)",
            (party, body, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )

    async def _telegram(self, chat_id: str, body: str) -> None:
        s = self.s
        if not s.telegram_bot_token:
            raise SendError("Telegram is not configured (TELEGRAM_BOT_TOKEN)")
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(
                    f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage",
                    json={"chat_id": chat_id, "text": body},
                )
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SendError(f"Telegram request failed: {type(exc).__name__}") from exc
        if not data.get("ok"):
            raise SendError(f"Telegram error: {data.get('description', data)}")
