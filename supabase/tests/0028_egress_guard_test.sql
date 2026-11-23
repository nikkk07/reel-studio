\set ON_ERROR_STOP 1
-- 0028 data guard: counts add up per day and part, the level follows the 30-day total, only members / the cloud may add,
-- only the owner sets the budget.
create or replace function pg_temp.ok(c boolean, n text) returns void language plpgsql as $$ begin raise notice '% %', case when c then 'PASS' else 'FAIL' end, n; end $$;
-- the real auth.role() reads the request's role claim (the stub always says 'authenticated')
create or replace function auth.role() returns text language sql stable as $$ select coalesce(nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'role', 'authenticated') $$;
insert into public.members (email, role) values ('owner@test.in', 'owner'), ('team@test.in', 'member') on conflict do nothing;
delete from public.egress_log; delete from public.settings where key = 'egress_guard';

select set_config('request.jwt.claims', '{"role":"service_role"}', false);
select public.add_egress('poster', 1048576, 10); select public.add_egress('poster', 1048576, 5);
select pg_temp.ok((select bytes = 2097152 and reqs = 15 from public.egress_log where part = 'poster'), 'E1 the cloud poster adds up within a day');
select pg_temp.ok((public.add_egress('mac', 0, 0)->>'level')::int = 0 and (public.egress_level()->>'used')::bigint = 2097152
  and (public.egress_level()->>'budget')::bigint = 4000::bigint * 1048576, 'E2 a zero count only asks the level; default budget 4000 MB, level 0');
insert into public.egress_log (day, part, bytes) values ((now() at time zone 'utc')::date - 3, 'web', 2600::bigint * 1048576);
select pg_temp.ok((public.egress_level()->>'level')::int = 1, 'E3 past 60 % of the budget in 30 days: saver');
insert into public.egress_log (day, part, bytes) values ((now() at time zone 'utc')::date - 5, 'mac', 900::bigint * 1048576);
select pg_temp.ok((public.egress_level()->>'level')::int = 2, 'E4 past 85 %: strict');
insert into public.egress_log (day, part, bytes) values ((now() at time zone 'utc')::date - 31, 'web', 9000::bigint * 1048576);
select pg_temp.ok((public.egress_level()->>'used')::bigint < 4000::bigint * 1048576, 'E5 days older than 30 no longer count');
do $$ begin perform public.add_egress('hacker', 1, 1); raise notice 'FAIL E6 unknown part refused';
exception when others then raise notice 'PASS E6 unknown part refused'; end $$;
do $$ begin perform public.add_egress('web', -5, 1); raise notice 'FAIL E7 negative count refused';
exception when others then raise notice 'PASS E7 negative count refused'; end $$;

select set_config('request.jwt.claims', '{"role":"authenticated","email":"stranger@x.in"}', false);
do $$ begin perform public.add_egress('web', 100, 1); raise notice 'FAIL E8 a stranger cannot add';
exception when others then raise notice 'PASS E8 a stranger cannot add'; end $$;
select set_config('request.jwt.claims', '{"role":"authenticated","email":"team@test.in"}', false);
select pg_temp.ok((public.add_egress('web', 100, 1)->>'level') is not null, 'E9 a team member''s page adds its count and gets the level');
do $$ begin perform public.set_egress_budget(20000); raise notice 'FAIL E10 only the owner sets the budget';
exception when others then raise notice 'PASS E10 only the owner sets the budget'; end $$;
select set_config('request.jwt.claims', '{"role":"authenticated","email":"owner@test.in"}', false);
select pg_temp.ok((public.set_egress_budget(20000)->>'level')::int = 0, 'E11 the owner raises the budget (e.g. on a paid plan): back to normal');
do $$ begin perform public.set_egress_budget(10); raise notice 'FAIL E12 a silly budget is refused';
exception when others then raise notice 'PASS E12 a silly budget is refused'; end $$;
select pg_temp.ok(not has_function_privilege('anon', 'public.add_egress(text, bigint, bigint)', 'EXECUTE')
  and not has_function_privilege('anon', 'public.egress_level()', 'EXECUTE'), 'E13 nothing for anonymous visitors');
select set_config('request.jwt.claims', '', false);
