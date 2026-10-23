-- Reel Studio 0012: each account's own assets (logo). Members upload a logo on the site; reels of that account use it.
-- Files live in a private bucket under <account id>/...; an account's logo can only be set from its own folder.

alter table public.brands add column if not exists logo_asset text;
alter table public.brands add column if not exists logo_updated_at timestamptz;

do $$ begin
  if exists (select 1 from information_schema.schemata where schema_name = 'storage') then
    insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
    values ('brand-assets', 'brand-assets', false, 5242880, array['image/png', 'image/jpeg', 'image/webp'])
    on conflict (id) do nothing;
    begin
      create policy brand_assets_member_read on storage.objects for select to authenticated
        using (bucket_id = 'brand-assets' and (select public.is_member()));
    exception when duplicate_object then null; end;
    begin
      create policy brand_assets_member_upload on storage.objects for insert to authenticated
        with check (bucket_id = 'brand-assets' and (select public.is_member())
                    and (storage.foldername(name))[1] in (select b.id from public.brands b)
                    and name ~ '^[a-z0-9-]{2,24}/logo-[0-9]{10,16}\.(png|jpg|jpeg|webp)$');
    exception when duplicate_object then null; end;
  end if;
end $$;

-- point an account at a logo it uploaded (only files in its own folder that really exist)
create or replace function public.set_brand_logo(p_brand text, p_path text)
returns jsonb language plpgsql security definer set search_path = '' as $$
begin
  if not public.is_member() then raise exception 'Your account is not on the members list.'; end if;
  if not exists (select 1 from public.brands where id = p_brand) then raise exception 'Unknown account.'; end if;
  if p_path is null or p_path !~ ('^' || p_brand || '/logo-[0-9]{10,16}\.(png|jpg|jpeg|webp)$') then
    raise exception 'That file does not belong to this account.';
  end if;
  if exists (select 1 from information_schema.tables where table_schema = 'storage' and table_name = 'objects')
     and not exists (select 1 from storage.objects o where o.bucket_id = 'brand-assets' and o.name = p_path) then
    raise exception 'Upload the logo first.';
  end if;
  update public.brands set logo_asset = p_path, logo_updated_at = now() where id = p_brand;
  return jsonb_build_object('brand', p_brand, 'logo_asset', p_path);
end $$;
revoke all on function public.set_brand_logo(text, text) from public, anon;
grant execute on function public.set_brand_logo(text, text) to authenticated;
