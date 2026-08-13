"""
One-off generator for eval/fixtures/*.wav — synthesizes each fixture via
the real GeminiTTS plugin (same provider the agent uses) so the audio
sounds like real TTS output, not a recording. Run once when fixtures
need to change; the .wav files themselves are committed, this script
isn't part of the CI eval run.
"""

import asyncio
import wave
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / "agent" / ".env")

import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "agent"))

from livekit.plugins.google.beta import GeminiTTS

FIXTURES = {
    "clean_en": "Hi, my name is Sarah and I enjoy hiking on weekends.",
    "clean_ar": "مرحباً، اسمي سارة وأحب المشي في الجبال في عطلة نهاية الأسبوع.",
    "code_switched": "Hey, I wanted to ask, هل تقدر تحجز لي اجتماع بكرا الساعة عشرة؟",
    "tool_trigger": "What time is Maghrib prayer in Riyadh today?",
}

OUT_DIR = Path(__file__).parent / "fixtures"


async def synthesize(text: str, out_path: Path) -> None:
    tts = GeminiTTS()
    frames = []
    async for ev in tts.synthesize(text):
        frames.append(ev.frame)

    with wave.open(str(out_path), "wb") as wav:
        wav.setnchannels(frames[0].num_channels)
        wav.setsampwidth(2)
        wav.setframerate(frames[0].sample_rate)
        for f in frames:
            wav.writeframes(bytes(f.data))

    duration = sum(f.duration for f in frames)
    print(f"{out_path.name}: {duration:.1f}s")


async def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    for name, text in FIXTURES.items():
        await synthesize(text, OUT_DIR / f"{name}.wav")


asyncio.run(main())
