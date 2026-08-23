"""Per-conversation persistence: the full turn-by-turn record for the web
UI to browse, and a short summary for the LLM to actually see on resume.

Deliberately separate from memory.py: facts are durable, deduped,
cross-session and never replayed verbatim to the model; a session's
summary is scoped to one specific past conversation and only matters
when the user explicitly picks "continue" on it. Different lifecycle,
different table, different consumer.
"""

import logging
import os

from openai import AsyncOpenAI

from db import get_pool

logger = logging.getLogger("sarjy-agent.conversations")

_SUMMARY_MODEL_OPENAI = "gpt-5.4-mini"
# llama-3.1-8b-instant was removed from Groq's catalog entirely (confirmed
# live via /openai/v1/models — a plain 404 model_not_found); gpt-oss-20b
# is the current cheap/fast model on Groq, same replacement memory.py's
# own Groq fallback made.
_SUMMARY_MODEL_GROQ = "openai/gpt-oss-20b"

_SUMMARY_SYSTEM_PROMPT = (
    "Summarize this conversation between a user and Sarjy, a voice "
    "assistant, in 2-3 short sentences — enough that Sarjy can pick the "
    "conversation back up naturally (what the user was doing or asking "
    "about, anything proposed or booked, how it was left). Plain prose, "
    "no lists, no preamble like 'This conversation is about'. If the "
    "conversation was too short or trivial to summarize meaningfully "
    "(a greeting with no real exchange), reply with exactly: (nothing "
    "notable)."
)

_groq: AsyncOpenAI | None = None
_openai: AsyncOpenAI | None = None


def _get_groq() -> AsyncOpenAI:
    global _groq
    if _groq is None:
        _groq = AsyncOpenAI(
            api_key=os.environ["GROQ_API_KEY"], base_url="https://api.groq.com/openai/v1"
        )
    return _groq


def _get_openai() -> AsyncOpenAI:
    global _openai
    if _openai is None:
        _openai = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _openai


def _session_dict(row) -> dict:
    # Same field set and shape web/server/api/sessions.get.ts's rows
    # already have (asyncpg gives back datetime objects; web_notify.py
    # sends this straight over HTTP as JSON, which needs plain strings) —
    # this is what the sidebar's live-push handler expects to receive
    # verbatim as a SessionSummary, not a shape it has to reshape first.
    return {
        "id": str(row["id"]),
        "started_at": row["started_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
        "ended_at": row["ended_at"].isoformat() if row["ended_at"] else None,
        "summary": row["summary"],
    }


def _message_dict(row) -> dict:
    return {
        "id": str(row["id"]),
        "role": row["role"],
        "content": row["content"],
        "created_at": row["created_at"].isoformat(),
    }


async def start_session(user_id: str) -> tuple[str, dict]:
    """(session_id, the row as a live-push-ready dict) — see _session_dict."""
    # sessions.user_id FK's users.id — lazily create the row rather than
    # requiring every caller to remember to do it first (same pattern as
    # memory.py's store() and tools.py's confirm_pending_action).
    # Confirmed live: a brand-new identity's very first DB write is this
    # call (memory.retrieve() before it is read-only), so without this
    # every first-ever connection failed with a ForeignKeyViolationError.
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("insert into users (id) values ($1) on conflict do nothing", user_id)
        row = await conn.fetchrow(
            "insert into sessions (user_id) values ($1) "
            "returning id, started_at, updated_at, ended_at, summary",
            user_id,
        )
    return str(row["id"]), _session_dict(row)


async def add_message(session_id: str, role: str, content: str) -> dict:
    """The inserted message as a live-push-ready dict — see _message_dict.

    Also bumps sessions.updated_at, same as end_session's own write below:
    without this, a resumed conversation someone is actively talking into
    only moves back to the top of the web UI's most-recently-active sort
    once the call ends, not while it's actually happening.
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "insert into messages (session_id, role, content) values ($1, $2, $3) "
            "returning id, role, content, created_at",
            session_id,
            role,
            content,
        )
        await conn.execute("update sessions set updated_at = now() where id = $1", session_id)
    return _message_dict(row)


async def resume_context(session_id: str, user_id: str) -> tuple[bool, str | None]:
    """(found, summary) for a "continue this conversation" request —
    found=False means main.py falls back to a brand-new session, same as
    if resume_session_id had never been sent at all; found=True but
    summary=None means the session is a real, valid resume target that
    just never got a summary (too short/trivial last time).

    A plain nullable-summary return couldn't distinguish those two cases,
    and conflating them would have looked the same to the LLM (no
    context injected) for a real reason and a "this id doesn't belong to
    you" reason alike. Scoped to user_id too, not just session id — the
    id alone is guessable-ish (a UUID a client could pass unchanged), and
    this is the one read path a browser-controlled value reaches
    un-authenticated by anything other than "does this session belong to
    this identity".
    """
    pool = await get_pool()
    row = await pool.fetchrow(
        "select summary from sessions where id = $1 and user_id = $2", session_id, user_id
    )
    if row is None:
        return False, None
    return True, row["summary"]


async def _summarize(transcript: str) -> str | None:
    if not transcript.strip():
        return None
    messages = [
        {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": transcript},
    ]
    try:
        resp = await _get_openai().chat.completions.create(
            model=_SUMMARY_MODEL_OPENAI, messages=messages, temperature=0
        )
    except Exception:
        logger.warning("summarize: OpenAI failed, falling back to Groq", exc_info=True)
        resp = await _get_groq().chat.completions.create(
            model=_SUMMARY_MODEL_GROQ, messages=messages, temperature=0
        )
    text = (resp.choices[0].message.content or "").strip()
    if not text or text == "(nothing notable)":
        return None
    return text


async def end_session(session_id: str) -> dict:
    """The updated row as a live-push-ready dict — see _session_dict."""
    # Always re-summarizes the *full* transcript fetched here, not just
    # whatever was said since the last close — correct by construction
    # for a reopened session (agent/main.py no longer creates a new row
    # on resume, it appends to this one), since there's nothing "new
    # only" to isolate.
    pool = await get_pool()
    rows = await pool.fetch(
        "select role, content from messages where session_id = $1 order by created_at", session_id
    )
    transcript = "\n".join(f"{r['role']}: {r['content']}" for r in rows)
    summary = await _summarize(transcript)
    # updated_at, not just ended_at — this is what the web UI's session
    # list actually sorts by (most recently *active*, not most recently
    # *created*), so sending a message in a reopened conversation is what
    # should move it back to the top of that list.
    row = await pool.fetchrow(
        "update sessions set ended_at = now(), updated_at = now(), summary = $2 "
        "where id = $1 returning id, started_at, updated_at, ended_at, summary",
        session_id,
        summary,
    )
    return _session_dict(row)
