-- Pre-population schema and policy changes for the Quote-of-the-Day tables.
-- Idempotent: safe to re-run.

begin;

-- 1. One pool row per verse. Nothing prevented the same entry being inserted
--    twice before this.
alter table public.qotd_pool
  drop constraint if exists qotd_pool_entry_id_key;
alter table public.qotd_pool
  add constraint qotd_pool_entry_id_key unique (entry_id);

-- 2. Somewhere to record that a verse must never be shown truncated. Used for
--    3.41 and 3.43, where the object of "kill"/"slay" is desire and cropping
--    the sentence would leave a bare imperative to kill.
alter table public.qotd_pool
  add column if not exists no_truncate boolean not null default false;

-- 3. Narrow public read on qotd_daily: only quotes that are ready and whose day
--    has arrived. Previously `using (true)` exposed tomorrow's quote and every
--    unpublished draft. qotd_pool keeps its unconditional read policy.
drop policy if exists qotd_daily_public_read on public.qotd_daily;

create policy qotd_daily_public_read
  on public.qotd_daily
  for select
  to anon, authenticated
  using (status = 'ready' and display_date <= current_date);

commit;
