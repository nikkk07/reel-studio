-- Reel Studio 0013: the agent team.
--   agents         - the specialised AI agents (registry shown on the site)
--   agent_runs     - every agent call: time, success, grade, which lessons it used (the Resource Manager's evidence)
--   agent_lessons  - what agents learned from results; applied automatically, rolled back automatically if results drop
--   jobs.kind      - 'onboard' (Account Intelligence studies an account) and 'learn' (Analyst + Resource Manager, daily)
--   add_brand / run_onboard / learn_tick - new accounts are studied automatically; every account is re-studied monthly

alter table public.jobs drop constraint if exists jobs_kind_check;
alter table public.jobs add constraint jobs_kind_check check (kind in ('suggest', 'optimize', 'reel', 'onboard', 'learn'));

alter table public.brands add column if not exists links jsonb not null default '{}'::jsonb;     -- instagram / linkedin / facebook URLs
alter table public.brands add column if not exists intel_at timestamptz;                          -- last time Account Intelligence studied it

create table if not exists public.agents (
  id text primary key,
  name text not null,
  role text not null,
  team text not null check (team in ('intelligence', 'strategy', 'production', 'quality', 'learning', 'control')),
  sort int not null default 100
);
alter table public.agents enable row level security;
create policy agents_read on public.agents for select to authenticated using ((select public.is_member()));
revoke all on public.agents from anon, authenticated;
grant select on public.agents to authenticated;

insert into public.agents(id, name, role, team, sort) values
  ('account_intel',    'Account Intelligence', 'Studies each account: website, socials, services, competitors, market trends and rules of its industry; writes the account''s knowledge profile and strategy.', 'intelligence', 1),
  ('trend_scout',      'Trend Scout',          'Finds what the account''s audience is talking about today, verified and ranked by viral potential.', 'strategy', 2),
  ('strategist',       'Strategist',           'Turns the account''s profile, results and trends into topic ideas with the right mix of core and trending reels.', 'strategy', 3),
  ('viral_editor',     'Viral Editor',         'Independently scores topic candidates on hook, shareability, saves, timing and fit; picks Autopilot''s topic.', 'strategy', 4),
  ('fact_checker',     'Fact-Checker',         'Researches the chosen topic and builds a verified fact sheet with sources before anything is written.', 'production', 5),
  ('scriptwriter',     'Scriptwriter',         'Writes the narration, captions, shots and graphics from the fact sheet, built for the first 2 seconds and full watch-through.', 'production', 6),
  ('hook_critic',      'Hook Critic',          'Independent editor: scores the script for hook, retention, payoff, shareability and fact fidelity; sends it back once if weak.', 'quality', 7),
  ('footage_picker',   'Footage Picker',       'Chooses stock clips that show exactly what each line says, and searches again when nothing fits.', 'production', 8),
  ('quality_inspector','Quality Inspector',    'Watches the finished reel frame by frame: captions, clips, graphics, sound; asks for re-picks.', 'quality', 9),
  ('caption_writer',   'Caption Writer',       'Writes the Instagram caption and hashtags for search and sends.', 'production', 10),
  ('analyst',          'Performance Analyst',  'Reads every posted reel''s Instagram results and turns them into lessons for the strategy and writing agents.', 'learning', 11),
  ('resource_manager', 'Resource Manager',     'Grades every agent from its run log (success, retries, time, quality) and writes improvements; watches Claude usage.', 'learning', 12),
  ('supervisor',       'Supervisor',           'Checks every agent''s output against its contract (format, rules, facts, account separation) and sends it back with feedback when it breaks one.', 'control', 13)
on conflict (id) do update set name = excluded.name, role = excluded.role, team = excluded.team, sort = excluded.sort;

create table if not exists public.agent_runs (
  id bigint generated always as identity primary key,
  agent text not null references public.agents(id),
  job_id uuid references public.jobs(id) on delete set null,
  brand text,
  ok boolean not null,
  ms int,
  grade numeric(5,1),           -- 0-100 when the agent's output was graded (critic total, QC score x10, supervisor checks)
  note text,
  lesson_ids bigint[] not null default '{}',
  created_at timestamptz not null default now()
);
create index if not exists agent_runs_agent_idx on public.agent_runs (agent, created_at desc);
create index if not exists agent_runs_job_idx on public.agent_runs (job_id);
alter table public.agent_runs enable row level security;
create policy agent_runs_read on public.agent_runs for select to authenticated using ((select public.is_member()));
revoke all on public.agent_runs from anon, authenticated;
grant select on public.agent_runs to authenticated;

create table if not exists public.agent_lessons (
  id bigint generated always as identity primary key,
  agent text not null references public.agents(id),
  brand text references public.brands(id),          -- null = every account
  lesson text not null check (length(lesson) between 10 and 600),
  why text,
  metric text not null check (metric in ('engagement', 'critic', 'qc', 'success')),
  baseline numeric,
  status text not null default 'active' check (status in ('active', 'proven', 'rolled_back', 'retired')),
  created_by text not null,
  result jsonb,
  created_at timestamptz not null default now(),
  evaluated_at timestamptz
);
create index if not exists agent_lessons_idx on public.agent_lessons (agent, brand, status);
alter table public.agent_lessons enable row level security;
create policy agent_lessons_read on public.agent_lessons for select to authenticated using ((select public.is_member()));
revoke all on public.agent_lessons from anon, authenticated;
grant select on public.agent_lessons to authenticated;

-- queue a study of one account (never two at once)
create or replace function public.queue_onboard(p_brand text, p_trigger text) returns uuid language plpgsql security definer set search_path = '' as $$
declare jid uuid;
begin
  select id into jid from public.jobs where kind = 'onboard' and input ->> 'brand' = p_brand and status in ('queued', 'running', 'waiting_quota') limit 1;
  if found then return jid; end if;
  insert into public.jobs(kind, input) values ('onboard', jsonb_build_object('brand', p_brand, 'trigger', p_trigger)) returning id into jid;
  return jid;
end $$;
revoke all on function public.queue_onboard(text, text) from public, anon, authenticated;

create or replace function public.run_onboard(p_brand text) returns uuid language plpgsql security definer set search_path = '' as $$
begin
  if not public.is_member() then raise exception 'Your account is not on the members list.'; end if;
  if not exists (select 1 from public.brands where id = p_brand) then raise exception 'Unknown account.'; end if;
  return public.queue_onboard(p_brand, 'manual');
end $$;
revoke all on function public.run_onboard(text) from public, anon;
grant execute on function public.run_onboard(text) to authenticated;

-- add a new account; Account Intelligence starts studying it right away
create or replace function public.add_brand(p_name text, p_website text, p_instagram text default null, p_linkedin text default null, p_facebook text default null)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare bid text; n int := 1; base text; clean text;
begin
  if not public.is_member() then raise exception 'Your account is not on the members list.'; end if;
  clean := trim(coalesce(p_name, ''));
  if length(clean) < 2 or length(clean) > 60 then raise exception 'Give the account a name (2-60 characters).'; end if;
  if coalesce(p_website, '') !~ '^https?://[a-z0-9.-]+\.[a-z]{2,}(/.*)?$' then raise exception 'Give the website address, starting with https://'; end if;
  if (select count(*) from public.brands) >= 20 then raise exception 'The studio already has 20 accounts.'; end if;
  base := left(trim(both '-' from regexp_replace(lower(clean), '[^a-z0-9]+', '-', 'g')), 20);
  if length(base) < 2 then base := 'account'; end if;
  bid := base;
  while exists (select 1 from public.brands where id = bid) loop n := n + 1; bid := left(base, 18) || '-' || n; end loop;
  insert into public.brands(id, name, website, instagram, status, sort, links)
  values (bid, clean, p_website, nullif(regexp_replace(coalesce(p_instagram, ''), '^.*instagram\.com/|^@|/.*$', '', 'g'), ''), 'setup',
          (select coalesce(max(sort), 0) + 1 from public.brands),
          jsonb_strip_nulls(jsonb_build_object('instagram', nullif(trim(coalesce(p_instagram, '')), ''), 'linkedin', nullif(trim(coalesce(p_linkedin, '')), ''),
                                               'facebook', nullif(trim(coalesce(p_facebook, '')), ''))));
  return jsonb_build_object('brand', bid, 'job', public.queue_onboard(bid, 'new'));
end $$;
revoke all on function public.add_brand(text, text, text, text, text) from public, anon;
grant execute on function public.add_brand(text, text, text, text, text) to authenticated;

-- daily: one learning run per live account (the first one also runs the Resource Manager); monthly re-study of every account
create or replace function public.learn_tick() returns int language plpgsql security definer set search_path = '' as $$
declare b record; n int := 0; day text := to_char(now() at time zone 'Asia/Kolkata', 'YYYY-MM-DD'); first boolean := true;
begin
  for b in select id, status, intel_at from public.brands order by sort loop
    if b.status = 'live' and not exists (select 1 from public.jobs where kind = 'learn' and input ->> 'brand' = b.id and input ->> 'day' = day) then
      insert into public.jobs(kind, input) values ('learn', jsonb_build_object('brand', b.id, 'day', day, 'resources', first));
      n := n + 1;
    end if;
    if b.status = 'live' then first := false; end if;
    if b.intel_at is null or b.intel_at < now() - interval '30 days' then
      perform public.queue_onboard(b.id, 'refresh');
    end if;
  end loop;
  return n;
end $$;
revoke all on function public.learn_tick() from public, anon, authenticated;

-- Lane A (ideas) also runs the studies and the learning runs
create or replace function public.claim_next(p_worker text, p_kinds text[], p_lease_seconds int default 90, p_revisions_only boolean default false)
returns public.jobs language plpgsql security definer set search_path = '' as $$
declare j public.jobs;
begin
  select * into j from public.jobs
   where kind = any(p_kinds)
     and (not p_revisions_only or parent_id is not null or (kind = 'reel' and input ? 'autopilot'))
     and ((status in ('queued', 'waiting_quota') and coalesce(not_before, now()) <= now())
       or (status = 'running' and lease_expires_at < now()))
   order by case when kind in ('suggest', 'optimize') then 0 when kind = 'reel' then 1 when kind = 'onboard' then 2 else 3 end, created_at
   limit 1
   for update skip locked;
  if not found then return null; end if;
  update public.jobs
     set status = 'running', lease_owner = p_worker, lease_expires_at = now() + make_interval(secs => p_lease_seconds),
         attempts = attempts + 1, error = null
   where id = j.id returning * into j;
  return j;
end $$;

do $$ begin
  if exists (select 1 from pg_namespace where nspname = 'cron') then
    perform cron.schedule('reelstudio-learn', '35 0 * * *', 'select public.learn_tick()');     -- 06:05 India time
  end if;
end $$;
