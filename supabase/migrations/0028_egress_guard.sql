-- 0028 Egress guard: Reel Studio never again runs out of the Supabase Free plan's monthly data allowance.
--  * egress_log: bytes the website, the cloud poster and the Mac received from Supabase, per UTC day and part
--    (each part counts its own responses and adds them every few minutes; a small allowance per request covers headers).
--  * egress_level(): the last 30 days against the budget (settings 'egress_guard'.budget_mb, default 4000 MB of the
--    5 GB allowance). level 0 = normal, 1 = saver (60 % used: everything polls less often),
--    2 = strict (85 % used: only posting, running reels and the open page keep working; learning and stats wait).
--  * add_egress(): adds a part's count and answers with the level, so every part slows down by itself.
--  * set_egress_budget(): the owner can change the budget (e.g. after moving to a paid plan).

create table if not exists public.egress_log (
  day date not null,
  part text not null check (part in ('web', 'poster', 'mac')),
  bytes bigint not null default 0,
  reqs bigint not null default 0,
  primary key (day, part));
alter table public.egress_log enable row level security;
drop policy if exists egress_log_read on public.egress_log;
create policy egress_log_read on public.egress_log for select to authenticated using ((select public.is_member()));
grant select on public.egress_log to authenticated;

create or replace function public.egress_level() returns jsonb
language sql stable security definer set search_path = '' as $$
  with u as (select coalesce(sum(bytes), 0)::bigint as used, coalesce(sum(reqs), 0)::bigint as reqs
             from public.egress_log where day > (now() at time zone 'utc')::date - 30),
       b as (select greatest(100, least(1000000, coalesce(
               (select nullif(value->>'budget_mb', '')::bigint from public.settings where key = 'egress_guard'), 4000)))::bigint * 1048576 as budget)
  select jsonb_build_object('used', u.used, 'reqs', u.reqs, 'budget', b.budget,
    'level', case when u.used >= b.budget * 0.85 then 2 when u.used >= b.budget * 0.6 then 1 else 0 end)
  from u, b $$;

create or replace function public.add_egress(p_part text, p_bytes bigint, p_reqs bigint default 0) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare d date := (now() at time zone 'utc')::date;
begin
  if coalesce(auth.role(), '') <> 'service_role' and not public.is_member() then raise exception 'Your account is not on the members list.'; end if;
  if p_part not in ('web', 'poster', 'mac') then raise exception 'unknown part'; end if;
  if coalesce(p_bytes, 0) < 0 or p_bytes > 2000000000 or coalesce(p_reqs, 0) < 0 or p_reqs > 10000000 then raise exception 'bad count'; end if;
  if coalesce(p_bytes, 0) > 0 or coalesce(p_reqs, 0) > 0 then
    insert into public.egress_log (day, part, bytes, reqs) values (d, p_part, coalesce(p_bytes, 0), coalesce(p_reqs, 0))
    on conflict (day, part) do update set bytes = public.egress_log.bytes + excluded.bytes, reqs = public.egress_log.reqs + excluded.reqs;
  end if;
  delete from public.egress_log where day < d - 90;
  return public.egress_level();
end $$;

create or replace function public.set_egress_budget(p_mb int) returns jsonb
language plpgsql security definer set search_path = '' as $$
begin
  if not public.is_owner() then raise exception 'Only the studio owner can change the data budget.'; end if;
  if p_mb is null or p_mb < 500 or p_mb > 1000000 then raise exception 'Choose a budget between 500 MB and 1,000,000 MB.'; end if;
  insert into public.settings (key, value, updated_at) values ('egress_guard', jsonb_build_object('budget_mb', p_mb), now())
  on conflict (key) do update set value = excluded.value, updated_at = now();
  return public.egress_level();
end $$;

revoke all on function public.egress_level() from public, anon;
revoke all on function public.add_egress(text, bigint, bigint) from public, anon;
revoke all on function public.set_egress_budget(int) from public, anon;
grant execute on function public.egress_level() to authenticated, service_role;
grant execute on function public.add_egress(text, bigint, bigint) to authenticated, service_role;
grant execute on function public.set_egress_budget(int) to authenticated;
