"""Sending texts: Twilio SMS, Viber and Telegram, plus a dry-run mode that just prints."""
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
    phone: str
    channel: str = "sms"          # sms | viber | telegram
    viber_id: str | None = None
    telegram_id: str | None = None


class Messenger:
    def __init__(self, settings: Settings, db):
        self.s = settings
        self.db = db

    async def send(self, rcpt: Recipient, body: str, job_id: int | None = None) -> None:
        channel = rcpt.channel
        if channel in ("viber", "telegram") and not (rcpt.viber_id if channel == "viber" else rcpt.telegram_id):
            # Viber/Telegram bots can only reach people who already opened the chat; fall back to SMS until they do.
            log.info("no %s id for %s, falling back to SMS", channel, rcpt.phone)
            channel = "sms"
        party = {"viber": rcpt.viber_id, "telegram": rcpt.telegram_id}.get(channel) or rcpt.phone

        if self.s.dry_run:
            provider = "dry-run"
            log.info("[dry-run %s -> %s] %s", channel, party, body)
        elif channel == "sms":
            await self._twilio(rcpt.phone, body)
            provider = "twilio"
        elif channel == "viber":
            await self._viber(rcpt.viber_id or "", body)
            provider = "viber"
        elif channel == "telegram":
            await self._telegram(rcpt.telegram_id or "", body)
            provider = "telegram"
        else:
            raise SendError(f"unknown channel {channel!r}")

        self.db.execute(
            "INSERT INTO messages(direction, channel, party, body, job_id, provider, created_at) "
            "VALUES ('out', ?, ?, ?, ?, ?, ?)",
            (channel, party, body, job_id, provider, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )

    def log_inbound(self, channel: str, party: str, body: str) -> None:
        self.db.execute(
            "INSERT INTO messages(direction, channel, party, body, provider, created_at) "
            "VALUES ('in', ?, ?, ?, ?, ?)",
            (channel, party, body, channel, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )

    async def _twilio(self, to: str, body: str) -> None:
        s = self.s
        if not (s.twilio_account_sid and s.twilio_auth_token and s.twilio_from):
            raise SendError("Twilio is not configured (TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM)")
        url = f"https://api.twilio.com/2010-04-01/Accounts/{s.twilio_account_sid}/Messages.json"
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(
                    url,
                    auth=(s.twilio_account_sid, s.twilio_auth_token),
                    data={"To": to, "From": s.twilio_from, "Body": body},
                )
        except httpx.HTTPError as exc:
            raise SendError(f"Twilio request failed: {exc}") from exc
        if r.status_code >= 400:
            raise SendError(f"Twilio error {r.status_code}: {r.text[:200]}")

    async def _viber(self, receiver: str, body: str) -> None:
        s = self.s
        if not s.viber_auth_token:
            raise SendError("Viber is not configured (VIBER_AUTH_TOKEN)")
        payload = {
            "receiver": receiver,
            "min_api_version": 1,
            "sender": {"name": s.viber_sender_name[:28]},
            "type": "text",
            "text": body,
        }
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(
                    "https://chatapi.viber.com/pa/send_message",
                    headers={"X-Viber-Auth-Token": s.viber_auth_token},
                    json=payload,
                )
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SendError(f"Viber request failed: {exc}") from exc
        if data.get("status") != 0:
            raise SendError(f"Viber error: {data.get('status_message', data)}")

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
