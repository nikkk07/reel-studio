\set ON_ERROR_STOP 1
create or replace function pg_temp.ok(c boolean, m text) returns void language plpgsql as $$ begin if not c then raise exception 'FAIL: %', m; end if; raise notice 'PASS: %', m; end $$;
create or replace function pg_temp.err(sql text) returns text language plpgsql as $$ begin execute sql; return null; exception when others then return sqlerrm; end $$;
grant execute on function pg_temp.ok(boolean,text), pg_temp.err(text) to authenticated, anon;
delete from jobs where kind in ('suggest','optimize'); update jobs set status='cancelled' where status in ('queued','running','waiting_quota','review');
select pg_temp.ok((select count(*) from jobs where brand <> 'weone') = 0, 'all existing reels stay with We One Aviation');
select set_config('request.jwt.claims','{"email":"weoneaviation8@gmail.com"}',false);
set role authenticated;
select pg_temp.ok((select count(*) from brands) = 4, 'members see the 4 accounts');
select pg_temp.ok((public.create_job('reel', '{"concept":{"title":"T1"}}')).brand = 'weone', 'no account given -> We One Aviation');
select pg_temp.ok((public.create_job('reel', '{"concept":{"title":"T2"},"brand":"weone"}')).brand = 'weone', 'We One Aviation reel');
select pg_temp.ok(pg_temp.err($$select public.create_job('reel', '{"concept":{"title":"T3"},"brand":"bookmycharter"}')$$) like 'This account is still being set up%', 'account in setup -> refused with a clear message');
select pg_temp.ok(pg_temp.err($$select public.create_job('suggest', '{"brand":"legalgroup"}')$$) like 'This account is still being set up%', 'ideas for an account in setup -> refused');
select pg_temp.ok(pg_temp.err($$select public.create_job('reel', '{"concept":{"title":"T4"},"brand":"hacker"}')$$) like 'Unknown account%', 'made-up account -> refused');
select pg_temp.ok(pg_temp.err($$insert into brands(id,name) values ('x','x')$$) like 'permission denied%', 'the site cannot add or change accounts');
select pg_temp.ok(pg_temp.err($$update jobs set brand='flystar'$$) like 'permission denied%', 'the site cannot move reels between accounts');
reset role;
update brands set status='live' where id='flystar';
set role authenticated;
select pg_temp.ok((public.create_job('reel', '{"concept":{"title":"T1"},"brand":"flystar"}')).brand = 'flystar', 'once live, the account gets its own reels (same title as another account is fine)');
reset role;
update jobs set status='review' where input->'concept'->>'title'='T1' and brand='flystar';
set role authenticated;
select pg_temp.ok((public.request_changes((select id from jobs where input->'concept'->>'title'='T1' and brand='flystar'), 'shorter')).brand = 'flystar', 'a revision stays in its account');
reset role;
insert into jobs(kind,input,parent_id) values ('reel','{"notes":"x"}',(select id from jobs where input->'concept'->>'title'='T2' limit 1));
select pg_temp.ok((select brand from jobs where input->>'notes'='x') = 'weone', 'worker-made revisions inherit the account');

-- no cross-posting: an account without its own connections never posts to We One's (or anyone's) Instagram
reset role;
update settings set value = value || '{"auto": true}'::jsonb where key = 'post_schedule';
update brands set status='live' where id='bookmycharter';
insert into jobs(kind,input,status,output) values
  ('reel','{"concept":{"title":"BMC1"},"brand":"bookmycharter"}','review','{"title":"BMC1","video":"x/reel.mp4","review_deadline":"2000-01-01T00:00:00Z"}'),
  ('reel','{"concept":{"title":"BMC2"},"brand":"bookmycharter"}','review','{"title":"BMC2","video":"x/reel.mp4"}');
