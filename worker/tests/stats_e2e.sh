#!/bin/bash
# Instagram results collection (cloud poster) against fake_instagram (:8790), poster on :8000, PostgREST via mock (:8765).
P="psql -h /tmp -p 5433 -U postgres -d rs_it -tAq"
TOK="IGAAstats$(printf 'x%.0s' {1..60})"; PASS=0; FAIL=0
ok() { if [ "$1" = "1" ]; then echo "PASS $2"; PASS=$((PASS+1)); else echo "FAIL $2"; FAIL=$((FAIL+1)); fi; }
q() { $P -c "$1"; }
scen() { echo "{\"valid_tokens\": [\"$TOK\"], \"insights\": \"$1\"}" > /tmp/rs_stack/igs/scenario.json; }
tick() { curl -s -X POST http://127.0.0.1:8000/ -H "x-cron-key: testcron" > /tmp/rs_stack/tick.json; }
scen full
q "insert into secrets(key,value,updated_at) values ('instagram_token','$TOK',now()) on conflict (key) do update set value=excluded.value, updated_at=now()"
q "insert into secrets(key,value) values ('cron_key','testcron') on conflict (key) do update set value='testcron'"
q "update settings set value=jsonb_build_object('state','connected','user_id','17841400000000001','username','weoneaviation','checked_at',to_char(now() at time zone 'utc','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')) where key='instagram_status'"
q "delete from settings where key='instagram_insights'"
q "update jobs set status='cancelled' where kind='reel' and status in ('queued','running','review')"
q "update jobs set output = output - 'ig' where kind='reel' and status='done' and id::text not like 'cccccccc%'"
q "delete from jobs where id::text like 'cccccccc%'"
for row in "1|5 hours|" "2|10 minutes|" "3|3 days|,'ig_stats',jsonb_build_object('at',now()-interval '1 hour','views',5)"; do IFS='|' read n age extra <<< "$row"
  q "insert into jobs(id,kind,status,input,output) values ('cccccccc-0000-0000-0000-00000000000$n','reel','running','{}', jsonb_build_object('title','R$n','ig',jsonb_build_object('state','posted','media_id','M$n','posted_at',now()-interval '$age')$extra))"
  q "update jobs set status='done' where id='cccccccc-0000-0000-0000-00000000000$n'"; done
tick
ok "$(q "select (output->'ig_stats'->>'views' = '1500' and output->'ig_stats'->>'shares' = '14' and output->'ig_stats'->>'avg_watch_s' = '11.2' and output->'ig_stats'->>'reels_skip_rate' = '41.5' and output->'ig_stats'->>'source'='insights')::int from jobs where id='cccccccc-0000-0000-0000-000000000001'")" "posted reel gets views, reach, sends, saves, watch time, skip rate"
ok "$(q "select (output->'ig_stats' is null)::int from jobs where id='cccccccc-0000-0000-0000-000000000002'")" "a reel live for under 30 minutes is not measured yet"
ok "$(q "select (output->'ig_stats'->>'views' = '5')::int from jobs where id='cccccccc-0000-0000-0000-000000000003'")" "an older reel measured an hour ago is not re-measured (every 12 h after 2 days)"
ok "$(q "select (output->'ig'->>'state'='posted' and output->'ig'->>'media_id'='M1')::int from jobs where id='cccccccc-0000-0000-0000-000000000001'")" "posting state untouched"
# Instagram rejects a newer metric -> falls back to the core set and remembers it
scen core_only; q "update jobs set output = output - 'ig_stats' where id='cccccccc-0000-0000-0000-000000000001'"; tick
ok "$(q "select (output->'ig_stats'->>'views' = '1500' and output->'ig_stats'->>'reels_skip_rate' is null)::int from jobs where id='cccccccc-0000-0000-0000-000000000001'")" "an unsupported metric -> core metrics still collected"
ok "$(q "select (value->>'metrics'='core')::int from settings where key='instagram_insights'")" "remembers to ask only for core metrics"
# the token lacks the insights permission -> likes/comments only, the Instagram connection stays connected
scen no_perm; q "update jobs set output = output - 'ig_stats' where id='cccccccc-0000-0000-0000-000000000001'"; q "delete from settings where key='instagram_insights'"; tick
ok "$(q "select (output->'ig_stats'->>'likes' = '61' and output->'ig_stats'->>'source'='basic')::int from jobs where id='cccccccc-0000-0000-0000-000000000001'")" "no insights permission -> public likes and comments"
ok "$(q "select (value->>'ok'='false')::int from settings where key='instagram_insights'")" "the site is told why (permission note)"
ok "$(q "select (value->>'state'='connected')::int from settings where key='instagram_status'")" "Instagram posting connection is NOT marked expired"
grep -q '"stats"' /tmp/rs_stack/tick.json; ok "$([ $? = 0 ] && echo 1)" "cron response reports stats"
q "delete from jobs where id::text like 'cccccccc%'"
echo "$PASS/$((PASS+FAIL)) stats checks passed"; [ $FAIL = 0 ]
