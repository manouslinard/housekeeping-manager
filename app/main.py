"""FastAPI app: Telegram (polling or webhook), a few admin endpoints, and the background loop."""
from __future__ import annotations

import asyncio
import hmac
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
from .llm import make_llm
from .messaging import Messenger
from .telegram import parse_update, poll_forever, set_webhook
from .view import router as web_router

log = logging.getLogger("cleaner-agent")


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
            agent = Agent(settings, db, Messenger(settings, db), llm=make_llm(settings))
        app.state.agent = agent
        task = tg_task = None
        if background:
            if settings.dry_run:
                log.warning("DRY_RUN is on: messages are printed, not sent")
            task = asyncio.create_task(_run_forever(agent))
            if settings.telegram_bot_token:
                if settings.telegram_webhook_secret and settings.public_base_url:
                    await set_webhook(settings.telegram_bot_token, settings.public_base_url + "/webhooks/telegram",
                                      settings.telegram_webhook_secret)
                elif not settings.telegram_webhook_secret:  # no webhook configured: long polling needs no public URL
                    tg_task = asyncio.create_task(poll_forever(agent, settings.telegram_bot_token))
            if agent.llm is None:
                log.warning("Gemini is off (no GEMINI_API_KEY or LLM_ENABLED=0): free-text replies get the fixed help text")
        yield
        for t_ in (task, tg_task):
            if t_:
                t_.cancel()

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
    app.include_router(web_router)  # /job/{id}: HTML page for a cleaning job

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

    async def _safe_inbound(ag: Agent, chat_id: str, text: str) -> None:
        try:
            await ag.handle_inbound(chat_id, text)
        except Exception:
            log.exception("failed to handle inbound Telegram message")

    @app.post("/webhooks/telegram")
    async def telegram_webhook(request: Request, tasks: BackgroundTasks):
        secret = settings.telegram_webhook_secret
        got = request.headers.get("x-telegram-bot-api-secret-token", "")
        if secret:
            if not hmac.compare_digest(got, secret):
                raise HTTPException(403, "Invalid Telegram secret")
        elif not settings.dry_run:
            raise HTTPException(403, "Set TELEGRAM_WEBHOOK_SECRET to use the Telegram webhook")
        try:
            update = await request.json()
        except ValueError:
            raise HTTPException(400, "Bad JSON")
        msg = parse_update(update) if isinstance(update, dict) else None
        if msg:
            tasks.add_task(_safe_inbound, request.app.state.agent, msg["chat_id"], msg["text"] or msg["contact_phone"])
        return {"ok": True}

    return app


app = create_app()
