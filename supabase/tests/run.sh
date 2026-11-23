#!/usr/bin/env bash
# Reel Studio migrations on a throwaway local Postgres (Supabase parts stubbed), then the 0026 checks.
#   PGHOST=/tmp PGPORT=54329 PGUSER=postgres bash supabase/tests/run.sh
set -euo pipefail
cd "$(dirname "$0")"
DB=rs_test_$$
psql -q -c "CREATE DATABASE $DB" >/dev/null
trap 'psql -q -c "DROP DATABASE IF EXISTS $DB" >/dev/null' EXIT
psql -q -X -v ON_ERROR_STOP=1 -d $DB -f 00_stub.sql >/dev/null
for f in ../migrations/0*.sql ../migrations/0026_crm_content.sql ../migrations/0027_dm_universal.sql ../migrations/0028_egress_guard.sql ../migrations/0029_mac_wake.sql; do     # 0026-0029 twice: must be re-runnable
  sed -E '/create extension( if not exists)? (pg_cron|pg_net|supabase_vault)/Id' "$f" | psql -q -X -v ON_ERROR_STOP=1 -d $DB >/dev/null 2>/tmp/rs_err.txt \
    || { echo "FAILED applying $f"; head -3 /tmp/rs_err.txt; exit 1; }
done
out=$(psql -q -X -d $DB -f 0026_crm_content_test.sql 2>&1 | grep -E "PASS|FAIL|ERROR" | sed 's/.*NOTICE:  //')
echo "$out"; ! grep -qE "FAIL|ERROR" <<<"$out" && echo "ALL 0026 CHECKS PASSED"
out=$(psql -q -X -d $DB -f 0028_egress_guard_test.sql 2>&1 | grep -E "PASS|FAIL|ERROR" | sed "s/.*NOTICE:  //")
echo "$out"; ! grep -qE "FAIL|ERROR" <<<"$out" && echo "ALL 0028 CHECKS PASSED"
out=$(psql -q -X -d $DB -f 0029_mac_wake_test.sql 2>&1 | grep -E "PASS|FAIL|ERROR" | sed "s/.*NOTICE:  //")
echo "$out"; ! grep -qE "FAIL|ERROR" <<<"$out" && echo "ALL 0029 CHECKS PASSED"
out=$(psql -q -X -d $DB -f 0027_dm_universal_test.sql 2>&1 | grep -E "PASS|FAIL|ERROR" | sed 's/.*NOTICE:  //')
echo "$out"; ! grep -qE "FAIL|ERROR" <<<"$out" && echo "ALL 0027 CHECKS PASSED"
