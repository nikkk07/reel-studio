\set ON_ERROR_STOP 1
create or replace function pg_temp.ok(c boolean, m text) returns void language plpgsql as $$ begin if not c then raise exception 'FAIL: %', m; end if; raise notice 'PASS: %', m; end $$;
create or replace function pg_temp.err(sql text) returns text language plpgsql as $$ begin execute sql; return null; exception when others then return sqlerrm; end $$;
grant execute on function pg_temp.ok(boolean,text), pg_temp.err(text) to authenticated;
delete from jobs where kind in ('suggest','optimize'); update jobs set status='cancelled' where status in ('queued','running','waiting_quota','review');
select set_config('request.jwt.claims','{"email":"weoneaviation8@gmail.com"}',false);
set role authenticated;
select pg_temp.ok(pg_temp.err($$select public.create_job('optimize', jsonb_build_object('prompt', repeat('x', 30000)))$$) like 'That request is too long%', 'a 30 KB request is refused');
select pg_temp.ok(pg_temp.err($$select public.create_job('reel', '{"concept":{"title":"sneaky"},"autopilot":"x"}')$$) like 'Use Run now%', 'a member cannot fake an autopilot reel to dodge the daily limit');
select public.create_job('suggest', '{"note":"a"}'); select public.create_job('suggest', '{"note":"b"}'); select public.create_job('optimize', '{"prompt":"c"}');
select pg_temp.ok(pg_temp.err($$select public.create_job('optimize', '{"prompt":"d"}')$$) like 'Three idea requests%', 'a flood of idea requests (Claude quota burn) is capped at 3 running');
select pg_temp.ok((select (public.create_job('suggest', '{"note":"a"}')).input->>'note') = 'a', 'repeating the same request returns the running one (no error)');
reset role;
insert into jobs(id,kind,status,input,output) values ('f0000000-0000-0000-0000-000000000001','reel','running','{"concept":{"title":"R"}}','{}'); update jobs set status='review' where id='f0000000-0000-0000-0000-000000000001';
set role authenticated;
select pg_temp.ok(pg_temp.err($$select public.request_changes('f0000000-0000-0000-0000-000000000001', repeat('y', 2500))$$) like 'Keep the changes under%', '2,500-character change notes are refused (reel untouched)');
select pg_temp.ok(pg_temp.err($$select public.set_reel_posting('f0000000-0000-0000-0000-000000000001', 'now')$$) like 'permission denied%', 'legacy posting function is closed');
select pg_temp.ok(pg_temp.err($$select public.set_post_schedule(array['19:30'], true)$$) like 'permission denied%', 'legacy schedule function is closed');
reset role;
-- autopilot daily limit
delete from jobs where kind='suggest' and input ? 'autopilot';
insert into jobs(kind,status,input) select 'suggest','done', jsonb_build_object('autopilot',true,'day',to_char(now() at time zone 'Asia/Kolkata','YYYY-MM-DD'),'trigger','manual') from generate_series(1,2);
set role authenticated;
select pg_temp.ok((public.run_autopilot_now()).status = 'queued', 'third autopilot run of the day is allowed');
reset role; update jobs set status='done' where kind='suggest' and input ? 'autopilot'; set role authenticated;
select pg_temp.ok(pg_temp.err($$select public.run_autopilot_now()$$) like 'Autopilot already made 3%', 'a fourth Run now the same day is refused (no Instagram flood)');
reset role;
-- night rule (only autopilot reels; manual reels keep the 5-minute rule)
insert into jobs(id,kind,status,input,output) values ('f0000000-0000-0000-0000-000000000002','reel','running','{"autopilot":"x"}','{}'),('f0000000-0000-0000-0000-000000000003','reel','running','{}','{}');
update jobs set status='review' where id in ('f0000000-0000-0000-0000-000000000002','f0000000-0000-0000-0000-000000000003');
update jobs set output = output || jsonb_build_object('review_deadline', now()-interval '1 s') where id in ('f0000000-0000-0000-0000-000000000002','f0000000-0000-0000-0000-000000000003');
select public.auto_approve_due();
select pg_temp.ok((select status from jobs where id='f0000000-0000-0000-0000-000000000003')='done', 'manual reel auto-posts after the window as before');
select pg_temp.ok((select status from jobs where id='f0000000-0000-0000-0000-000000000002') = case when (now() at time zone 'Asia/Kolkata')::time between '07:30' and '23:00' then 'done' else 'review' end, 'autopilot reel: posts in the day, waits for 07:30 at night (now ' || to_char(now() at time zone 'Asia/Kolkata','HH24:MI') || ' IST)');
delete from jobs where id::text like 'f0000000%'; delete from jobs where kind in ('suggest','optimize');
select 'ALL HARDENING CHECKS PASSED';
