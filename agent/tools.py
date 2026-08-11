"""
Tool functions exposed to the LLM. Headline demo (docs/PRD.md §1):
prayer-time lookup feeding calendar availability — "book after Maghrib
tomorrow" resolves through both APIs before confirming.

TODO(day 2): real calendar API, request-scoped city memory, graceful
degradation per docs/PRD.md (ask the user, don't fail silently).
"""

from livekit.agents import function_tool


@function_tool
async def get_prayer_time(city: str, prayer: str) -> str:
    """Look up a prayer time for a given city via the Aladhan API.

    Args:
        city: City name, e.g. "Riyadh".
        prayer: One of Fajr, Dhuhr, Asr, Maghrib, Isha.
    """
    # TODO: call Aladhan API (https://aladhan.com/prayer-times-api), no key required.
    raise NotImplementedError("wire up Aladhan API — day 2")


@function_tool
async def book_calendar_event(title: str, start_time: str, duration_minutes: int = 30) -> str:
    """Book a calendar event.

    Args:
        title: Event title.
        start_time: ISO 8601 start time, or a resolved time from get_prayer_time.
        duration_minutes: Event length in minutes.
    """
    # TODO: wire up Google Calendar API — day 2.
    raise NotImplementedError("wire up calendar API — day 2")