select pg_temp.ok((select count(*) from jobs where input->'concept'->>'title' in ('BMC1','BMC2') and brand='bookmycharter') = 2, 'Book My Charter reels are filed under Book My Charter');
select public.auto_approve_due();
select pg_temp.ok((select status from jobs where input->'concept'->>'title'='BMC1') = 'review', 'the 5-minute auto-approve never touches another account''s reel');
select pg_temp.ok(public.post_targets(true, 'bookmycharter') = '{}'::jsonb and public.post_targets(false, 'flystar') = '{}'::jsonb, 'accounts without their own connections have no posting targets');
select set_config('request.jwt.claims','{"email":"weoneaviation8@gmail.com"}',false);
set role authenticated;
select pg_temp.ok(pg_temp.err($$select public.post_targets(true, 'weone')$$) like 'permission denied%', 'the site cannot call the posting-target helper');
select public.set_review_decision((select id from jobs where input->'concept'->>'title'='BMC2'), 'approve');
select pg_temp.ok((select status from jobs where input->'concept'->>'title'='BMC2') = 'done', 'approving a Book My Charter reel works');
select pg_temp.ok((select not (output ? 'ig' or output ? 'fb' or output ? 'li') from jobs where input->'concept'->>'title'='BMC2'), 'approved Book My Charter reel is not queued for We One''s Instagram, Facebook or LinkedIn');
select pg_temp.ok(pg_temp.err($$select public.set_post((select id from jobs where input->'concept'->>'title'='BMC2'), 'ig', 'now')$$) like 'Connect this account''s own Instagram first%', 'Post now on a Book My Charter reel is refused (no cross-posting)');
select pg_temp.ok(pg_temp.err($$select public.set_post((select id from jobs where input->'concept'->>'title'='BMC2'), 'ig', 'off')$$) is null, 'cancelling is still allowed');
select pg_temp.ok((public.create_job('suggest', '{"brand":"bookmycharter"}')).brand = 'bookmycharter', 'ideas are requested per account');
select pg_temp.ok((select count(*) from brands where profile ? 'pillars' and id <> 'bookmycharter') = 0, 'Book My Charter''s knowledge lives only in its own profile');
reset role;
update brands set status='setup' where id='bookmycharter';
delete from jobs where brand='bookmycharter';

-- account assets: a logo is uploaded to the account's own folder and only that account can point at it
reset role;
insert into storage.objects(bucket_id, name) values ('brand-assets', 'bookmycharter/logo-1761200000000.png'), ('brand-assets', 'weone/logo-1761200000001.png');
select set_config('request.jwt.claims','{"email":"weoneaviation8@gmail.com"}',false);
set role authenticated;
select pg_temp.ok((public.set_brand_logo('bookmycharter', 'bookmycharter/logo-1761200000000.png')) ->> 'logo_asset' = 'bookmycharter/logo-1761200000000.png', 'a member sets an account logo from its own folder');
select pg_temp.ok((select logo_asset from brands where id = 'bookmycharter') = 'bookmycharter/logo-1761200000000.png' and (select logo_asset from brands where id = 'weone') is null, 'changing one account''s logo leaves the others alone');
select pg_temp.ok(pg_temp.err($$select public.set_brand_logo('bookmycharter', 'weone/logo-1761200000001.png')$$) like 'That file does not belong%', 'one account cannot use another account''s logo file');
select pg_temp.ok(pg_temp.err($$select public.set_brand_logo('bookmycharter', 'bookmycharter/logo-1761200009999.png')$$) like 'Upload the logo first%', 'a file that was never uploaded is refused');
select pg_temp.ok(pg_temp.err($$select public.set_brand_logo('bookmycharter', 'bookmycharter/../weone/logo-1761200000001.png')$$) like 'That file does not belong%', 'path tricks are refused');
select pg_temp.ok(pg_temp.err($$update brands set logo_asset = 'x' where id = 'weone'$$) like 'permission denied%', 'the site cannot write logos directly');
select set_config('request.jwt.claims','{"email":"stranger@x.com"}',false);
select pg_temp.ok(pg_temp.err($$select public.set_brand_logo('weone', 'weone/logo-1761200000001.png')$$) like 'Your account is not on the members list%', 'non-members cannot change logos');
reset role;
update brands set logo_asset = null, logo_updated_at = null; delete from storage.objects where bucket_id = 'brand-assets';
select set_config('request.jwt.claims','{"email":"weoneaviation8@gmail.com"}',false);
select set_config('request.jwt.claims','{"email":"stranger@x.com"}',false); set role authenticated;
select pg_temp.ok((select count(*) from brands) = 0, 'non-members see no accounts');
reset role;
update brands set status='setup' where id='flystar';
delete from jobs where input->'concept'->>'title' in ('T1','T2') or input->>'notes' in ('x','shorter') or (input->>'previous_title') is not null and brand='flystar';
select 'ALL ACCOUNT CHECKS PASSED';
