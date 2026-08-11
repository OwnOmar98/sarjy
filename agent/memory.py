"""
Long-term memory: fact extraction, embedding, retrieval (docs/PRD.md §5,
long-term tier only — turn context/session summary live in AgentSession).

TODO(day 2):
- extract_facts(): cheap-model pass per turn, durable statements only
- store(): embed + upsert into Postgres/pgvector
- retrieve(): vector search scoped to user_id, Redis-cached
- Barge-in truncation: on session.interrupted, extract_facts() must see
  only what was actually spoken, not the full generated turn (§5).
"""


async def extract_facts(user_id: str, transcript: str) -> list[str]:
    raise NotImplementedError("day 2")


async def store(user_id: str, facts: list[str]) -> None:
    raise NotImplementedError("day 2")


async def retrieve(user_id: str, query: str, k: int = 5) -> list[str]:
    raise NotImplementedError("day 2")
