"""
Tool functions exposed to the LLM: prayer-time lookup (Aladhan API) and
a calendar (Postgres, db/schema.sql calendar_events — not a real Google
Calendar, which would need OAuth2/a service account rather than a plain
API key).

user_id is never a tool argument the model can see or set — it comes
from RunContext.userdata (session.userdata, set in agent/main.py from
the LiveKit participant identity), the same per-browser id
agent/memory.py keys facts on.
"""

from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
from livekit.agents import RunContext, function_tool

from db import get_pool

_ALADHAN_BASE = "https://api.aladhan.com/v1/timingsByCity"
_ALADHAN_METHOD = (
    4  # Umm al-Qura University, Makkah — standard for KSA (docs/PRD.md: users are in KSA)
)
_Prayer = Literal["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"]
_DEFAULT_TZ = ZoneInfo("Asia/Riyadh")


def _resolve_date(date: str) -> datetime:
    now = datetime.now(_DEFAULT_TZ)
    if date == "today":
        return now
    if date == "tomorrow":
        return now + timedelta(days=1)
    return datetime.fromisoformat(date).replace(tzinfo=_DEFAULT_TZ)


def _coerce_minutes(duration_minutes: int | str | None) -> int:
    # The model sometimes emits duration_minutes as a string ("30"
    # instead of 30). A strict `int` type hint would make the JSON
    # schema handed to the LLM reject that outright and crash the turn —
    # int | str widens the schema so it validates, and gets coerced here.
    try:
        return int(duration_minutes)
    except (TypeError, ValueError):
        pass
    try:
        return int(float(duration_minutes))  # int() rejects "45.0" outright
    except (TypeError, ValueError):
        return 30


def _round_up_to_quarter_hour(dt: datetime) -> datetime:
    # Booking exactly at a prayer's minute (11:58) is accurate but reads
    # as an odd, un-calendar-like time — round up (never earlier, so a
    # "book after Dhuhr" slot never lands before Dhuhr actually is) to
    # the nearest quarter hour, the way a person scheduling by hand would.
    quarter = timedelta(minutes=15)
    hour_start = dt.replace(minute=0, second=0, microsecond=0)
    quarters = -(-(dt - hour_start) // quarter)  # ceiling division
    return hour_start + quarters * quarter


def _parse_start_time(start_time: str) -> datetime:
    dt = datetime.fromisoformat(start_time)
    # A naive ISO string (no offset) — assume KSA local time rather than
    # erroring, since that's this product's target market (docs/PRD.md §6).
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=_DEFAULT_TZ)
    return _round_up_to_quarter_hour(dt)


def _to_local(dt: datetime) -> datetime:
    # Postgres timestamptz always round-trips as UTC regardless of what
    # offset it was written with (same instant, wrong display timezone).
    # Every value read back from calendar_events needs this before it's
    # shown to the user; values that never left this process (e.g.
    # book_calendar_event's own confirmation) already carry the right
    # offset and don't.
    return dt.astimezone(_DEFAULT_TZ)


