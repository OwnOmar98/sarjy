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
        -- Exact embedding model, not just the vendor (embedding_adapter.py's
        -- fallback: gemini-embedding-2 -> gemini-embedding-001 -> OpenAI
        -- text-embedding-3-small) — each is a different, uncalibrated
        -- vector space even at matching dimensions, so comparing a fact
        -- embedded by one against a query embedded by another would
        -- produce a meaningless similarity score. retrieve() scopes its
        -- search to this column so a provider outage narrows memory
        -- temporarily instead of silently corrupting it.
        embedding_provider text not null default 'gemini',
        created_at timestamptz not null default now ()
    );

-- Idempotent for the already-existing local dev table, which predates
-- this column.
alter table facts
add column if not exists embedding_provider text not null default 'gemini';

create index if not exists facts_user_id_idx on facts (user_id);

create index if not exists facts_embedding_idx on facts using hnsw (embedding vector_cosine_ops);

-- The user's calendar (agent/tools.py check_calendar_availability /
-- book_calendar_event). Our own store, not a real Google Calendar — that
-- needs OAuth2/a service account, not the plain API key we have; the
-- brief only requires one external API and Aladhan (prayer time) already
-- covers that.
create table
    if not exists calendar_events (
        id uuid primary key default gen_random_uuid (),
        user_id uuid not null references users (id) on delete cascade,
        title text not null,
        start_time timestamptz not null,
        duration_minutes integer not null,
        created_at timestamptz not null default now ()
    );

create index if not exists calendar_events_user_time_idx on calendar_events (user_id, start_time);

-- One row per conversation, browsable in the web UI and resumable — not
-- the same thing as `facts` (durable, cross-session, deduped) or
-- `turn_traces` (latency-only). `summary` is generated once per close, at
-- session end (agent/main.py's shutdown callback), and is what gets fed
-- back to the LLM as context when a user picks "continue" on a past
-- conversation — the full turn-by-turn record below is for the human to
-- read, not something replayed into the model's context wholesale.
-- "Continue" reopens this same row (more messages appended, same id) —
-- it does not create a sibling row — so `updated_at` (bumped whenever the
-- session closes, including a reopen-then-close) is genuinely "last
-- activity," not just "first created," which is what the web UI sorts
-- conversations by.
create table
    if not exists sessions (
        id uuid primary key default gen_random_uuid (),
        user_id uuid not null references users (id) on delete cascade,
        started_at timestamptz not null default now (),
        updated_at timestamptz not null default now (),
        ended_at timestamptz,
        summary text
    );

-- Idempotent for the already-existing local dev table, which predates
-- this column.
alter table sessions
add column if not exists updated_at timestamptz not null default now ();

-- Dropped and recreated, not "if not exists" — that only checks the
-- index *name*, so on a database that already had this index from
-- before updated_at existed, "if not exists" would silently keep it
-- pointed at the old started_at column forever.
drop index if exists sessions_user_id_idx;

create index sessions_user_id_idx on sessions (user_id, updated_at desc);

-- Full turn-by-turn transcript for the "read back through it" view —
-- separate from `sessions.summary`, which is the compact form the LLM
-- actually sees on resume.
create table
    if not exists messages (
        id uuid primary key default gen_random_uuid (),
        session_id uuid not null references sessions (id) on delete cascade,
        role text not null,
        content text not null,
        created_at timestamptz not null default now ()
    );

create index if not exists messages_session_id_idx on messages (session_id, created_at);

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