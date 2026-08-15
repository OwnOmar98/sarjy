"""
Provider-agnostic embeddings for long-term memory (docs/PRD.md §5,
agent/memory.py), with automatic fallback via a manual try/except — not
livekit-agents' FallbackAdapter, which is built for stt.STT/llm.LLM/
tts.TTS instances living inside AgentSession; this is a standalone pair
of functions memory.py calls directly, same shape as before this file
grew a backup provider.

Three tiers, tried in order: gemini-embedding-2 -> gemini-embedding-001
-> OpenAI text-embedding-3-small.

Unlike STT/LLM/TTS, a fallback here isn't a drop-in: embeddings get
*stored* and later compared numerically. A fact embedded by one tier and
a query embedded by another would produce a meaningless cosine-similarity
score even at matching dimensions — they're different, uncalibrated
vector spaces, not just different providers of the same thing. That's
true across vendors (Gemini vs OpenAI) *and* across the two Gemini
models below — embed_documents()/embed_query() both return exactly which
model served the call (not just "gemini") so memory.py can record it per
-fact and scope retrieve()'s search to whichever one answered the query.
A fallback then degrades to "temporarily narrower memory," not silently
wrong results.

gemini-embedding-2 and gemini-embedding-001 aren't interchangeable calls,
either — -2 dropped the task_type param entirely ("You cannot use the
task_type field for the gemini-embedding-2 model" — ai.google.dev/
gemini-api/docs/embeddings) in favor of a literal prompt prefix to convey
document-vs-query intent. Passing task_type to -2 or skipping the prefix
on -001 wouldn't error — it would silently embed with the wrong
semantics, which is worse than a crash, so the two get separate embed
functions rather than one parameterized over both.
"""

import asyncio
import logging
import os

from google import genai
from google.genai import types
from openai import AsyncOpenAI

logger = logging.getLogger("sarjy-agent.embedding_adapter")

_GEMINI_PRIMARY_MODEL = "gemini-embedding-2"
_GEMINI_FALLBACK_MODEL = "gemini-embedding-001"
_OPENAI_MODEL = "text-embedding-3-small"
_DIMENSIONS = 768  # matches db/schema.sql's vector(768); all three models support truncating to it
_TASK_TYPES = {"document": "RETRIEVAL_DOCUMENT", "query": "RETRIEVAL_QUERY"}

_genai_client: genai.Client | None = None
_openai_client: AsyncOpenAI | None = None


def _get_genai_client() -> genai.Client:
    global _genai_client
    if _genai_client is None:
        _genai_client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    return _genai_client


def _get_openai_client() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _openai_client


def _v2_prefix(texts: list[str], kind: str) -> list[str]:
    # gemini-embedding-2's documented convention for asymmetric retrieval
    # (ai.google.dev/gemini-api/docs/embeddings) — facts have no title,
    # and the docs are explicit that the titleless case is the literal
    # string "title: none", not an omitted/blank title.
    if kind == "query":
        return [f"task: search result | query: {t}" for t in texts]
    return [f"title: none | text: {t}" for t in texts]


async def _gemini_embed_v2(texts: list[str], kind: str) -> list[list[float]]:
    # gemini-embedding-2 doesn't actually batch: passing N>1 texts in
    # `contents` still returns exactly 1 embedding, silently — confirmed
    # live, no error, no warning. store()'s zip(facts, embeddings) then
    # truncates to the shorter list, so only the first of several facts
    # in one turn ever got saved. One call per text sidesteps it; -001
    # (the fallback) batches correctly on the same input, so this is a
    # -2-specific gap, not a general SDK limitation.
    client = _get_genai_client()
    prefixed = _v2_prefix(texts, kind)

    async def _one(text: str) -> list[float]:
        resp = await client.aio.models.embed_content(
            model=_GEMINI_PRIMARY_MODEL,
            contents=[text],
            config=types.EmbedContentConfig(output_dimensionality=_DIMENSIONS),
        )
        return resp.embeddings[0].values

    return list(await asyncio.gather(*(_one(t) for t in prefixed)))


async def _gemini_embed_v1(texts: list[str], kind: str) -> list[list[float]]:
    resp = await _get_genai_client().aio.models.embed_content(
        model=_GEMINI_FALLBACK_MODEL,
        contents=texts,
        config=types.EmbedContentConfig(
            output_dimensionality=_DIMENSIONS, task_type=_TASK_TYPES[kind]
        ),
    )
    return [e.values for e in resp.embeddings]


async def _openai_embed(texts: list[str]) -> list[list[float]]:
    resp = await _get_openai_client().embeddings.create(
        model=_OPENAI_MODEL, input=texts, dimensions=_DIMENSIONS
    )
    return [d.embedding for d in resp.data]


async def _embed(texts: list[str], kind: str) -> tuple[list[list[float]], str]:
    try:
        return await _gemini_embed_v2(texts, kind), _GEMINI_PRIMARY_MODEL
    except Exception:
        logger.warning(
            "embed: %s failed, falling back to %s",
            _GEMINI_PRIMARY_MODEL,
            _GEMINI_FALLBACK_MODEL,
            exc_info=True,
        )
    try:
        return await _gemini_embed_v1(texts, kind), _GEMINI_FALLBACK_MODEL
    except Exception:
        logger.warning(
            "embed: %s failed, falling back to OpenAI", _GEMINI_FALLBACK_MODEL, exc_info=True
        )
        return await _openai_embed(texts), "openai"


async def embed_documents(texts: list[str]) -> tuple[list[list[float]], str]:
    """Embed facts being written to storage. Returns (embeddings, provider)."""
    return await _embed(texts, kind="document")


async def embed_query(text: str) -> tuple[list[float], str]:
    """Embed a query used to search stored facts. Returns (embedding, provider)."""
    embeddings, provider = await _embed([text], kind="query")
    return embeddings[0], provider
