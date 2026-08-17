"""Shared asyncpg pool — agent/memory.py and agent/tools.py both need Postgres."""

import os

import asyncpg

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    return _pool


async def ensure_user(user_id: str) -> None:
    # Every table keyed by user_id (facts, calendar_events, sessions) FK's
    # this one, and each caller used to lazily insert it on its own first
    # write — memory.py's store(), tools.py's confirm_pending_action, and
    # (until it was missed once, confirmed live as a real
    # ForeignKeyViolationError) conversations.py's start_session. One
    # call, made as early as entrypoint.py knows the identity at all,
    # replaces "remember to do this" with "already done" for every
    # caller after it — the per-call inserts stay in place as defense in
    # depth, not because this one is unreliable.
    pool = await get_pool()
    await pool.execute("insert into users (id) values ($1) on conflict do nothing", user_id)
