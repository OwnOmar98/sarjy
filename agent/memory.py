"""
Long-term memory: fact extraction, embedding, retrieval (docs/PRD.md §5,
long-term tier only — turn context/session summary live in AgentSession).

Facts are extracted (not accumulated) by a cheap model, embedded via
embedding_adapter.py (768-dim, matches db/schema.sql), and retrieved by
pgvector cosine search scoped to user_id *and* embedding provider (see
embedding_adapter.py's docstring — Gemini and OpenAI embeddings aren't
comparable even at the same dimensionality). retrieve() is Redis-cached
— per §5 memory also feeds STT vocabulary hints, so it's called on every
user turn (main.py), not just occasionally.

extract_facts() tries Groq first, falls back to OpenAI on failure — the
same "don't get blocked by one provider's rate limit" reasoning as
stt_adapter.py/llm_adapter.py, just as a manual try/except here since
this is a standalone function call, not an AgentSession component.

Barge-in truncation (§5: never record a fact from text the user didn't
actually hear) doesn't need separate handling here — extract_facts()
is only ever called on user-role turns (main.py), and user speech is
never truncated; only an interrupted *assistant* reply is.
"""

import hashlib
import json
import logging
import os

import redis.asyncio as aioredis
from openai import AsyncOpenAI

from db import get_pool
from embedding_adapter import embed_documents, embed_query

logger = logging.getLogger("sarjy-agent.memory")

_GROQ_EXTRACT_MODEL = "llama-3.1-8b-instant"  # cheap pass, per PRD §5
_OPENAI_EXTRACT_MODEL = "gpt-5.4-mini"  # same fallback tier as llm_adapter.py
_CACHE_TTL_S = 60

_redis: aioredis.Redis | None = None
_groq: AsyncOpenAI | None = None
_openai: AsyncOpenAI | None = None

_EXTRACT_SYSTEM_PROMPT = (
    "Extract durable facts about the user from their message — things worth "
    "remembering next session: preferences, names, relationships, recurring "
    "plans. A stated preference or standing rule is durable no matter how "
    'it\'s phrased — a want, a "should", an always/never statement, a '
    '"please only ever" request — judge it by whether it describes an '
    "ongoing pattern to remember, not by the literal wording used. Ignore "
    'one-off requests about right now (e.g. "book it for 3pm today" is '
    'not durable, but "I never want meetings before 3pm" is), small '
    'talk, and anything transient ("what\'s the weather"). The message '
    "may be in Arabic, English, or a mix of both — normalize each fact "
    "into a single clear, natural sentence in whichever language reads "
    "best, rather than preserving code-switched phrasing verbatim; keep "
    "names and other proper nouns as spoken. Reply with ONLY a JSON array "
    'of short factual statements, e.g. ["favorite color is blue", "has a '
    'meeting every Sunday"]. If nothing durable was said, reply with [].'
)


def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(os.environ["REDIS_URL"])
    return _redis


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


def _to_vector_literal(embedding: list[float]) -> str:
    return "[" + ",".join(map(str, embedding)) + "]"


def _parse_facts_response(raw: str | None) -> list[str]:
    raw = raw or "[]"
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1:
        logger.warning("extract_facts: no JSON array in response, discarding: %r", raw)
        return []
    try:
        facts = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        logger.warning("extract_facts: malformed JSON, discarding: %r", raw)
        return []
    return [f.strip() for f in facts if isinstance(f, str) and f.strip()]


async def extract_facts(user_id: str, transcript: str) -> list[str]:
    messages = [
        {"role": "system", "content": _EXTRACT_SYSTEM_PROMPT},
        {"role": "user", "content": transcript},
    ]
    try:
        resp = await _get_groq().chat.completions.create(
            model=_GROQ_EXTRACT_MODEL, messages=messages, temperature=0
        )
    except Exception:
        logger.warning("extract_facts: Groq failed, falling back to OpenAI", exc_info=True)
        resp = await _get_openai().chat.completions.create(
            model=_OPENAI_EXTRACT_MODEL, messages=messages, temperature=0
        )
    return _parse_facts_response(resp.choices[0].message.content)


async def store(user_id: str, facts: list[str]) -> None:
    if not facts:
        return
    embeddings, provider = await embed_documents(facts)
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        # facts.user_id FK's users.id — lazily create the row rather than
        # requiring every caller to remember to do it first.
        await conn.execute("insert into users (id) values ($1) on conflict do nothing", user_id)
        await conn.executemany(
            "insert into facts (user_id, fact, embedding, embedding_provider) "
            "values ($1, $2, $3::vector, $4)",
            [
                (user_id, fact, _to_vector_literal(emb), provider)
                for fact, emb in zip(facts, embeddings)
            ],
        )


async def retrieve(user_id: str, query: str, k: int = 5) -> list[str]:
    cache_key = f"sarjy:mem:{user_id}:{k}:{hashlib.sha256(query.encode()).hexdigest()}"
    r = _get_redis()
    if cached := await r.get(cache_key):
        return json.loads(cached)

    embedding, provider = await embed_query(query)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "select fact from facts where user_id = $1 and embedding_provider = $2 "
            "order by embedding <=> $3::vector limit $4",
            user_id,
            provider,
            _to_vector_literal(embedding),
            k,
        )
    facts = [row["fact"] for row in rows]
    await r.set(cache_key, json.dumps(facts), ex=_CACHE_TTL_S)
    return facts
