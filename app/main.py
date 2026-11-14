"""FastAPI app: inbound SMS/Viber webhooks, a few admin endpoints, and the background loop."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse

from pathlib import Path

from .agent import Agent
from .api import make_router
from .config import Settings
from .db import connect
from .messaging import Messenger

log = logging.getLogger("cleaner-agent")


def valid_twilio_signature(token: str, url: str, params: dict, signature: str) -> bool:
    data = url + "".join(k + str(params[k]) for k in sorted(params))
    digest = hmac.new(token.encode(), data.encode(), hashlib.sha1).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature or "")


def valid_viber_signature(token: str, body: bytes, signature: str) -> bool:
    digest = hmac.new(token.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature or "")


async def _run_forever(agent: Agent) -> None:
    last_sync = float("-inf")  # sync immediately on startup
    while True:
        try:
            due = time.monotonic() - last_sync >= agent.s.sync_interval_min * 60
            await agent.tick(sync=due)
            if due:
                last_sync = time.monotonic()
        except Exception:
            log.exception("background loop error")
        await asyncio.sleep(30)


def create_app(settings: Settings | None = None, agent: Agent | None = None, background: bool = True) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal agent
        if agent is None:
            db = connect(settings.db_path)
            agent = Agent(settings, db, Messenger(settings, db))
        app.state.agent = agent
        task = None
        if background:
            if settings.dry_run:
                log.warning("DRY_RUN is on: messages are printed, not sent")
            task = asyncio.create_task(_run_forever(agent))
        yield
        if task:
            task.cancel()

    app = FastAPI(title="Cleaner scheduling agent", lifespan=lifespan)
    app.state.settings = settings

    def admin(request: Request) -> None:
        token = settings.admin_token
        if not token:
            if settings.dry_run:
                return
            raise HTTPException(403, "Set ADMIN_TOKEN to use admin endpoints")
        if not hmac.compare_digest(request.headers.get("x-admin-token", ""), token):
            raise HTTPException(401, "Bad admin token")

    app.include_router(make_router(admin))

    @app.get("/", include_in_schema=False)
    async def ui():
        return FileResponse(Path(__file__).parent.parent / "static" / "index.html")

    @app.get("/health")
    async def health():
        return {"ok": True, "dry_run": settings.dry_run}

    @app.get("/jobs", dependencies=[Depends(admin)])
    async def jobs(request: Request):
        return request.app.state.agent.list_jobs()

    @app.get("/messages", dependencies=[Depends(admin)])
    async def messages(request: Request, limit: int = 50):
        rows = request.app.state.agent.q("SELECT * FROM messages ORDER BY id DESC LIMIT ?", (min(limit, 500),))
        return [dict(r) for r in rows]

    @app.post("/admin/sync", dependencies=[Depends(admin)])
    async def sync(request: Request):
        await request.app.state.agent.sync_calendars()
        return {"ok": True}

    @app.post("/admin/reload", dependencies=[Depends(admin)])
    async def reload(request: Request):
        await request.app.state.agent.reload_data(force=True)
        return {"ok": True}

    @app.post("/jobs/{job_id}/retry", dependencies=[Depends(admin)])
    async def retry(job_id: int, request: Request):
        if not await request.app.state.agent.retry_job(job_id):
            raise HTTPException(404, "Job not found or not retryable")
        return {"ok": True}

    async def _safe_inbound(ag: Agent, channel: str, sender: str, text: str) -> None:
        try:
            await ag.handle_inbound(channel, sender, text)
        except Exception:
            log.exception("failed to handle inbound %s message", channel)

    @app.post("/webhooks/twilio")
    async def twilio_webhook(request: Request, tasks: BackgroundTasks):
        form = await request.form()
        params = {k: str(v) for k, v in form.items()}
        if not settings.dry_run and settings.verify_webhooks:
            url = settings.public_base_url + "/webhooks/twilio"
            sig = request.headers.get("x-twilio-signature", "")
            if not settings.twilio_auth_token or not valid_twilio_signature(settings.twilio_auth_token, url, params, sig):
                raise HTTPException(403, "Invalid Twilio signature")
        tasks.add_task(_safe_inbound, request.app.state.agent, "sms", params.get("From", ""), params.get("Body", ""))
        return Response("<Response/>", media_type="application/xml")

    @app.post("/webhooks/viber")
    async def viber_webhook(request: Request, tasks: BackgroundTasks):
        raw = await request.body()
        if not settings.dry_run and settings.verify_webhooks:
            sig = request.headers.get("x-viber-content-signature", "")
            if not settings.viber_auth_token or not valid_viber_signature(settings.viber_auth_token, raw, sig):
                raise HTTPException(403, "Invalid Viber signature")
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            raise HTTPException(400, "Bad JSON")
        event = data.get("event")
        if event == "conversation_started":
            return {
                "sender": {"name": settings.viber_sender_name[:28]},
                "type": "text",
                "text": "Hi! Please send your phone number exactly as registered with us (e.g. +306912345678) to link this chat.",
            }
        if event == "message":
            text = (data.get("message") or {}).get("text", "")
            sender = (data.get("sender") or {}).get("id", "")
            if sender and text:
                tasks.add_task(_safe_inbound, request.app.state.agent, "viber", sender, text)
        return {"status": 0}

    return app


app = create_app()
