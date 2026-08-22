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

extract_facts() tries OpenAI first, falls back to Groq on failure.
OpenAI is primary here (unlike llm_adapter.py's Groq-first order) because
Groq's cheap extraction model (llama-3.1-8b-instant) was verified to
silently return "[]" — a clean, non-exception response, so the old
Groq-first/OpenAI-fallback order never caught it — for the two most
common fact categories a user states: name and favorite-thing. OpenAI's
gpt-5.4-mini got every tested case right. Groq stays as the fallback
for resilience against an OpenAI outage, not for cost — see
docs/KNOWN_ISSUES.md.

Barge-in truncation (§5: never record a fact from text the user didn't
actually hear) doesn't need separate handling here — extract_facts()
is only ever called on user-role turns (main.py), and user speech is
never truncated; only an interrupted *assistant* reply is.

Corrections (a restated name, a changed preference) don't leave the old
fact behind to keep getting recalled alongside the new one — store()
deletes whatever extract_facts() flags as superseded in the same
transaction as inserting the replacement, given the caller passes it
the user's relevant existing facts first (main.py's _remember()).
Without those, extract_facts() has nothing to compare a correction
against and can only ever add. store() also clears retrieve()'s Redis
cache for that user on any change — otherwise a query cached just
before a correction can keep answering with the pre-correction fact
for the rest of its TTL, even though Postgres already has the right
answer.
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

# llama-3.1-8b-instant was removed from Groq's catalog entirely (confirmed
# live via /openai/v1/models — a plain 404 model_not_found, not a rate
# limit); gpt-oss-20b is the current cheap/fast model on Groq.
_GROQ_EXTRACT_MODEL = "openai/gpt-oss-20b"  # cheap pass, per PRD §5
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
    "ongoing pattern to remember, not by the literal wording used. A "
    'universal word — all, every, any, كل — makes even a bare "want" a '
    'standing rule rather than a one-off: "I want all X to be Y" '
    "describes an ongoing pattern and is durable, while the same sentence "
    "without that universal word may not be. (Stated as a shape on "
    "purpose, not as a worked example — a concrete example sentence in "
    "this prompt gets echoed verbatim onto unrelated input, which is a "
    "real failure this prompt already hit once.) Ignore "
    'one-off requests about right now (e.g. "book it for 3pm today" is '
    'not durable, but "I never want meetings before 3pm" is), small '
    'talk, and anything transient ("what\'s the weather"). The message '
    "may be in Arabic, English, or a mix of both — normalize each fact "
    "into a single clear, natural sentence in whichever language reads "
    "best, rather than preserving code-switched phrasing verbatim; keep "
    "names and other proper nouns as spoken. If the user spells a name or "
    'word out letter by letter ("O-M-A-R", "دال-ألف-نون"), always store '
    'the assembled, properly-capitalized word ("Omar"), never the literal '
    "spelled-out letters — whether or not they're correcting an earlier "
    "mishearing.\n\n"
    "You will also be given the user's existing known facts (whichever "
    "ones are already relevant to this message — not necessarily all of "
    "them). If the new message states something that contradicts or "
    "updates one of them — a new name, a changed preference, a corrected "
    'spelling — put that OLD fact\'s exact original text in "remove", and '
    'the new corrected fact in "add", so the outdated one doesn\'t keep '
    "getting recalled alongside the correction. A fact with nothing to "
    "add or remove is simply omitted from both. Reply with ONLY a JSON "
    'object: {"add": [...], "remove": [...]}, e.g. {"add": ["favorite '
    'color is green"], "remove": ["favorite color is blue"]}. Both may be '
    "empty arrays."
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


def _parse_facts_response(raw: str | None) -> tuple[list[str], list[str]]:
    raw = raw or '{"add": [], "remove": []}'
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        logger.warning("extract_facts: no JSON object in response, discarding: %r", raw)
        return [], []
    try:
        parsed = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        logger.warning("extract_facts: malformed JSON, discarding: %r", raw)
        return [], []
    add = [f.strip() for f in parsed.get("add", []) if isinstance(f, str) and f.strip()]
    remove = [f.strip() for f in parsed.get("remove", []) if isinstance(f, str) and f.strip()]
    return add, remove


