-- Quote-of-the-Day tables: public read, service-role-only writes.
--
-- Starting state (verified 2026-09-05): RLS was enabled on both tables but no
-- policies existed, so anon/authenticated were denied everything. Separately,
-- neither anon, authenticated nor service_role held SELECT/INSERT/UPDATE/DELETE
-- grants — service_role bypasses RLS but still needs the grant, so writes would
-- have failed with "permission denied" too. Both tables also carried a stray
-- TRUNCATE grant for anon and authenticated; TRUNCATE is not gated by RLS.
--
-- Idempotent: safe to re-run.

begin;

-- RLS is already on; assert it rather than assume.
alter table public.qotd_pool  enable row level security;
alter table public.qotd_daily enable row level security;

-- --------------------------------------------------------------- grants
-- Read for the two public roles.
grant select on public.qotd_pool, public.qotd_daily to anon, authenticated;

-- Writes for the service role only. It has rolbypassrls, so it needs no policy,
-- but it does need the table grant and the sequence for the serial ids.
grant select, insert, update, delete
  on public.qotd_pool, public.qotd_daily to service_role;
grant usage, select
  on sequence public.qotd_pool_id_seq, public.qotd_daily_id_seq to service_role;

-- Nothing else. TRUNCATE in particular ignores RLS entirely, so leaving it with
-- anon would let a public caller empty either table through any SQL surface.
revoke truncate, references, trigger
  on public.qotd_pool, public.qotd_daily from anon, authenticated;
revoke insert, update, delete
  on public.qotd_pool, public.qotd_daily from anon, authenticated;

-- -------------------------------------------------------------- policies
-- SELECT only. No INSERT/UPDATE/DELETE policy is defined, which under RLS means
-- those commands are denied for every role that does not bypass RLS.
drop policy if exists qotd_pool_public_read  on public.qotd_pool;
drop policy if exists qotd_daily_public_read on public.qotd_daily;

create policy qotd_pool_public_read
  on public.qotd_pool
  for select
  to anon, authenticated
  using (true);

create policy qotd_daily_public_read
  on public.qotd_daily
  for select
  to anon, authenticated
  using (true);

commit;
