"""
Per-stage latency instrumentation: end-of-speech -> first-audible-byte,
published to the room's "latency" data topic for the web HUD (docs/PRD.md §3).

TODO(day 2): hook AgentSession's metrics events
(https://docs.livekit.io/agents/build/metrics/) instead of this stub.
TODO(day 3): log each turn to Postgres for eval-harness p50/p95 (§2, §7).
"""

import json
import time

from livekit.rtc import Room


class LatencyTracker:
    def __init__(self, room: Room) -> None:
        self._room = room
        self._turn_start: float | None = None

    def attach(self, session) -> None:
        # TODO: session.on("metrics_collected", self._on_metrics) — day 2.
        pass

    def mark_turn_start(self) -> None:
        self._turn_start = time.monotonic()

    async def publish(self, stage: str, elapsed_ms: float) -> None:
        payload = json.dumps({"stage": stage, "ms": elapsed_ms}).encode("utf-8")
        await self._room.local_participant.publish_data(payload, topic="latency")
