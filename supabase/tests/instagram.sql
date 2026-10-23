\set ON_ERROR_STOP 1
insert into public.members(email, role) values ('weoneaviation8@gmail.com','owner'), ('editor@x.com','member') on conflict (email) do update set role = excluded.role;
create or replace function pg_temp.ok(c boolean, m text) returns void language plpgsql as $$ begin if not c then raise exception 'FAIL: %', m; end if; raise notice 'PASS: %', m; end $$;
create or replace function pg_temp.err(sql text) returns text language plpgsql as $$ begin execute sql; return null; exception when others then return sqlerrm; end $$;
grant execute on function pg_temp.ok(boolean,text), pg_temp.err(text) to authenticated;
-- member (not owner)
set role authenticated; select set_config('request.jwt.claims','{"email":"editor@x.com"}',false);
select pg_temp.ok(pg_temp.err($$select public.set_instagram_token('IGAA'||repeat('x',60))$$) like 'Only the studio owner%', 'only the owner can connect Instagram');
select pg_temp.ok(pg_temp.err($$select * from public.secrets$$) like '%permission denied%', 'nobody can read the token through the API');
select pg_temp.ok((select count(*) from public.settings) >= 2, 'members can read the schedule and connection status');
-- owner
select set_config('request.jwt.claims','{"email":"weoneaviation8@gmail.com"}',false);
select pg_temp.ok(pg_temp.err($$select public.set_instagram_token('hello')$$) like 'That does not look%', 'junk token rejected with a clear message');
select public.set_instagram_token('  IGAA'||repeat('x',60)||'  ');
select pg_temp.ok((select value->>'state' from public.settings where key='instagram_status')='checking', 'saving a token starts a check');
select pg_temp.ok(pg_temp.err($$select public.set_post_schedule(array['7:30pm'], true)$$) like 'Times must%', 'bad time rejected');
select pg_temp.ok((public.set_post_schedule(array['19:30','13:00','19:30'], true))->'slots' = '["13:00","19:30"]', 'times de-duplicated and sorted');
reset role;
select pg_temp.ok((select value from public.secrets where key='instagram_token') = 'IGAA'||repeat('x',60), 'token stored trimmed (service side only)');
-- approve queues the post; per-reel controls
insert into public.jobs(id,kind,status,input,output) values ('bbbbbbbb-0000-0000-0000-000000000001','reel','review','{}','{"title":"T"}');
set role authenticated;
select public.set_review_decision('bbbbbbbb-0000-0000-0000-000000000001','approve');
reset role;
select pg_temp.ok((select output->'ig'->>'state' from public.jobs where id='bbbbbbbb-0000-0000-0000-000000000001')='queued', 'approving queues the reel for Instagram');
set role authenticated;
select pg_temp.ok((public.set_reel_posting('bbbbbbbb-0000-0000-0000-000000000001','off')).output->'ig'->>'state'='off', 'Don''t post');
select pg_temp.ok((public.set_reel_posting('bbbbbbbb-0000-0000-0000-000000000001','now')).output->'ig'->>'state'='scheduled', 'Post now');
reset role;
update public.jobs set output = jsonb_set(output,'{ig,state}','"posted"') where id='bbbbbbbb-0000-0000-0000-000000000001';
set role authenticated;
select pg_temp.ok(pg_temp.err($$select public.set_reel_posting('bbbbbbbb-0000-0000-0000-000000000001','now')$$) like '%already on Instagram%', 'a posted reel can never be posted twice from the site');
select public.set_post_schedule(array['19:30'], false);
reset role;
insert into public.jobs(id,kind,status,input,output) values ('bbbbbbbb-0000-0000-0000-000000000002','reel','review','{}','{}');
set role authenticated; select public.set_review_decision('bbbbbbbb-0000-0000-0000-000000000002','approve'); reset role;
select pg_temp.ok((select output->'ig' from public.jobs where id='bbbbbbbb-0000-0000-0000-000000000002') is null, 'auto-posting off: approve does not queue');
set role authenticated; select public.set_post_schedule(array['19:30'], true); select public.disconnect_instagram(); reset role;
select pg_temp.ok((select value is null from public.secrets where key='instagram_token'), 'disconnect removes the token');
select 'ALL INSTAGRAM DB CHECKS PASSED';
