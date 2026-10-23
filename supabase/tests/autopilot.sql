\set ON_ERROR_STOP 1
create or replace function pg_temp.ok(c boolean, m text) returns void language plpgsql as $$ begin if not c then raise exception 'FAIL: %', m; end if; raise notice 'PASS: %', m; end $$;
create or replace function pg_temp.err(sql text) returns text language plpgsql as $$ begin execute sql; return null; exception when others then return sqlerrm; end $$;
grant execute on function pg_temp.ok(boolean,text), pg_temp.err(text) to authenticated, anon;
delete from jobs where kind='suggest' and input ? 'autopilot';
delete from jobs where id::text like 'eeeeeeee%';
-- schedule window (India time)
update settings set value = jsonb_build_object('on', true, 'tz', 'Asia/Kolkata', 'time', to_char((now() at time zone 'Asia/Kolkata') + interval '10 min', 'HH24:MI')) where key='autopilot';
select pg_temp.ok(public.autopilot_tick() is null, 'before the set time nothing is queued');
update settings set value = value || jsonb_build_object('time', to_char((now() at time zone 'Asia/Kolkata') - interval '4 hours', 'HH24:MI')) where key='autopilot';
select pg_temp.ok(public.autopilot_tick() is null or to_char((now() at time zone 'Asia/Kolkata'), 'HH24') < '04', 'more than 3 hours late (e.g. turned on at night) nothing is queued');
delete from jobs where kind='suggest' and input ? 'autopilot';
update settings set value = value || jsonb_build_object('time', to_char((now() at time zone 'Asia/Kolkata') - interval '2 min', 'HH24:MI')) where key='autopilot';
select pg_temp.ok(public.autopilot_tick() is not null, 'at the set time one autopilot request is queued');
select pg_temp.ok(public.autopilot_tick() is null and public.autopilot_tick() is null, 'every later tick that day does nothing (one run a day)');
select pg_temp.ok((select count(*) from jobs where kind='suggest' and input->>'autopilot'='true' and input->>'trigger'='schedule' and input->>'day'=to_char(now() at time zone 'Asia/Kolkata','YYYY-MM-DD'))=1, 'exactly one request, stamped with today''s India date');
update settings set value = value || '{"on": false}' where key='autopilot';
delete from jobs where kind='suggest' and input ? 'autopilot';
select pg_temp.ok(public.autopilot_tick() is null, 'switched off: nothing is queued');
-- site controls
select set_config('request.jwt.claims','{"email":"weoneaviation8@gmail.com"}',false);
set role authenticated;
select pg_temp.ok(pg_temp.err($$select public.set_autopilot(true, '25:00')$$) like 'Use a time%', 'bad time rejected');
select pg_temp.ok((public.set_autopilot(true, '13:30')) ->> 'time' = '13:30', 'member sets 13:30');
select pg_temp.ok((select (public.run_autopilot_now()).id) = (select (public.run_autopilot_now()).id), 'Run now twice -> one request');
select pg_temp.ok(pg_temp.err($$select public.autopilot_tick()$$) like 'permission denied%', 'the site cannot call the cloud clock');
select pg_temp.ok(pg_temp.err($$select public.set_reel_stats('eeeeeeee-0000-0000-0000-000000000001', '{}')$$) like 'permission denied%', 'the site cannot write Instagram results');
reset role;
select set_config('request.jwt.claims','{"email":"stranger@example.com"}',false);
set role authenticated;
select pg_temp.ok(pg_temp.err($$select public.run_autopilot_now()$$) like '%members list%', 'non-members cannot start it');
reset role;
select set_config('request.jwt.claims','{}',false);
-- daily cap: autopilot reels still start, manual ones wait
insert into jobs(id,kind,status,input,created_at) values
 ('eeeeeeee-0000-0000-0000-000000000001','reel','queued','{"concept":{"title":"manual"}}', now()-interval '2 min'),
 ('eeeeeeee-0000-0000-0000-000000000002','reel','queued','{"concept":{"title":"auto"},"autopilot":"x"}', now()-interval '1 min');
update jobs set status='done' where kind='reel' and status in ('queued','running') and id::text not like 'eeeeeeee%';
select pg_temp.ok((select (public.claim_next('mac', array['reel'], 90, true)).id) = 'eeeeeeee-0000-0000-0000-000000000002', 'cap reached: the autopilot reel still starts, the manual one waits');
select pg_temp.ok((select (public.claim_next('mac', array['reel'], 90, false)).id) = 'eeeeeeee-0000-0000-0000-000000000001', 'below the cap the manual reel starts as before');
-- quality hold
insert into jobs(id,kind,status,input,output) values ('eeeeeeee-0000-0000-0000-000000000003','reel','running','{"autopilot":"x"}','{"autopilot_hold":true}'),
                                                     ('eeeeeeee-0000-0000-0000-000000000004','reel','running','{"autopilot":"x"}','{}');
update jobs set status='review' where id in ('eeeeeeee-0000-0000-0000-000000000003','eeeeeeee-0000-0000-0000-000000000004');
update jobs set output = output || jsonb_build_object('review_deadline', now() - interval '1 s') where id in ('eeeeeeee-0000-0000-0000-000000000003','eeeeeeee-0000-0000-0000-000000000004');
select public.auto_approve_due();
select pg_temp.ok((select status from jobs where id='eeeeeeee-0000-0000-0000-000000000003')='review', 'a reel the autopilot held back is never auto-posted');
select pg_temp.ok((select status||'/'||(output->'ig'->>'state') from jobs where id='eeeeeeee-0000-0000-0000-000000000004')='done/scheduled', 'a good autopilot reel posts by itself after the window');
-- results merge
update jobs set output = output || '{"fb":{"state":"posted"}}' where id='eeeeeeee-0000-0000-0000-000000000004';
select public.set_reel_stats('eeeeeeee-0000-0000-0000-000000000004', '{"views": 1200, "shares": 9}');
select pg_temp.ok((select output->'ig_stats'->>'views' = '1200' and output->'fb'->>'state' = 'posted' and output->'ig'->>'state'='scheduled' from jobs where id='eeeeeeee-0000-0000-0000-000000000004'), 'results are merged without touching posting state');
delete from jobs where id::text like 'eeeeeeee%'; delete from jobs where kind='suggest' and input ? 'autopilot';
update settings set value = '{"on": true, "time": "13:30", "tz": "Asia/Kolkata"}' where key='autopilot';
select 'ALL AUTOPILOT DB CHECKS PASSED';
