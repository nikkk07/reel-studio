\set ON_ERROR_STOP 1
create or replace function pg_temp.ok(c boolean, m text) returns void language plpgsql as $$ begin if not c then raise exception 'FAIL: %', m; end if; raise notice 'PASS: %', m; end $$;
create or replace function pg_temp.err(sql text) returns text language plpgsql as $$ begin execute sql; return null; exception when others then return sqlerrm; end $$;
grant execute on function pg_temp.ok(boolean,text), pg_temp.err(text) to authenticated, anon;
insert into public.members(email, role) values ('weoneaviation8@gmail.com','owner'), ('editor@x.com','member') on conflict (email) do update set role = excluded.role;
update brands set status = 'live';
update settings set value = value || '{"auto": true, "linkedin": true}'::jsonb where key = 'post_schedule';
delete from jobs;

-- keys of each account are separate; We One Aviation keeps its original keys
select pg_temp.ok(public.bkey('instagram_token', 'weone') = 'instagram_token' and public.bkey('instagram_token', null) = 'instagram_token', 'We One keeps its original keys');
select pg_temp.ok(public.bkey('instagram_token', 'flystar') = 'instagram_token@flystar', 'other accounts get their own keys');

-- owner connects Flying Star's own Instagram
select set_config('request.jwt.claims','{"email":"weoneaviation8@gmail.com"}',false);
set role authenticated;
select public.set_instagram_token('IGAAflystar' || repeat('x', 60), 'flystar');
reset role;
select pg_temp.ok((select value from secrets where key = 'instagram_token@flystar') like 'IGAAflystar%', 'token stored under Flying Star');
select pg_temp.ok((select value from secrets where key = 'instagram_token') is distinct from (select value from secrets where key = 'instagram_token@flystar'), 'We One''s token untouched');
select pg_temp.ok((select value->>'state' from settings where key = 'instagram_status@flystar') = 'checking', 'Flying Star status: checking');
set role authenticated;
select pg_temp.ok(pg_temp.err($$select value from secrets$$) like 'permission denied%', 'nobody on the site can read tokens');
select pg_temp.ok(pg_temp.err($$select public.set_instagram_token('IGAA' || repeat('x', 60), 'hacker')$$) like 'Unknown account%', 'unknown account refused');
select pg_temp.ok(pg_temp.err($$select public.set_instagram_token('short', 'flystar')$$) like 'That does not look like%', 'bad token refused');
reset role;

-- posting targets per account
update settings set value = '{"state":"connected","username":"flyingstaraviator","user_id":"1"}' where key = 'instagram_status@flystar';
select pg_temp.ok(public.post_targets(true, 'flystar') ? 'ig' and not (public.post_targets(true, 'flystar') ? 'fb'), 'Flying Star posts to its own Instagram only');
select pg_temp.ok(public.post_targets(true, 'bookmycharter') = '{}'::jsonb, 'Book My Charter (nothing connected) posts nowhere');
select pg_temp.ok(public.post_targets(true, 'weone') ? 'ig', 'We One still always gets Instagram');
insert into settings(key, value) values ('facebook_status@legalgroup', '{"state":"connected","page_id":"9"}');
select pg_temp.ok(public.post_targets(false, 'legalgroup') ? 'fb' and not (public.post_targets(false, 'legalgroup') ? 'ig'), 'Legal Group: Facebook only');

-- the review timer: accounts with a connection auto-post, accounts without never do
insert into jobs(kind,input,status,output) values
  ('reel','{"concept":{"title":"FS1"},"brand":"flystar"}','review','{"title":"FS1"}'),
  ('reel','{"concept":{"title":"BMC1"},"brand":"bookmycharter"}','review','{"title":"BMC1"}');
update jobs set output = output || '{"review_deadline":"2000-01-01T00:00:00Z"}' where input->'concept'->>'title' in ('FS1','BMC1');
select public.auto_approve_due();
select pg_temp.ok((select status from jobs where input->'concept'->>'title'='FS1') = 'done' and (select output->'ig'->>'state' from jobs where input->'concept'->>'title'='FS1') = 'scheduled', 'Flying Star reel auto-posts to its own Instagram');
select pg_temp.ok((select output ? 'fb' or output ? 'li' from jobs where input->'concept'->>'title'='FS1') = false, 'no Facebook/LinkedIn for Flying Star (not connected)');
select pg_temp.ok((select status from jobs where input->'concept'->>'title'='BMC1') = 'review', 'Book My Charter reel waits for review (nothing connected)');