async def extract_facts(
    user_id: str, transcript: str, existing_facts: list[str] | None = None
) -> tuple[list[str], list[str]]:
    """Returns (facts_to_add, facts_to_remove). existing_facts should be
    whichever of the user's already-known facts are relevant to this
    message (e.g. main.py's own retrieve(user_id, transcript) call,
    which the Redis cache in retrieve() below makes effectively free to
    call again here with the same query) — without them, the model has
    nothing to compare a correction against and can only ever add, never
    supersede what it's correcting.
    """
    user_content = (
        f"Existing known facts: {json.dumps(existing_facts or [])}\n\nNew message: {transcript}"
    )
    messages = [
        {"role": "system", "content": _EXTRACT_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    try:
        resp = await _get_openai().chat.completions.create(
            model=_OPENAI_EXTRACT_MODEL, messages=messages, temperature=0
        )
    except Exception:
        logger.warning("extract_facts: OpenAI failed, falling back to Groq", exc_info=True)
        resp = await _get_groq().chat.completions.create(
            model=_GROQ_EXTRACT_MODEL, messages=messages, temperature=0
        )
    return _parse_facts_response(resp.choices[0].message.content)


async def store(user_id: str, facts: list[str], remove: list[str] | None = None) -> None:
    if not facts and not remove:
        return
    embeddings: list[list[float]] = []
    provider = None
    if facts:
        embeddings, provider = await embed_documents(facts)
        if len(embeddings) != len(facts):
            # zip() below would otherwise silently truncate to the shorter
            # list — confirmed live: gemini-embedding-2 returned 1 embedding
            # for 4 facts with no error, and only the first fact got saved.
            # Fixed at the embedding_adapter.py source, but this stays as a
            # hard stop against the same silent-data-loss shape recurring
            # from a different provider quirk in the future.
            raise RuntimeError(
                f"embed_documents returned {len(embeddings)} embeddings for "
                f"{len(facts)} facts (provider={provider}) — refusing to "
                "silently save a truncated subset."
            )
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        # facts.user_id FK's users.id — lazily create the row rather than
        # requiring every caller to remember to do it first.
        await conn.execute("insert into users (id) values ($1) on conflict do nothing", user_id)
        if remove:
            # Same transaction as the insert below — a superseded fact and
            # its replacement never both exist, and never neither, from
            # any other reader's point of view. Matched by exact text,
            # since extract_facts() is told to echo the old fact's
            # original text back verbatim specifically so this matches.
            await conn.executemany(
                "delete from facts where user_id = $1 and fact = $2",
                [(user_id, fact) for fact in remove],
            )
        if facts:
            await conn.executemany(
                "insert into facts (user_id, fact, embedding, embedding_provider) "
                "values ($1, $2, $3::vector, $4)",
                [
                    (user_id, fact, _to_vector_literal(emb), provider)
                    for fact, emb in zip(facts, embeddings)
                ],
            )
    await _invalidate_cache(user_id)


def _cache_key(user_id: str, k: int, query: str) -> str:
    return f"sarjy:mem:{user_id}:{k}:{hashlib.sha256(query.encode()).hexdigest()}"


async def _invalidate_cache(user_id: str) -> None:
    # retrieve()'s cache key hashes the query text, so there's no way to
    # target just the entries a given fact change could affect — every
    # cached result for this user is invalidated instead, not just the
    # ones that happen to mention the changed fact. Confirmed live: a
    # user correcting a fact, then immediately re-asking the exact
    # question that had already been cached (well inside the 60s TTL),
    # got the pre-correction answer back — store() had updated Postgres
    # correctly, retrieve() just never knew to stop trusting its cache.
    r = _get_redis()
    keys = [key async for key in r.scan_iter(match=f"sarjy:mem:{user_id}:*")]
    if keys:
        await r.delete(*keys)


async def retrieve(user_id: str, query: str, k: int = 5) -> list[str]:
    cache_key = _cache_key(user_id, k, query)
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
