"""
Sarjy voice agent worker — entry point.

Wires VAD -> turn detection -> STT -> LLM -> TTS with tool calls,
memory, and latency instrumentation. Day-1 goal: a working EN+AR voice
loop, deployed. Stubbed pieces are TODO-tagged per docs/PRD.md.
"""

import logging

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    JobProcess,
    RoomInputOptions,
    WorkerOptions,
    cli,
)
from livekit.plugins import groq, noise_cancellation, silero

from latency import LatencyTracker
from tts_adapter import build_tts

load_dotenv()
logger = logging.getLogger("sarjy-agent")


class SarjyAgent(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are Sarjy, a helpful bilingual (Arabic/English) voice "
                "assistant. Match the user's language, including mid-"
                "sentence switches. Keep responses short — you're spoken "
                "aloud, not read."
            ),
            # No tools yet: get_prayer_time/book_calendar_event (tools.py)
            # both raise NotImplementedError. Registering them let the
            # model attempt real calls — confirmed live: it leaked a raw
            # <function=...> call as spoken text. Re-add once implemented
            # (day 2, docs/PRD.md §1).
        )


def prewarm(proc: JobProcess) -> None:
    # Load VAD once per worker process, not per session.
    proc.userdata["vad"] = silero.VAD.load()


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    latency = LatencyTracker(room=ctx.room)

    # TODO(day 2): swap STT/LLM/TTS per request by language (docs/PRD.md §3-4).
    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        stt=groq.STT(model="whisper-large-v3-turbo"),
        llm=groq.LLM(model="llama-3.3-70b-versatile"),
        tts=build_tts(),
        # TODO(day 2-3): multilingual turn detector — measure AR/EN
        # code-switching, don't assume (docs/PRD.md "Quality/fidelity").
        #
        # Preemptive generation (on by default) starts an early reply
        # before end-of-turn is confident. Live test: a low-confidence
        # turn (0.43, threshold 0.56) preceded a reply that never played.
        # Off until confirmed safe with the real turn detector above.
        turn_handling={"preemptive_generation": {"enabled": False}},
    )

    latency.attach(session)

    await session.start(
        agent=SarjyAgent(),
        room=ctx.room,
        room_input_options=RoomInputOptions(
            # Krisp noise cancellation — near-free on LiveKit Cloud.
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )

    await session.generate_reply(
        instructions="Greet the user briefly in English, mention you also speak Arabic."
    )


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm))