-- Post now only to the account's own connected platform
update jobs set status = 'done' where input->'concept'->>'title'='BMC1';
set role authenticated;
select pg_temp.ok(pg_temp.err($$select public.set_post((select id from jobs where input->'concept'->>'title'='BMC1'), 'ig', 'now')$$) like 'Connect this account''s own Instagram first%', 'Book My Charter cannot post (not connected)');
select pg_temp.ok(pg_temp.err($$select public.set_post((select id from jobs where input->'concept'->>'title'='FS1'), 'fb', 'now')$$) like 'Connect this account''s own Facebook first%', 'Flying Star cannot post to Facebook it does not have');
select pg_temp.ok((public.set_post((select id from jobs where input->'concept'->>'title'='BMC1'), 'ig', 'off')).output->'ig'->>'state' = 'off', 'Don''t post always works');

-- disconnect is per account
select public.disconnect_instagram('flystar');
reset role;
select pg_temp.ok((select value from secrets where key = 'instagram_token@flystar') is null and (select value->>'state' from settings where key = 'instagram_status@flystar') = 'not_connected', 'Flying Star disconnected');
select pg_temp.ok((select value from secrets where key = 'instagram_token') is not distinct from (select value from secrets where key = 'instagram_token'), 'We One unaffected');
select pg_temp.ok(public.post_targets(true, 'flystar') = '{}'::jsonb, 'after disconnect Flying Star posts nowhere');

-- members (not owner) cannot connect or change keys
select set_config('request.jwt.claims','{"email":"editor@x.com"}',false);
set role authenticated;
select pg_temp.ok(pg_temp.err($$select public.set_instagram_token('IGAA' || repeat('x', 60), 'flystar')$$) like 'Only the studio owner%', 'members cannot connect Instagram');
select pg_temp.ok(pg_temp.err($$select public.disconnect_facebook('legalgroup')$$) like 'Only the studio owner%', 'members cannot disconnect');
select pg_temp.ok(pg_temp.err($$select public.set_studio_key('pexels', repeat('a', 56))$$) like 'Only the studio owner%', 'members cannot change studio keys');
select pg_temp.ok((public.update_brand('flystar', '{"tagline":"CPL ground school"}')).tagline = 'CPL ground school', 'members can edit an account''s profile');
reset role;

-- studio keys: write-only, last 4 characters visible, validation
select set_config('request.jwt.claims','{"email":"weoneaviation8@gmail.com"}',false);
set role authenticated;
select pg_temp.ok((public.set_studio_key('pexels', repeat('a', 52) || 'WXYZ'))->>'hint' = '…WXYZ', 'Pexels key saved; only the last 4 characters shown');
select pg_temp.ok((select value->'pexels'->>'state' from settings where key = 'studio_keys') = 'checking', 'Pexels key waits for the cloud check');
select pg_temp.ok((select value::text from settings where key = 'studio_keys') not like '%aaaaaaaa%', 'the full key never appears in settings');
select pg_temp.ok(pg_temp.err($$select public.set_studio_key('pexels', 'not a key!')$$) like 'That does not look like a Pexels key%', 'bad Pexels key refused');
select pg_temp.ok(pg_temp.err($$select public.set_studio_key('pixabay', 'abc')$$) like 'That does not look like a Pixabay key%', 'bad Pixabay key refused');
select pg_temp.ok((public.set_studio_key('pixabay', '12345678-abcdef0123456789abcdef01'))->>'state' = 'checking', 'Pixabay key saved');
select pg_temp.ok(pg_temp.err($$select public.set_studio_key('openai', 'x')$$) like 'Unknown key%', 'only known keys');
select pg_temp.ok((public.set_studio_key('pixabay', ''))->>'state' = 'not_set', 'removing a key: the Mac falls back to its own .env');
select pg_temp.ok((public.set_meta_app('123456789', repeat('ab', 16)))->>'app_id' = '123456789', 'Meta app saved');
select pg_temp.ok(pg_temp.err($$select public.set_meta_app('abc', 'x')$$) like 'The App ID%', 'bad Meta app refused');
select public.set_linkedin_app('abcdEFGH12', 'secretSECRET123');
select pg_temp.ok((select value->>'hint' from settings where key = 'linkedin_app') = '…T123', 'LinkedIn app shows only a hint');
reset role;
select pg_temp.ok((select value from secrets where key = 'api_pexels') = repeat('a', 52) || 'WXYZ' and (select value from secrets where key = 'api_pixabay') is null, 'keys stored server side');
set role authenticated;

