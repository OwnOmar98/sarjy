"""Shared asyncpg pool — agent/memory.py and agent/tools.py both need Postgres."""

import os

import asyncpg

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    return _pool
