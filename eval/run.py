"""
CI eval harness (docs/PRD.md §2, §7): drives eval/fixtures/*.wav through
the real agent pipeline in a real LiveKit room — the actual production
entrypoint()/SarjyAgent (agent/main.py), not a re-implementation — then
reports p50/p95 per latency stage straight from turn_traces
(agent/latency.py already logs every stage there).

Scoped to latency only, not eval/README.md's fuller original vision
(WER, tool-call accuracy) — this closes out the Latency pillar
specifically, which is what asked for a p50/p95 scorecard tracked per
commit; the rest is future work.

fake_job_context (livekit.agents.testing) is what makes reusing the
real entrypoint() possible without a full worker process — it still
needs a real, already-connected room (the docstring is explicit about
this), which is why the room connection happens before entering it.
"""

import asyncio
import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv

AGENT_DIR = Path(__file__).parent.parent / "agent"
sys.path.insert(0, str(AGENT_DIR))
load_dotenv(AGENT_DIR / ".env")  # local dev only; CI sets real env vars directly

import main as agent_main
from livekit import api, rtc
from livekit.agents.testing import fake_job_context
from livekit.agents.utils import http_context
from livekit.agents.utils.audio import audio_frames_from_file

from db import get_pool

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIXTURES = ["clean_en", "clean_ar", "code_switched", "tool_trigger"]
SAMPLE_RATE = 24000
FRAME_MS = 10
SILENCE_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
# How long to keep the room open after the fixture audio finishes, for
# STT -> LLM -> TTS (and the greeting before it) to actually complete.
SETTLE_SECONDS = 20


def _make_token(identity: str, room: str, *, suppress_dispatch: bool = False) -> str:
    token = (
        api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
        .with_identity(identity)
        .with_grants(api.VideoGrants(room_join=True, room=room))
    )
    if suppress_dispatch:
        # The real production agent (CA_SKSUk3gKmmFQ, no dispatch name set)
        # auto-joins every new room on this LiveKit Cloud project, including
        # these ad-hoc eval rooms, and its own AgentSession then competes
        # with this harness's fake_job_context-driven one for the same
        # fixture track. An explicit (non-empty) RoomAgentDispatch list
        # overrides that implicit dispatch; an *empty* agents=[] list does
        # not — proto3 can't distinguish an empty repeated field from an
        # omitted one on the wire, so it's silently ignored. Confirmed
        # live both ways.
        token = token.with_room_config(
            api.RoomConfiguration(
                agents=[api.RoomAgentDispatch(agent_name="eval-harness-no-dispatch")]
            )
        )
    return token.to_jwt()


async def _stream_silence(source: rtc.AudioSource, stop: asyncio.Event) -> None:
    # RoomIO only forwards tracks published with source=SOURCE_MICROPHONE
    # (_ParticipantAudioInputStream filters everything else silently,
    # regardless of whether the raw track was subscribed) — publishing the
    # fixture's real speech only *after* the greeting finishes, on a track
    # not tagged as a microphone, meant the agent's VAD never saw a single
    # frame of it. Confirmed live: fixing the source tag was what actually
    # made endpointing/stt/memory rows start appearing; keeping the track
    # alive with silence from before session-start, then switching to real
    # audio on the same already-subscribed track, additionally matches how
    # a real browser client behaves (mic track published on connect, well
    # before the agent's greeting starts).
    frame = rtc.AudioFrame.create(SAMPLE_RATE, 1, SILENCE_SAMPLES)
    while not stop.is_set():
        await source.capture_frame(frame)


async def _publish_fixture(source: rtc.AudioSource, wav_path: Path) -> None:
    async for frame in audio_frames_from_file(
        str(wav_path), sample_rate=SAMPLE_RATE, num_channels=1
    ):
        await source.capture_frame(frame)


async def _run_fixture(name: str) -> str:
    room_name = f"eval-{name}-{uuid.uuid4().hex[:8]}"
    url = os.environ["LIVEKIT_URL"]

    user_room = rtc.Room()
    await user_room.connect(
        url, _make_token("eval-user", room_name, suppress_dispatch=True)
    )

    source = rtc.AudioSource(sample_rate=SAMPLE_RATE, num_channels=1)
    track = rtc.LocalAudioTrack.create_audio_track("fixture", source)
    await user_room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    )

    stop_silence = asyncio.Event()
    silence_task = asyncio.create_task(_stream_silence(source, stop_silence))

    agent_room = rtc.Room()
    await agent_room.connect(url, _make_token("eval-agent", room_name))

    with fake_job_context(room=agent_room, job_id=f"eval-{name}") as ctx:
        agent_main.prewarm(ctx.proc)
        await agent_main.entrypoint(ctx)

        stop_silence.set()
        await silence_task
        await _publish_fixture(source, FIXTURES_DIR / f"{name}.wav")
        await asyncio.sleep(SETTLE_SECONDS)

    await user_room.disconnect()
    await agent_room.disconnect()
    return room_name


def _percentile(sorted_vals: list[int], p: int) -> int:
    idx = min(len(sorted_vals) - 1, max(0, -(-p * len(sorted_vals) // 100) - 1))
    return sorted_vals[idx]


async def _report(session_ids: list[str]) -> bool:
    pool = await get_pool()
    rows = await pool.fetch(
        "select stage, ms from turn_traces where session_id = any($1::text[])",
        session_ids,
    )
    by_stage: dict[str, list[int]] = {}
    for r in rows:
        by_stage.setdefault(r["stage"], []).append(r["ms"])

    print(f"\n{'STAGE':<20}{'P50':>10}{'P95':>10}{'N':>6}")
    for stage in sorted(by_stage):
        vals = sorted(by_stage[stage])
        print(
            f"{stage:<20}{_percentile(vals, 50):>8}ms{_percentile(vals, 95):>8}ms{len(vals):>6}"
        )

    return bool(rows)


async def main() -> None:
    # Plugins that make their own HTTP calls (e.g. ElevenLabs TTS) expect
    # an aiohttp session bound by the real worker process — fake_job_context
    # doesn't provide one on its own; without this, ElevenLabs synthesis
    # fails outright on every turn (falls back to Gemini TTS, so the
    # session doesn't crash, but tts_first_byte then reflects a
    # failed-then-retried attempt, not a clean one).
    async with http_context.open():
        session_ids = []
        for name in FIXTURES:
            print(f"running fixture: {name}", file=sys.stderr)
            session_ids.append(await _run_fixture(name))

    if not await _report(session_ids):
        print("no turn_traces rows recorded for any fixture", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
