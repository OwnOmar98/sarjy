"""
Provider-agnostic conversation LLM with automatic fallback. Groq's rate
limit (12k tokens/minute, 100k/day on the free tier) can kill a turn
outright — this is the first line of defense against that, tried before
a turn ever reaches main.py's llm_node safety net.

Uses livekit-agents' own llm.FallbackAdapter, same reasoning as
stt_adapter.py: it already catches provider errors (rate limits
included), not a hand-rolled retry loop.

LLM_PROVIDER picks which one is tried first ("groq", default, or
"openai"); whichever isn't primary is always the automatic fallback.
"""

import os

from livekit.agents import llm as llm_base
from livekit.plugins import groq
from livekit.plugins import openai as openai_plugin


def _groq_llm() -> llm_base.LLM:
    return groq.LLM(model="llama-3.3-70b-versatile")


def _openai_llm() -> llm_base.LLM:
    # Cheap/fast tier — appropriate for a fallback meant to rarely run,
    # not the flagship model.
    return openai_plugin.LLM(model="gpt-5.4-mini")


def build_llm() -> llm_base.LLM:
    providers = {"groq": _groq_llm, "openai": _openai_llm}
    primary = os.getenv("LLM_PROVIDER", "groq").lower()
    if primary not in providers:
        primary = "groq"
    order = [primary, *[name for name in providers if name != primary]]
    return llm_base.FallbackAdapter([providers[name]() for name in order])
