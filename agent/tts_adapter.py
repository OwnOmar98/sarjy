"""
Provider-agnostic TTS (docs/PRD.md §3, §8) with automatic fallback —
same reasoning as stt_adapter.py/llm_adapter.py: uses livekit-agents'
own tts.FallbackAdapter rather than a hand-rolled retry loop.

Uses google.beta.GeminiTTS, not google.TTS: google.TTS wraps Cloud
Text-to-Speech and needs GCP service-account credentials, not a plain
API key. GeminiTTS reads GOOGLE_API_KEY directly.

TTS_PROVIDER picks which is tried first ("elevenlabs" or "gemini");
defaults to whichever has a key set (ElevenLabs, if both do — this
matches the original behavior before this file grew a real fallback).
If only one provider has a key, there's nothing to fall back to, same
as before.

TODO(day 1-2): Arabic TTS normalization (docs/PRD.md §2) hooks in here.
"""

import os

from livekit.agents import tts as tts_base
from livekit.plugins.google.beta import GeminiTTS


def _gemini_tts() -> tts_base.TTS:
    return GeminiTTS()


def _elevenlabs_tts() -> tts_base.TTS | None:
    elevenlabs_key = os.getenv("ELEVENLABS_API_KEY")
    if not elevenlabs_key:
        return None
    from livekit.plugins import elevenlabs

    # Passed explicitly: the plugin's own env fallback reads
    # ELEVEN_API_KEY, not ELEVENLABS_API_KEY, so it raises even with our
    # var set unless the key is handed to it directly.
    return elevenlabs.TTS(model="eleven_flash_v2_5", api_key=elevenlabs_key)


def build_tts() -> tts_base.TTS:
    providers = {"gemini": _gemini_tts, "elevenlabs": _elevenlabs_tts}
    default_primary = "elevenlabs" if os.getenv("ELEVENLABS_API_KEY") else "gemini"
    primary = os.getenv("TTS_PROVIDER", default_primary).lower()
    if primary not in providers:
        primary = default_primary

    order = [primary, *(name for name in providers if name != primary)]
    instances = [inst for name in order if (inst := providers[name]()) is not None]
    if len(instances) == 1:
        return instances[0]
    return tts_base.FallbackAdapter(instances)
