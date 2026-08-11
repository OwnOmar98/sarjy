-- Sarjy — long-term memory schema (docs/PRD.md §5).
-- TODO(day 2): migrations tooling once this outgrows plain SQL.
create extension if not exists vector;

create table
    if not exists users (
        id uuid primary key default gen_random_uuid (),
        created_at timestamptz not null default now ()
    );

-- Extracted durable facts, embedded for retrieval — not raw transcript
-- (agent/memory.py extract_facts()).
create table
    if not exists facts (
        id uuid primary key default gen_random_uuid (),
        user_id uuid not null references users (id) on delete cascade,
        fact text not null,
        embedding vector (768),
        created_at timestamptz not null default now ()
    );

create index if not exists facts_user_id_idx on facts (user_id);

create index if not exists facts_embedding_idx on facts using hnsw (embedding vector_cosine_ops);

-- Per-turn latency traces, feeds the eval harness p50/p95 table (§2, §7).
create table
    if not exists turn_traces (
        id uuid primary key default gen_random_uuid (),
        session_id text not null,
        language text not null,
        stage text not null,
        ms integer not null,
        created_at timestamptz not null default now ()
    );

create index if not exists turn_traces_session_idx on turn_traces (session_id);