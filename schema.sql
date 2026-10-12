-- Run this once in Supabase: Dashboard -> SQL Editor -> New query -> paste -> Run

create extension if not exists "pgcrypto";

-- Every speech the bot writes. text_hash is unique, so a repeat can never be saved.
create table if not exists public.speeches (
  id          uuid primary key default gen_random_uuid(),
  topic       text,
  title       text,
  text        text not null,
  text_hash   text not null unique,
  description text,
  tags        text[],
  created_at  timestamptz not null default now()
);

-- Every video attempt and its result.
create table if not exists public.uploads (
  id               uuid primary key default gen_random_uuid(),
  speech_id        uuid references public.speeches(id) on delete cascade,
  status           text not null default 'rendering',  -- rendering | rendered | uploaded | failed
  youtube_video_id text,
  youtube_url      text,
  error            text,
  created_at       timestamptz not null default now()
);

-- Tracks whether the periodic video job is currently running and when it last succeeded.
create table if not exists public.scheduler_state (
  id            uuid primary key default gen_random_uuid(),
  job_name      text not null unique,
  status        text not null default 'idle',
  last_run_at   timestamptz,
  last_error    text,
  updated_at    timestamptz not null default now(),
  created_at    timestamptz not null default now()
);

create index if not exists speeches_created_at_idx on public.speeches (created_at desc);
create index if not exists uploads_speech_id_idx   on public.uploads (speech_id);
create index if not exists scheduler_state_job_name_idx on public.scheduler_state (job_name);

-- Lock the tables from the public/anon key. The bot uses the service_role key,
-- which bypasses RLS, so it still works.
alter table public.speeches enable row level security;
alter table public.uploads  enable row level security;
alter table public.scheduler_state enable row level security;
