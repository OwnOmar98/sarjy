"""
Redis-backed cache for fixed TTS phrases (docs/PRD.md §4: "TTS phrase
cache — pre-rendered fixed strings... takes first-byte to ~0ms, cuts the
largest per-minute cost line"). Scoped to exactly the fixed strings this
codebase actually has (main.py's _FALLBACK_MESSAGE) — not a general
cache for LLM-generated replies, which vary per turn and were never a
cache candidate in the first place.

Keyed by text only, not (text, voice, lang) as the PRD phrasing
suggests: with a fallback TTS provider in play (tts_adapter.py), the
"active" voice can differ turn to turn, and there's no cheap way to read
back which underlying provider inside a FallbackAdapter is live right
now. A provider failover landing on exactly the same turn this cache is
read would produce a one-off wrong-voice apology — a real but narrow
edge case, accepted rather than engineered around for a single cached
phrase.
"""

import hashlib
import json
import logging
import os

import redis.asyncio as aioredis
from livekit import rtc

logger = logging.getLogger("sarjy-agent.tts_cache")

_redis: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(os.environ["REDIS_URL"])
    return _redis


def _key(text: str) -> str:
    return f"sarjy:tts:{hashlib.sha256(text.encode()).hexdigest()}"


async def get(text: str) -> rtc.AudioFrame | None:
    try:
        raw = await _get_redis().get(_key(text))
    except Exception:
        logger.warning("redis get failed, treating as a cache miss", exc_info=True)
        return None
    if raw is None:
        return None

    meta_len = int.from_bytes(raw[:4], "big")
    meta = json.loads(raw[4 : 4 + meta_len])
    data = raw[4 + meta_len :]
    return rtc.AudioFrame(
        data=data,
        sample_rate=meta["sample_rate"],
        num_channels=meta["num_channels"],
        samples_per_channel=len(data) // (2 * meta["num_channels"]),
    )


async def store(text: str, frames: list[rtc.AudioFrame]) -> None:
    if not frames:
        return
    # All frames in one synthesis stream share sample_rate/num_channels —
    # concatenating raw PCM and storing the format once, rather than
    # preserving individual frame boundaries, which nothing downstream
    # needs (rtc.AudioFrame carries no per-frame timing metadata to lose).
    meta = json.dumps(
        {"sample_rate": frames[0].sample_rate, "num_channels": frames[0].num_channels}
    ).encode()
    data = b"".join(bytes(f.data) for f in frames)
    payload = len(meta).to_bytes(4, "big") + meta + data
    try:
        await _get_redis().set(_key(text), payload)
    except Exception:
        # A failed cache write must never break TTS playback — the frames
        # were already yielded to the caller before this runs.
        logger.warning("redis set failed, phrase stays uncached", exc_info=True)
