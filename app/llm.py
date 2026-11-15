"""Gemini: reads a cleaner's free-text reply and picks the best replacement cleaner.

Gemini only ever chooses among options our code hands it; the Agent validates every answer and falls back to the
deterministic behaviour (parse_reply / nearest cleaner) on any failure. It never talks to Telegram itself.
"""
from __future__ import annotations

import json
import logging

import httpx

from .config import Settings

log = logging.getLogger(__name__)

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class LLMError(Exception):
    pass


REPLY_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "intent": {"type": "STRING", "enum": ["yes", "no", "done", "unavailable", "status", "answers", "other"]},
        "job_id": {"type": "INTEGER", "nullable": True},
        "unavailable_from": {"type": "STRING", "nullable": True},
        "unavailable_to": {"type": "STRING", "nullable": True},
        "reply_text": {"type": "STRING"},
        "decisions": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"job_id": {"type": "INTEGER"}, "answer": {"type": "STRING", "enum": ["yes", "no"]}},
                "required": ["job_id", "answer"],
            },
        },
        "decline_rest": {"type": "BOOLEAN"},
    },
    "required": ["intent", "job_id", "unavailable_from", "unavailable_to", "reply_text", "decisions", "decline_rest"],
}

CHOOSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {"cleaner_id": {"type": "INTEGER", "nullable": True}, "why": {"type": "STRING"}},
    "required": ["cleaner_id", "why"],
}

REPLY_SYSTEM = """You are the assistant of a hotel housekeeping manager. A cleaner answered on Telegram (Greek or English)
to a cleaning job offer, a plan, or wrote on her own. Read her message and the context (JSON) and fill the fields.

intent:
- "yes": she accepts or confirms the job(s).
- "no": she declines a job, or cannot do it at the proposed time (e.g. "only after 14:00" when the job starts earlier).
- "done": she says she finished cleaning.
- "unavailable": she is sick, away or has an emergency and cannot work for some days (all her jobs in that period must move).
- "answers": she answers SEVERAL open offers in one message, or accepts only some ("I can't do any, only 6", "όχι
  στην 7 και 8, τις άλλες ναι", "μόνο την Τρίτη μπορώ", "everything except Friday"). Fill decisions and decline_rest.
- "status": she asks to see / send / repeat her offers, jobs or schedule ("send me the offers", "τι δουλειές έχω;", "πες μου το πρόγραμμα").
  The system then sends her the real list itself.
- "other": anything else (question, thanks, small talk).
job_id: the id of the job she means. Use ONLY ids listed in open_offers or upcoming_jobs; null if unclear.
unavailable_from / unavailable_to: for "unavailable" only, YYYY-MM-DD, inclusive. Resolve "today", "until Friday",
"this week" from today's date in the context. If she gives no end, use the same day as the start. null otherwise.
reply_text: one short, friendly sentence to her in HER language (context.cleaner.language: en or el). For "no" or
"unavailable" tell her it is fine and we will arrange it. Never promise anything else. Never invent job details.
decisions: for "answers" one entry per open offer she clearly accepts ("yes") or refuses ("no"), using job_id from
open_offers. A number she writes ("την 6", "job 7", "#7") is the job_id if it is in open_offers; otherwise it may be the
room number (open_offers[].room), then use that room's job. A day or time ("την Τρίτη", "after 14:00 not possible")
refers to all open offers on that date / time. [] when intent is not "answers".
decline_rest: true when she refuses everything she did not name ("I can't do any, only 6", "μόνο η 6, οι άλλες όχι"),
false when the unnamed offers should stay open. Never accept anything she did not clearly accept.
IMPORTANT: you cannot send, change or arrange anything yourself. For "other" never say you will send or check something;
only answer briefly, or tell her she can write "offers" / "πρόγραμμα" to see her jobs."""

CHOOSE_SYSTEM = """You assign a cleaning job to ONE cleaner. Pick the best candidate (JSON context follows).
Rules: choose ONLY a cleaner_id that appears in candidates. Prefer a small distance (km), but also:
- a cleaner who already works in the SAME hotel that day (see same_hotel_same_day and her schedule) is a good fit:
  she can take neighbouring rooms in one trip;
- avoid giving her more jobs than max_jobs_per_day, avoid piling jobs on one cleaner when a similarly close one is free;
- check her schedule: jobs close in time need travel, a long gap in the middle of her day is bad.
Candidates are already sorted best-first by a distance/workload cost; keep that order unless you have a clear reason.
Answer cleaner_id and a short reason (why)."""


class GeminiClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.s = settings
        self._client = client

    async def _generate(self, system: str, payload: dict, schema: dict) -> dict:
        if not self.s.gemini_api_key:
            raise LLMError("GEMINI_API_KEY is not set")
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": json.dumps(payload, ensure_ascii=False, default=str)}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "responseSchema": schema},
        }
        url = API.format(model=self.s.gemini_model)
        headers = {"x-goog-api-key": self.s.gemini_api_key}
        try:
            if self._client is not None:
                r = await self._client.post(url, json=body, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=30) as c:
                    r = await c.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise LLMError(f"Gemini request failed: {type(exc).__name__}") from exc
        if r.status_code >= 400:
            raise LLMError(f"Gemini error {r.status_code}: {r.text[:300]}")
        try:
            parts = r.json()["candidates"][0]["content"]["parts"]
            return json.loads("".join(p.get("text", "") for p in parts if not p.get("thought")))
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise LLMError(f"Unexpected Gemini response: {r.text[:300]}") from exc

    async def interpret_reply(self, text: str, context: dict) -> dict:
        """-> {"intent": yes|no|done|unavailable|other, "job_id": int|None, "unavailable_from": str|None,
        "unavailable_to": str|None, "reply_text": str, "decisions": [{job_id, answer}], "decline_rest": bool}. Raises LLMError."""
        data = await self._generate(REPLY_SYSTEM, {"message": text, "context": context}, REPLY_SCHEMA)
        intent = data.get("intent")
        return {
            "intent": intent if intent in ("yes", "no", "done", "unavailable", "status", "answers", "other") else "other",
            "job_id": data.get("job_id"),
            "unavailable_from": data.get("unavailable_from"),
            "unavailable_to": data.get("unavailable_to"),
            "reply_text": str(data.get("reply_text") or "").strip(),
            "decisions": [d for d in (data.get("decisions") or []) if isinstance(d, dict)],
            "decline_rest": bool(data.get("decline_rest")),
        }

    async def choose_cleaner(self, job: dict, candidates: list[dict], context: dict) -> int | None:
        """Return the id of one candidate (or None). The caller ignores anything not in `candidates`. Raises LLMError."""
        data = await self._generate(
            CHOOSE_SYSTEM, {"job": job, "candidates": candidates, "context": context}, CHOOSE_SCHEMA
        )
        cid = data.get("cleaner_id")
        log.info("Gemini chose cleaner %s for job %s: %s", cid, job.get("id"), data.get("why"))
        return cid if isinstance(cid, int) else None


def make_llm(settings: Settings) -> GeminiClient | None:
    """None (= app works without Gemini) when it is switched off or there is no key."""
    return GeminiClient(settings) if settings.llm_enabled and settings.gemini_api_key else None
