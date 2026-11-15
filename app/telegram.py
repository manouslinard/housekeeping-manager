"""Receive Telegram messages: long polling (no public URL needed) or the webhook in main.py."""
from __future__ import annotations

import asyncio
import logging

import httpx

log = logging.getLogger(__name__)


def parse_update(update: dict) -> dict | None:
    """Extract what the agent needs from one Telegram update, or None to skip it."""
    msg = update.get("message") or {}
    chat = msg.get("chat") or {}
    if not chat or chat.get("type") != "private":
        return None  # only 1:1 chats with the bot
    contact = msg.get("contact") or {}
    out = {
        "chat_id": str(chat["id"]),
        "from_id": str((msg.get("from") or {}).get("id", "")),
        "text": (msg.get("text") or "").strip(),
        "contact_phone": contact.get("phone_number", ""),
        "contact_user_id": str(contact.get("user_id", "")) if contact else "",
    }
    if not out["text"] and not out["contact_phone"]:
        return None  # stickers, photos, ...
    return out


async def poll_forever(agent, token: str) -> None:
    base = f"https://api.telegram.org/bot{token}"
    offset = 0
    async with httpx.AsyncClient(timeout=70) as c:
        try:
            await c.post(f"{base}/deleteWebhook")  # polling and webhooks can't be used together
        except httpx.HTTPError:
            pass
        log.info("Telegram bot is listening (long polling)")
        while True:
            try:
                r = await c.get(f"{base}/getUpdates", params={"offset": offset, "timeout": 50})
                data = r.json()
                if not data.get("ok"):
                    log.error("Telegram getUpdates failed: %s", data.get("description"))
                    await asyncio.sleep(10)
                    continue
                for upd in data.get("result", []):
                    offset = upd["update_id"] + 1
                    parsed = parse_update(upd)
                    if parsed:
                        asyncio.create_task(_safe_handle(agent, parsed))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # network blip: wait and retry
                log.warning("Telegram polling error: %s", type(exc).__name__)
                await asyncio.sleep(5)


async def _safe_handle(agent, msg: dict) -> None:
    try:
        # a shared contact arrives without text: its phone number is what links the chat
        await agent.handle_inbound(msg["chat_id"], msg["text"] or msg["contact_phone"])
    except Exception:
        log.exception("failed to handle Telegram message")


async def bot_username(token: str) -> str:
    """The bot's @username (needed for t.me links), from the Bot API getMe call. "" on failure."""
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(f"https://api.telegram.org/bot{token}/getMe")
        return r.json().get("result", {}).get("username", "")
    except (httpx.HTTPError, ValueError):
        return ""


async def set_webhook(token: str, url: str, secret: str) -> None:
    """Register POST <url> with Telegram (call once on startup when a webhook secret is configured)."""
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"https://api.telegram.org/bot{token}/setWebhook", json={"url": url, "secret_token": secret})
        if not r.json().get("ok"):
            log.error("Telegram setWebhook failed: %s", r.text[:200])
    except (httpx.HTTPError, ValueError) as exc:
        log.error("Telegram setWebhook failed: %s", type(exc).__name__)