@function_tool
async def get_prayer_time(
    city: str, prayer: _Prayer, date: str = "today", country: str = "Saudi Arabia"
) -> str:
    """Look up a prayer time for a given city via the Aladhan API.

    Args:
        city: City name, e.g. "Riyadh".
        date: "today", "tomorrow", or an ISO date (YYYY-MM-DD).
        country: Defaults to Saudi Arabia.
    """
    # The Literal type already constrains this at the schema level for a
    # well-behaved provider (strict function-calling) — kept as a cheap
    # defensive fallback, not the primary guard, since this codebase has
    # documented real instances of Groq/Llama not perfectly honoring a
    # tool schema.
    if prayer not in _Prayer.__args__:
        return f"Unknown prayer '{prayer}'. Valid: {', '.join(_Prayer.__args__)}."

    try:
        target = _resolve_date(date)
    except ValueError:
        return f"'{date}' isn't a date I understand — ask for today, tomorrow, or a specific date."
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(
                f"{_ALADHAN_BASE}/{target.strftime('%d-%m-%Y')}",
                params={"city": city, "country": country, "method": _ALADHAN_METHOD},
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            # Graceful degradation (docs/PRD.md §5): tell the user, don't
            # fail silently or crash the tool call.
            return (
                f"The prayer-time service isn't responding right now ({e}). "
                "Let the user know and offer to try again shortly."
            )

    payload = resp.json()["data"]
    time_str = payload["timings"][prayer]  # "HH:MM", 24h
    tz = ZoneInfo(payload["meta"]["timezone"])
    hour, minute = (int(p) for p in time_str.split(":"))
    when = target.astimezone(tz).replace(hour=hour, minute=minute, second=0, microsecond=0)

    # Just the ISO timestamp, not also a separately-spelled-out date/time
    # — main.py's instructions already forbid speaking either verbatim,
    # so the human-formatted duplicate never earned its tokens; the ISO
    # value alone still has everything needed to chain into the next
    # tool call (date, time, and offset in one parseable string).
    return f"{prayer} in {city}: {when.isoformat()}"


@function_tool
async def check_calendar_availability(
    context: RunContext, start_time: str, duration_minutes: int | str | None = 30
) -> str:
    """Check whether the user is free at a given time — call this before
    book_calendar_event if availability isn't already known.

    Args:
        start_time: ISO 8601 start time (e.g. the ISO value get_prayer_time returns).
        duration_minutes: Event length in minutes.
    """
    user_id = context.userdata
    try:
        start = _parse_start_time(start_time)
    except ValueError:
        # e.g. the model passing "tomorrow" (a day, not a moment) as
        # start_time — datetime.fromisoformat() would otherwise raise
        # unhandled and crash the turn.
        return (
            f"'{start_time}' isn't a specific time — ask the user for an "
            "exact time (a clock time, or after a named prayer) and try again."
        )
    duration_minutes = _coerce_minutes(duration_minutes)
    end = start + timedelta(minutes=duration_minutes)

    pool = await get_pool()
    rows = await pool.fetch(
        "select title, start_time from calendar_events "
        "where user_id = $1 and start_time < $2 "
        "and start_time + (duration_minutes::text || ' minutes')::interval > $3",
        user_id,
        end,
        start,
    )
    if not rows:
        return f"Free at {start.isoformat()} for {duration_minutes} minutes."
    conflicts = "; ".join(
        f"'{r['title']}' at {_to_local(r['start_time']).isoformat()}" for r in rows
    )
    return f"Not free — conflicts with: {conflicts}"


@function_tool
async def list_calendar_events(context: RunContext, date: str = "today") -> str:
    """List everything already on the user's calendar for a given day —
    call this for "what's on my schedule/calendar" style questions.
    check_calendar_availability is for a specific proposed slot instead.

    Args:
        date: "today", "tomorrow", or an ISO date (YYYY-MM-DD).
    """
    user_id = context.userdata
    try:
        day_start = _resolve_date(date).replace(hour=0, minute=0, second=0, microsecond=0)
    except ValueError:
        return f"'{date}' isn't a date I understand — ask for today, tomorrow, or a specific date."
    day_end = day_start + timedelta(days=1)

    pool = await get_pool()
    rows = await pool.fetch(
        "select title, start_time, duration_minutes from calendar_events "
        "where user_id = $1 and start_time >= $2 and start_time < $3 "
        "order by start_time",
        user_id,
        day_start,
        day_end,
    )
    if not rows:
        return f"Nothing booked on {day_start.date().isoformat()}."
    events = "; ".join(
        f"'{r['title']}' at {_to_local(r['start_time']).isoformat()} "
        f"for {r['duration_minutes']} minutes"
        for r in rows
    )
    return f"On {day_start.date().isoformat()}: {events}."


@function_tool
async def cancel_calendar_event(
    context: RunContext, start_time: str, title: str | None = None
) -> str:
    """Cancel/delete a calendar event. If you don't already know its exact
    start time, call list_calendar_events or check_calendar_availability
    first to find it — never guess a time just to cancel something.

    Args:
        start_time: ISO 8601 start time of the event to cancel — must
            match an existing event (e.g. the value list_calendar_events
            returned for it).
        title: Optional — narrows the match if more than one event starts
            at the same time.
    """
    user_id = context.userdata
    try:
        start = _parse_start_time(start_time)
    except ValueError:
        return (
            f"'{start_time}' isn't a specific time — ask the user for an exact time and try again."
        )

    pool = await get_pool()
    if title:
        rows = await pool.fetch(
            "select id, title, start_time from calendar_events "
            "where user_id = $1 and start_time = $2 and title ilike $3",
            user_id,
            start,
            f"%{title}%",
        )
    else:
        rows = await pool.fetch(
            "select id, title, start_time from calendar_events "
            "where user_id = $1 and start_time = $2",
            user_id,
            start,
        )

    if not rows:
        return (
            f"No event found at {start.isoformat()}"
            + (f" matching '{title}'" if title else "")
            + " — call list_calendar_events to find the exact time first."
        )
    if len(rows) > 1:
        options = "; ".join(
            f"'{r['title']}' at {_to_local(r['start_time']).isoformat()}" for r in rows
        )
        return f"More than one event matches — ask the user which one: {options}"

    event = rows[0]
    await pool.execute("delete from calendar_events where id = $1", event["id"])
    return f"Cancelled '{event['title']}' at {_to_local(event['start_time']).isoformat()}."


@function_tool
async def book_calendar_event(
    context: RunContext, title: str, start_time: str, duration_minutes: int | str | None = 30
) -> str:
    """Book a calendar event. Check availability first if it's not already known.

    Args:
        title: Event title.
        start_time: ISO 8601 start time, or the ISO value from get_prayer_time.
        duration_minutes: Event length in minutes.
    """
    user_id = context.userdata
    try:
        start = _parse_start_time(start_time)
    except ValueError:
        return (
            f"'{start_time}' isn't a specific time — ask the user for an "
            "exact time (a clock time, or after a named prayer) and try again."
        )
    duration_minutes = _coerce_minutes(duration_minutes)

    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        # calendar_events.user_id FK's users.id — lazily create the row
        # rather than requiring every caller to remember to do it first
        # (same pattern as memory.py's store()).
        await conn.execute("insert into users (id) values ($1) on conflict do nothing", user_id)
        await conn.execute(
            "insert into calendar_events (user_id, title, start_time, duration_minutes) "
            "values ($1, $2, $3, $4)",
            user_id,
            title,
            start,
            duration_minutes,
        )

    return f"Booked '{title}' at {start.isoformat()} for {duration_minutes} minutes."
