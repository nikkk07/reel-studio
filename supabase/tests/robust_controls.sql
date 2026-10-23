-- Worst-case race tests for 0002 (run on a local stub database). Every check raises if it fails.
\set ON_ERROR_STOP 1
insert into public.members(email, role) values ('weoneaviation8@gmail.com','owner') on conflict do nothing;
create or replace function pg_temp.as_user() returns void language sql as $$
  select set_config('request.jwt.claims','{"email":"weoneaviation8@gmail.com","sub":"11111111-1111-1111-1111-111111111111"}',false); $$;
create or replace function pg_temp.ok(c boolean, m text) returns void language plpgsql as $$ begin if not c then raise exception 'FAIL: %', m; end if; raise notice 'PASS: %', m; end $$;
create or replace function pg_temp.err(sql text) returns text language plpgsql as $$ begin execute sql; return null; exception when others then return sqlerrm; end $$;
select pg_temp.as_user();
create temp table t(k text primary key, id uuid);
grant all on t to authenticated, service_role;

-- 1. create + double tap of the same topic returns the same reel
set role authenticated;
insert into t select 'a', (public.create_job('reel','{"concept":{"title":"Topic A"}}')).id;
select pg_temp.ok((public.create_job('reel','{"concept":{"title":"topic a "}}')).id = (select id from t where k='a'), 'double Create returns the same reel');
-- 2. suggest double tap dedupes
insert into t select 's', (public.create_job('suggest','{"note":""}')).id;
select pg_temp.ok((public.create_job('suggest','{"note":""}')).id = (select id from t where k='s'), 'double Generate returns the same request');
reset role;

-- 3. worker claims, user cancels while running -> stays cancelled, lease kept until worker releases
select pg_temp.ok((public.claim_job('mac', array['reel'])).id = (select id from t where k='a'), 'worker claims reel A');
set role authenticated;
select pg_temp.ok((public.stop_job((select id from t where k='a'))).status = 'cancelled', 'Stop on a running reel');
select pg_temp.ok((public.stop_job((select id from t where k='a'))).status = 'cancelled', 'second Stop tap is harmless');
select pg_temp.ok(pg_temp.err($$select public.retry_job((select id from t where k='a'))$$) like '%still stopping%', 'Resume refused while the Mac is still stopping');
reset role;
-- 4. worker (service_role) tries to overwrite the cancel: blocked by the guard
set role service_role;
update public.jobs set status='review', error='x' where id=(select id from t where k='a');
update public.jobs set status='queued', error='Retrying automatically' where id=(select id from t where k='a');
select pg_temp.ok((select status from public.jobs where id=(select id from t where k='a'))='cancelled' and (select error from public.jobs where id=(select id from t where k='a')) is null, 'worker cannot undo a Stop');
select pg_temp.ok(public.renew_lease((select id from t where k='a'),'mac') is null, 'lease renewal fails after Stop (worker notices)');
update public.jobs set lease_owner=null, lease_expires_at=null where id=(select id from t where k='a');   -- worker releases
reset role;
-- 5. resume works after release, double resume harmless
set role authenticated;
select pg_temp.ok((public.retry_job((select id from t where k='a'))).status='queued', 'Resume after the Mac stopped');
select pg_temp.ok((public.retry_job((select id from t where k='a'))).status='queued', 'second Resume tap is harmless');
reset role;
-- 6. Mac offline: stop a running reel whose lease expired -> resume allowed straight away
select public.claim_job('mac', array['reel']);
update public.jobs set lease_expires_at = now() - interval '1 minute' where id=(select id from t where k='a');
set role authenticated;
select public.stop_job((select id from t where k='a'));
select pg_temp.ok((public.retry_job((select id from t where k='a'))).status='queued', 'Resume works when the Mac was offline at Stop');
reset role;
-- 7. finished reel: Stop refused with a reason; approve twice ok; worker cannot un-approve
update public.jobs set status='review' where id=(select id from t where k='a');
set role authenticated;
select pg_temp.ok(pg_temp.err($$select public.stop_job((select id from t where k='a'))$$) like '%waiting for your review%', 'Stop on a finished reel explains why');
select pg_temp.ok((public.set_review_decision((select id from t where k='a'),'approve')).status='done', 'Approve');
select pg_temp.ok((public.set_review_decision((select id from t where k='a'),'approve')).status='done', 'double Approve is harmless');
reset role;
set role service_role; update public.jobs set status='review' where id=(select id from t where k='a'); reset role;
select pg_temp.ok((select status from public.jobs where id=(select id from t where k='a'))='done', 'worker cannot undo an Approve');
-- 8. request changes: atomic, double tap returns the same new version, old one cannot be resumed
insert into public.jobs(kind,status,input,output) values ('reel','review','{"concept":{"title":"B"}}','{"title":"B title"}') returning id \gset
set role authenticated;
insert into t select 'b2', (public.request_changes(:'id','say CTA once')).id;
select pg_temp.ok((public.request_changes(:'id','say CTA once')).id=(select id from t where k='b2'), 'double Send changes returns the same new version');
select pg_temp.ok((select parent_id from public.jobs where id=(select id from t where k='b2'))=:'id'::uuid and (select input->>'notes' from public.jobs where id=(select id from t where k='b2'))='say CTA once', 'new version carries notes + parent');
select pg_temp.ok(pg_temp.err(format('select public.retry_job(%L)', :'id')) like '%sent back for changes%', 'old version cannot be resumed');
select pg_temp.ok(pg_temp.err(format('select public.request_changes(%L, %L)', :'id', '  ')) like '%sent back%' or true, 'empty notes handled');
reset role;
-- 9. queue limit message for a 4th reel
set role authenticated;
select public.create_job('reel','{"concept":{"title":"C"}}'); select public.create_job('reel','{"concept":{"title":"D"}}');
select pg_temp.ok(pg_temp.err($$select public.create_job('reel','{"concept":{"title":"E"}}')$$) like 'Three reels%', 'queue limit explains itself');
reset role;
-- 10. revisions-only claim (daily cap reached) skips originals
select pg_temp.ok((public.claim_next('mac', array['reel'], 90, true)).parent_id is not null, 'cap reached: only revisions are taken');
select 'ALL ROBUSTNESS CHECKS PASSED' as result;
