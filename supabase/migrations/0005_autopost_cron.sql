-- Reel Studio 0005: cloud timers. Every minute: approve reels whose review window ran out, and run the ig-poster Edge Function.
create extension if not exists pg_cron;
create extension if not exists pg_net;
select cron.schedule('reelstudio-auto-approve', '* * * * *', $$select public.auto_approve_due()$$);
select cron.schedule('reelstudio-ig-poster', '* * * * *', $$
  select net.http_post(
    url := 'https://rjgwxfsjjplijsnsfztd.supabase.co/functions/v1/ig-poster',
    headers := jsonb_build_object('Content-Type', 'application/json', 'x-cron-key', (select value from public.secrets where key = 'cron_key')),
    body := '{}'::jsonb,
    timeout_milliseconds := 60000)
$$);
