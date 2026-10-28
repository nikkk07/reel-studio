\set ON_ERROR_STOP 1
create or replace function pg_temp.ok(c boolean, m text) returns void language plpgsql as $$ begin if not c then raise exception 'FAIL: %', m; end if; raise notice 'PASS: %', m; end $$;
create or replace function pg_temp.err(sql text) returns text language plpgsql as $$ begin execute sql; return null; exception when others then return sqlerrm; end $$;
grant execute on function pg_temp.ok(boolean,text), pg_temp.err(text) to authenticated, anon;
insert into public.members(email, role) values ('weoneaviation8@gmail.com','owner'), ('editor@x.com','member') on conflict (email) do update set role = excluded.role;
update brands set status = 'live';
update settings set value = value || '{"auto": true}'::jsonb where key = 'post_schedule';
delete from jobs; delete from settings where key like 'post_slots%';
insert into settings(key, value) values ('instagram_status@flystar', '{"state":"connected","user_id":"1"}') on conflict (key) do update set value = excluded.value;

-- off: approval posts at once, exactly as before
select pg_temp.ok((public.post_targets(false, 'flystar') -> 'ig' ->> 'at')::timestamptz <= now() + interval '1 second' and not (public.post_targets(false, 'flystar') ? 'post_slot'), 'off: posts immediately');

-- members set times; validation
select set_config('request.jwt.claims','{"email":"editor@x.com"}',false);
set role authenticated;
select pg_temp.ok(pg_temp.err($$select public.set_post_slots('flystar', true, array['7pm'])$$) like 'Times must%', 'bad time refused');
select pg_temp.ok(pg_temp.err($$select public.set_post_slots('flystar', true, array[]::text[])$$) like 'Choose 1 to 3%', 'no time refused when on');
select pg_temp.ok(pg_temp.err($$select public.set_post_slots('flystar', true, array['09:00','12:00','15:00','18:00'])$$) like 'Choose 1 to 3%', 'more than 3 refused');
select pg_temp.ok(pg_temp.err($$select public.set_post_slots('nobody', true, array['09:00'])$$) like 'Unknown account%', 'unknown account refused');
select public.set_post_slots('flystar', true, array['19:30', '12:00', '19:30']);
reset role;
select pg_temp.ok((select value->'times' from settings where key = 'post_slots@flystar') = '["12:00","19:30"]'::jsonb, 'times de-duplicated and sorted');

-- on: the next slot in India time, later than now, never in the past
select pg_temp.ok(public.next_post_slot('flystar') > now() and to_char(public.next_post_slot('flystar') at time zone 'Asia/Kolkata', 'HH24:MI') in ('12:00','19:30'), 'next slot is a chosen India time in the future');
select pg_temp.ok(public.next_post_slot('weone') is null and public.next_post_slot('bookmycharter') is null, 'other accounts unaffected');

-- two reels approved together get two different slots (review timer)
insert into jobs(kind,input,status,output,brand) values
  ('reel','{"concept":{"title":"S1"}}','review','{"title":"S1","review_deadline":"2000-01-01T00:00:00Z"}','flystar'),
  ('reel','{"concept":{"title":"S2"}}','review','{"title":"S2","review_deadline":"2000-01-01T00:00:00Z"}','flystar');
update jobs set output = output || '{"review_deadline":"2000-01-01T00:00:00Z"}' where output->>'title' in ('S1','S2');
select pg_temp.ok(public.auto_approve_due() = 2, 'both reels approved');
select pg_temp.ok((select count(distinct output->>'post_slot') from jobs where output->>'title' in ('S1','S2')) = 2, 'two reels, two different slots');
select pg_temp.ok((select bool_and((output->'ig'->>'at')::timestamptz = (output->>'post_slot')::timestamptz and (output->'ig'->>'at')::timestamptz > now()) from jobs where output->>'title' in ('S1','S2')), 'Instagram waits for the slot');

-- approving by hand books the next free slot too; Post now overrides it
insert into jobs(kind,input,status,output,brand) values ('reel','{"concept":{"title":"S3"}}','review','{"title":"S3"}','flystar');
select set_config('request.jwt.claims','{"email":"editor@x.com"}',false);
set role authenticated;
select pg_temp.ok((public.set_review_decision((select id from jobs where output->>'title'='S3'), 'approve')).output ? 'post_slot', 'manual approval is scheduled');
select pg_temp.ok(((public.set_post((select id from jobs where output->>'title'='S3'), 'ig', 'now')).output->'ig'->>'at')::timestamptz <= now() + interval '1 second', 'Post now still posts at once');
select public.set_post_slots('flystar', false, array['19:30']);
reset role;
select pg_temp.ok(public.next_post_slot('flystar') is null and (public.post_targets(false, 'flystar') -> 'ig' ->> 'at')::timestamptz <= now() + interval '1 second', 'switched off: back to posting at once');
select pg_temp.ok((select count(*) from jobs where output->>'title' in ('S1','S2') and output->'ig'->>'state' = 'scheduled') = 2, 'already-booked reels keep their slot');

-- a brand without connections still behaves as before (no auto-approval)
insert into jobs(kind,input,status,output,brand) values ('reel','{"concept":{"title":"B1"}}','review','{"title":"B1","review_deadline":"2000-01-01T00:00:00Z"}','bookmycharter');
update jobs set output = output || '{"review_deadline":"2000-01-01T00:00:00Z"}' where output->>'title' = 'B1';
delete from settings where key like '%@bookmycharter' and key not like 'dm%' and key not like 'autopilot%';
select public.auto_approve_due();
select pg_temp.ok((select status from jobs where output->>'title'='B1') = 'review', 'account with nothing connected is never auto-approved');