-- account profile
select pg_temp.ok((public.update_brand('flystar', '{"links":{"instagram":"https://www.instagram.com/flyingstaraviator/","linkedin":"https://www.linkedin.com/company/flying-star"}}')).instagram = 'flyingstaraviator', 'Instagram handle taken from the link');
select pg_temp.ok(pg_temp.err($$select public.update_brand('flystar', '{"links":{"instagram":"javascript:alert(1)"}}')$$) like 'Use the full link%', 'only real social links');
select pg_temp.ok(pg_temp.err($$select public.update_brand('flystar', '{"website":"ftp://x"}')$$) like 'Give the website%', 'website must be https');
select pg_temp.ok((public.update_brand('flystar', '{"links":{"instagram":""}}')).instagram is null, 'clearing a link');
select pg_temp.ok(pg_temp.err($$select public.update_brand('nope', '{}')$$) like 'Unknown account%', 'unknown account');
select pg_temp.ok((public.set_worker_settings(4))->>'daily_cap' = '4', 'daily reel limit set from the site');
select pg_temp.ok(pg_temp.err($$select public.set_worker_settings(50)$$) like 'Choose between%', 'limit kept sane');
reset role;

-- voice model per language (owner only, known models only, Hindi pack only for the Hinglish voice)
select set_config('request.jwt.claims','{"email":"weoneaviation8@gmail.com"}',false);
set role authenticated;
select pg_temp.ok((public.set_voice_models('hi', 'v3'))->>'voice_hi' = 'hi', 'voice models saved');
select pg_temp.ok((public.set_voice_models('hi', 'hi'))->>'voice_en' = 'hi', 'Hindi pack allowed for the English voice (Indian English)');
select pg_temp.ok(pg_temp.err($$select public.set_voice_models('hi', 'x')$$) like 'Unknown English voice model%', 'unknown English model refused');
select pg_temp.ok((public.set_voice_style('natural', 'expressive'))->>'style_en' = 'expressive', 'voice style saved');
select pg_temp.ok(pg_temp.err($$select public.set_voice_style('loud', 'natural')$$) like 'Choose natural or expressive%', 'unknown style refused');
select pg_temp.ok(pg_temp.err($$select public.set_voice_models('x', 'v3')$$) like 'Unknown Hinglish voice model%', 'unknown model refused');
select set_config('request.jwt.claims','{"email":"editor@x.com"}',false);
select pg_temp.ok(pg_temp.err($$select public.set_voice_models('v2', 'v2')$$) like 'Only the studio owner%', 'members cannot change the voice model');
select pg_temp.ok(pg_temp.err($$select public.set_voice_style('natural', 'natural')$$) like 'Only the studio owner%', 'members cannot change the voice style');
reset role;

-- Cloudflare R2: owner only, keys write-only, formats checked
select set_config('request.jwt.claims','{"email":"weoneaviation8@gmail.com"}',false);
set role authenticated;
select pg_temp.ok((public.set_r2(repeat('a',32), 'reel-studio', repeat('b',32), repeat('c',64)))->>'state' = 'checking', 'R2 saved; waits for the cloud check');
select pg_temp.ok((select value::text from settings where key = 'r2') not like '%cccccccc%', 'the R2 secret never appears in settings');
select pg_temp.ok(pg_temp.err($$select public.set_r2('x', 'b', 'k', 's')$$) like 'The Account ID%', 'bad R2 account id refused');
select pg_temp.ok(pg_temp.err($$select public.set_r2(repeat('a',32), 'Bad_Bucket', repeat('b',32), repeat('c',64))$$) like 'Use the bucket name%', 'bad bucket name refused');
select pg_temp.ok(pg_temp.err($$select value from secrets where key = 'r2_secret_access_key'$$) like 'permission denied%', 'the site cannot read R2 keys');
select pg_temp.ok(pg_temp.err($$select public.merge_reel_output(gen_random_uuid(), '{}')$$) like 'permission denied%', 'the site cannot use the cloud poster''s merge');
-- Comment-to-DM
select pg_temp.ok((public.set_dm('flystar', true, 'cpl', 'Hi {name}! CPL details: https://www.flystar.co.in/courses/cpl', true))->>'state' = 'checking', 'Comment-to-DM saved, keyword in capitals, waits for the cloud check');
select pg_temp.ok((select value->'keywords'->>0 from settings where key = 'dm@flystar') = 'CPL', 'keyword stored as CPL');
select pg_temp.ok(pg_temp.err($$select public.set_dm('flystar', true, 'two words', 'Hi there, details here please read', true)$$) like 'Use one keyword%', 'one keyword only');
select pg_temp.ok(pg_temp.err($$select public.set_dm('flystar', true, 'CPL', 'short', true)$$) like 'Write a message%', 'message length checked');
select pg_temp.ok(pg_temp.err($$insert into dm_log(brand, comment_id) values ('weone', 'x')$$) like 'permission denied%' or pg_temp.err($$insert into dm_log(brand, comment_id) values ('weone', 'x')$$) like '%row-level security%', 'the site cannot write the DM log');
reset role;
select pg_temp.ok((select count(*) from settings where key in ('dm', 'dm@bookmycharter', 'dm@flystar', 'dm@legalgroup')) = 4, 'every account starts with its own Comment-to-DM message');
