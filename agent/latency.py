"""
Per-stage latency instrumentation: publishes each turn's real per-stage
timings (docs/PRD.md §3-4) to the room's "latency" data topic for the web
HUD, and logs the same values to turn_traces (db/schema.sql) for the CI
eval harness's p50/p95 scorecard (eval/README.md, §2, §7). Values come
straight off ChatMessage.metrics (main.py's conversation_item_added
handler) and a manual timing around memory.py's retrieve() call — not a
hand-rolled stopwatch guessing at stage boundaries from outside the
pipeline; the SDK already measures end_of_turn_delay/transcription_delay/
llm_node_ttft/tts_node_ttfb/e2e_latency per turn more precisely than a
wrapper here could.
"""

import json
import logging

from livekit.rtc import Room

from db import get_pool

logger = logging.getLogger("sarjy-agent.latency")


class LatencyTracker:
    def __init__(self, room: Room, session_id: str) -> None:
        self._room = room
        self._session_id = session_id
        self._turn = 0
        # STT-detected language isn't always available — Groq's plugin
        # doesn't surface it for non-whisper-1 models (stt_adapter.py) —
        # so this is best-effort, updated from user_input_transcribed
        # when the provider does report one.
        self._language = "unknown"

    def next_turn(self) -> None:
        # Called synchronously the moment a new user turn starts (main.py's
        # conversation_item_added), before any of that turn's stages are
        # published. Stages publish via asyncio.create_task from multiple
        # call sites (the sync conversation_item_added handler and the
        # async on_user_turn_completed, which awaits a slower memory.py
        # round-trip) — their actual sends can complete in any order, so
        # the frontend can't reliably tell turns apart by stage name/arrival
        # order alone. Tagging every stage with this counter lets it group
        # by turn instead, which arrival order can't corrupt.
        self._turn += 1

    def set_language(self, language: str) -> None:
        self._language = language

    async def publish(self, stage: str, elapsed_ms: float) -> None:
        payload = json.dumps({"stage": stage, "ms": elapsed_ms, "turn": self._turn}).encode("utf-8")
        await self._room.local_participant.publish_data(payload, topic="latency")
        await self._log(stage, elapsed_ms)

    async def _log(self, stage: str, elapsed_ms: float) -> None:
        # Best-effort: a DB hiccup here must never affect the room
        # broadcast the HUD depends on, so it's logged and swallowed
        # rather than raised.
        try:
            pool = await get_pool()
            await pool.execute(
                "insert into turn_traces (session_id, language, stage, ms) values ($1, $2, $3, $4)",
                self._session_id,
                self._language,
                stage,
                round(elapsed_ms),
            )
        except Exception:
            logger.exception("failed to log turn trace")
