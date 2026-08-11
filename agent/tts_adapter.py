"""
Provider-agnostic TTS (docs/PRD.md §3, §8). Defaults to Gemini, swaps to
ElevenLabs Flash v2.5 if a key is present — decided at worker startup so
a session's provider stays stable (caching, docs/PRD.md §4).

Uses google.beta.GeminiTTS, not google.TTS: google.TTS wraps Cloud
Text-to-Speech and needs GCP service-account credentials, not a plain
API key. GeminiTTS reads GOOGLE_API_KEY directly.

TODO(day 1-2): Arabic TTS normalization (docs/PRD.md §2) hooks in here.
"""

import os

from livekit.agents import tts as tts_base
from livekit.plugins.google.beta import GeminiTTS


def build_tts() -> tts_base.TTS:
    elevenlabs_key = os.getenv("ELEVENLABS_API_KEY")
    if elevenlabs_key:
        from livekit.plugins import elevenlabs

        # Passed explicitly: the plugin's own env fallback reads
        # ELEVEN_API_KEY, not ELEVENLABS_API_KEY — confirmed live, it
        # otherwise raises even with our var set.
        return elevenlabs.TTS(model="eleven_flash_v2_5", api_key=elevenlabs_key)
    return GeminiTTS()
