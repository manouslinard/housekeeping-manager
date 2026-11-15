"""Understand a cleaner's text reply: YES / NO / DONE, with an optional job number."""
from __future__ import annotations

import re
import unicodedata


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


YES = {_norm(w) for w in ["yes", "y", "yep", "yeah", "ok", "okay", "accept", "confirm", "sure", "ναι", "οκ", "nai"]}
NO = {_norm(w) for w in ["no", "n", "nope", "decline", "cant", "όχι", "οχι", "ochi"]}
DONE = {_norm(w) for w in ["done", "finished", "complete", "completed", "τέλος", "τελος", "έτοιμο", "ετοιμο", "teleiosa"]}


def is_simple(text: str) -> bool:
    """A bare answer like "yes", "ΝΑΙ 12" or "no 7": at most 3 words and one number. Anything longer
    ("I can only do 4, no to the rest") has more to it than the first yes/no word."""
    tokens = re.findall(r"\d+|[^\W\d_]+", _norm(text or ""))
    return len(tokens) <= 3 and sum(t.isdigit() for t in tokens) <= 1 and not ({"ολα", "ολες", "ολους", "all", "everything"} & set(tokens))


def parse_reply(text: str) -> tuple[str, int | None]:
    """Return (intent, job_id). intent is 'yes', 'no', 'done' or 'unknown'."""
    tokens = re.findall(r"\d+|[^\W\d_]+", _norm(text or ""))
    intent, job_id = "unknown", None
    for tok in tokens:
        if tok.isdigit():
            if job_id is None:
                job_id = int(tok)
        elif intent == "unknown":
            if tok in YES:
                intent = "yes"
            elif tok in NO:
                intent = "no"
            elif tok in DONE:
                intent = "done"
    return intent, job_id
