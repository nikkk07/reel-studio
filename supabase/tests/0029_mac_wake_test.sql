\set ON_ERROR_STOP 1
-- 0029 wake signal: new jobs, status changes, website / timer changes and pings wake the Mac; its own saves do not.
create or replace function pg_temp.ok(c boolean, n text) returns void language plpgsql as $$ begin raise notice '% %', case when c then 'PASS' else 'FAIL' end, n; end $$;
create or replace function auth.role() returns text language sql stable as $$ select nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'role' $$;
delete from public.wake;
select set_config('request.jwt.claims', '{"role":"authenticated","email":"x@y.in"}', false);
insert into public.jobs (id, kind, brand, status, input) values ('00000000-0000-0000-0000-0000000000e1', 'reel', 'weone', 'queued', '{}');
select pg_temp.ok((select count(*) = 1 from public.wake where kind = 'job' and ref = '00000000-0000-0000-0000-0000000000e1'), 'W1 a new job wakes the Mac');
select set_config('request.jwt.claims', '{"role":"service_role"}', false);
update public.jobs set status = 'running', lease_owner = 'mac' where id = '00000000-0000-0000-0000-0000000000e1';
select pg_temp.ok((select count(*) = 2 from public.wake), 'W2 a status change wakes it');
update public.jobs set progress = 0.4, stage = 'voice' where id = '00000000-0000-0000-0000-0000000000e1';
update public.jobs set output = '{"trace": {}}' where id = '00000000-0000-0000-0000-0000000000e1';
select pg_temp.ok((select count(*) = 2 from public.wake), 'W3 the Mac''s own progress and trace saves do not echo back');
select set_config('request.jwt.claims', '{"role":"authenticated","email":"x@y.in"}', false);
update public.jobs set output = output || '{"hold": true}' where id = '00000000-0000-0000-0000-0000000000e1';
select pg_temp.ok((select count(*) = 3 from public.wake), 'W4 a change made on the website wakes it');
select set_config('request.jwt.claims', '', false);
update public.jobs set output = output || '{"decided_at": "x"}' where id = '00000000-0000-0000-0000-0000000000e1';
select pg_temp.ok((select count(*) = 4 from public.wake), 'W5 a change by a timer (no sign-in) wakes it');
insert into public.pings default values;
select pg_temp.ok((select count(*) = 1 from public.wake where kind = 'ping'), 'W6 Connect on the website wakes it');
insert into public.wake (kind, ref, at) values ('job', 'old', now() - interval '2 hours');
select set_config('request.jwt.claims', '{"role":"service_role"}', false);
update public.jobs set status = 'review' where id = '00000000-0000-0000-0000-0000000000e1';
select pg_temp.ok(not exists (select 1 from public.wake where ref = 'old'), 'W7 rows older than an hour are cleared');
select pg_temp.ok(not has_table_privilege('authenticated', 'public.wake', 'SELECT') and not has_table_privilege('anon', 'public.wake', 'SELECT'), 'W8 only the Mac can read it');
delete from public.jobs where id = '00000000-0000-0000-0000-0000000000e1';
select set_config('request.jwt.claims', '', false);
