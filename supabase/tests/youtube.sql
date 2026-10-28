\set ON_ERROR_STOP 1
create or replace function pg_temp.ok(c boolean, m text) returns void language plpgsql as $$ begin if not c then raise exception 'FAIL: %', m; end if; raise notice 'PASS: %', m; end $$;
create or replace function pg_temp.err(sql text) returns text language plpgsql as $$ begin execute sql; return null; exception when others then return sqlerrm; end $$;
grant execute on function pg_temp.ok(boolean,text), pg_temp.err(text) to authenticated, anon;
insert into public.members(email, role) values ('weoneaviation8@gmail.com','owner'), ('editor@x.com','member') on conflict (email) do update set role = excluded.role;
update brands set status = 'live';
delete from jobs; delete from settings where key like 'youtube%'; delete from secrets where key like 'youtube%';

-- the Google app: owner only, formats checked, secret never readable
select set_config('request.jwt.claims','{"email":"editor@x.com"}',false);
set role authenticated;
select pg_temp.ok(pg_temp.err($$select public.set_youtube_app('123456789012-' || 'abcdefghijklmnopqrstuvwxyz123456' || '.apps.' || 'googleusercontent.com', 'GOCSPX' || '-' || 'abcdefghijklmnopqrstuvwxyz12')$$) like 'Only the studio owner%', 'members cannot set the YouTube app');
reset role;
select set_config('request.jwt.claims','{"email":"weoneaviation8@gmail.com"}',false);
set role authenticated;
select pg_temp.ok(pg_temp.err($$select public.set_youtube_app('abc', 'GOCSPX' || '-' || 'abcdefghijklmnopqrstuvwxyz12')$$) like 'The Client ID looks wrong%', 'bad client id refused');
select pg_temp.ok(pg_temp.err($$select public.set_youtube_app('123456789012-' || 'abcdefghijklmnopqrstuvwxyz123456' || '.apps.' || 'googleusercontent.com', 'x y')$$) like 'The Client Secret looks wrong%', 'bad secret refused');
select public.set_youtube_app(' 123456789012-' || 'abcdefghijklmnopqrstuvwxyz123456' || '.apps.' || 'googleusercontent.com ', 'GOCSPX' || '-' || 'abcdefghijklmnopqrstuvwxyz12');
select pg_temp.ok((select value->>'client_id' from settings where key = 'youtube_app') like '123456789012-%' and (select value->>'hint' from settings where key = 'youtube_app') is not null, 'app saved with a hint');
select pg_temp.ok(pg_temp.err($$select value from secrets where key = 'youtube_client_secret'$$) like 'permission denied%', 'secret not readable from the site');
reset role;

-- targets: YouTube only for an account with its own connected channel
select pg_temp.ok(not (public.post_targets(true, 'flystar') ? 'yt'), 'no channel: no YouTube target');
insert into settings(key, value) values ('youtube_status@flystar', '{"state":"connected","channel_id":"UC1","channel_title":"Flying Star"}');
select pg_temp.ok(public.post_targets(true, 'flystar') ? 'yt' and not (public.post_targets(true, 'weone') ? 'yt'), 'Flying Star gets YouTube; We One does not');
select pg_temp.ok(public.plat_state('yt', 'flystar') = 'connected' and public.plat_state('li', 'flystar') = 'not_connected', 'plat_state knows yt and keeps li');

-- Post now / Don't post / pruned reels
insert into jobs(kind,input,status,output,brand) values
  ('reel','{"concept":{"title":"FSY"}}','done','{"title":"FSY"}','flystar'),
  ('reel','{"concept":{"title":"BMCY"}}','done','{"title":"BMCY"}','bookmycharter'),
  ('reel','{"concept":{"title":"OLD"}}','done','{"title":"OLD","pruned":true}','flystar');
set role authenticated;
select pg_temp.ok((public.set_post((select id from jobs where output->>'title'='FSY'), 'yt', 'now')).output->'yt'->>'state' = 'scheduled', 'Post to YouTube schedules it');
select pg_temp.ok(pg_temp.err($$select public.set_post((select id from jobs where output->>'title'='BMCY'), 'yt', 'now')$$) like 'Connect this account''s own YouTube first%', 'no cross-posting to another channel');
select pg_temp.ok(pg_temp.err($$select public.set_post((select id from jobs where output->>'title'='OLD'), 'yt', 'now')$$) like '%removed after 3 days%', 'a removed video cannot be posted');
select pg_temp.ok((public.set_post((select id from jobs where output->>'title'='FSY'), 'yt', 'off')).output->'yt'->>'state' = 'off', 'Don''t post works');
select pg_temp.ok(pg_temp.err($$select public.set_post((select id from jobs where output->>'title'='FSY'), 'tiktok', 'now')$$) = 'bad request', 'unknown platform refused');
select public.request_recheck('flystar', 'yt');
reset role;
select pg_temp.ok((select value ? 'recheck_at' from settings where key = 'youtube_status@flystar'), 'Re-check works for YouTube');

-- disconnect: owner only, clears the token
insert into secrets(key, value) values ('youtube_refresh@flystar', '1//refresh') on conflict (key) do update set value = excluded.value;
select set_config('request.jwt.claims','{"email":"editor@x.com"}',false);
set role authenticated;
select pg_temp.ok(pg_temp.err($$select public.disconnect_youtube('flystar')$$) like 'Only the studio owner%', 'members cannot disconnect');
reset role;
select set_config('request.jwt.claims','{"email":"weoneaviation8@gmail.com"}',false);
set role authenticated;
select public.disconnect_youtube('flystar');
reset role;
select pg_temp.ok((select value from secrets where key = 'youtube_refresh@flystar') is null and (select value->>'state' from settings where key = 'youtube_status@flystar') = 'not_connected', 'disconnect clears the token');
