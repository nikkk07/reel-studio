-- Reel Studio 0010: several accounts (brands) in one studio. Same pipeline; every reel belongs to one account.
-- Existing reels and settings stay with We One Aviation. Other accounts start in 'setup' until their content plan,
-- voice, watermark and Instagram are decided; reels can only be created for 'live' accounts.

create table if not exists public.brands (
  id text primary key check (id ~ '^[a-z0-9-]{2,24}$'),
  name text not null,
  tagline text,
  website text,
  logo text,                       -- path on the site, e.g. brands/weone.png
  color text,                      -- accent for the dashboard card
  instagram text,                  -- handle, without @
  status text not null default 'setup' check (status in ('live', 'setup', 'paused')),
  sort int not null default 100,
  created_at timestamptz not null default now()
);
alter table public.brands enable row level security;
create policy brands_read on public.brands for select to authenticated using ((select public.is_member()));
revoke all on public.brands from anon, authenticated;
grant select on public.brands to authenticated;

insert into public.brands(id, name, tagline, website, logo, color, instagram, status, sort) values
  ('weone', 'We One Aviation', 'DGCA ground classes, Dwarka · Delhi', 'https://weoneaviation.in', 'brands/weone.png', '#0A2342', 'we_one_aviation', 'live', 1),
  ('bookmycharter', 'Book My Charter', 'Private jet & helicopter charter in India', 'https://bookmycharter.in', 'brands/bookmycharter.png', '#13213D', null, 'setup', 2),
  ('flystar', 'Flying Star Aviator', 'CPL & ATPL ground training, Dwarka · Delhi', 'https://www.flystar.co.in', 'brands/flystar.png', '#1E8E3E', 'flyingstaraviator', 'setup', 3),
  ('legalgroup', 'Legal Group', 'Legal services & lawyers in India', 'https://www.legalgroup.in', 'brands/legalgroup.png', '#B8902F', null, 'setup', 4)
on conflict (id) do nothing;

alter table public.jobs add column if not exists brand text not null default 'weone' references public.brands(id);
create index if not exists jobs_brand_idx on public.jobs (brand, kind, status, created_at);

-- every new job gets its account: from the request, else from the reel it revises, else We One Aviation
create or replace function public.set_job_brand() returns trigger language plpgsql set search_path = '' as $$
begin
  new.brand := coalesce(nullif(new.input ->> 'brand', ''),
                        (select p.brand from public.jobs p where p.id = new.parent_id),
                        nullif(new.brand, ''), 'weone');
  if not exists (select 1 from public.brands b where b.id = new.brand) then raise exception 'Unknown account.'; end if;
  return new;
end $$;
create or replace trigger jobs_set_brand before insert on public.jobs for each row execute function public.set_job_brand();
revoke all on function public.set_job_brand() from public, anon, authenticated;

-- creating anything for an account that is still being set up is refused (its plan, voice and watermark don't exist yet)
create or replace function public.create_job(p_kind text, p_input jsonb, p_parent uuid default null)
returns public.jobs language plpgsql security definer set search_path = '' as $$
declare j public.jobs; n int; t text; b text; st text;
begin
  if not public.is_member() then raise exception 'Your account is not on the members list.'; end if;
  if p_kind not in ('suggest', 'optimize', 'reel') then raise exception 'bad kind'; end if;
  if octet_length(coalesce(p_input, '{}'::jsonb)::text) > 20000 then raise exception 'That request is too long. Keep it under a page of text.'; end if;
  if coalesce(p_input, '{}'::jsonb) ? 'autopilot' then raise exception 'Use Run now to start Autopilot.'; end if;
  b := coalesce(nullif(p_input ->> 'brand', ''), 'weone');
  select status into st from public.brands where id = b;
  if st is null then raise exception 'Unknown account.'; end if;
  if st <> 'live' then raise exception 'This account is still being set up. Reels open as soon as its plan is ready.'; end if;
  if p_kind = 'reel' then
    t := lower(trim(coalesce(p_input -> 'concept' ->> 'title', '')));
    if t <> '' and p_parent is null then
      select * into j from public.jobs
       where kind = 'reel' and brand = b and status in ('queued', 'running', 'waiting_quota')
         and lower(trim(coalesce(input -> 'concept' ->> 'title', ''))) = t
       order by created_at limit 1;
      if found then return j; end if;
    end if;
    select count(*) into n from public.jobs where kind = 'reel' and status in ('queued', 'running', 'waiting_quota');
    if n >= 3 then raise exception 'Three reels are already in the queue. Wait for one to finish, then try again.'; end if;
  else
    select * into j from public.jobs where kind = p_kind and status in ('queued', 'running', 'waiting_quota')
       and created_at > now() - interval '20 minutes' and input = coalesce(p_input, '{}'::jsonb) order by created_at limit 1;
    if found then return j; end if;
    select count(*) into n from public.jobs where kind in ('suggest', 'optimize') and not (input ? 'autopilot')
       and status in ('queued', 'running', 'waiting_quota');
    if n >= 3 then raise exception 'Three idea requests are already running. Wait for one to finish.'; end if;
  end if;
  insert into public.jobs(kind, input, parent_id, created_by) values (p_kind, coalesce(p_input, '{}'::jsonb), p_parent, auth.uid())
  returning * into j;
  return j;
end $$;
