#!/bin/bash
# Fresh local copy of the database for the SQL tests: stub + every migration (pg_cron / pg_net come from the stub).
set -e; cd "$(dirname "$0")/.."; W=/tmp/sbt; rm -rf $W; mkdir -p $W
cp tests/supabase_stub.sql $W/00_stub.sql
for f in migrations/0*.sql; do sed -E '/create extension( if not exists)? (pg_cron|pg_net)/Id' "$f" > "$W/$(basename "$f")"; done
cp tests/*.sql $W/ 2>/dev/null || true; chmod -R a+rX $W
su postgres -c "psql -q -c 'drop database if exists rs' -c 'create database rs'" >/dev/null 2>&1
for f in $W/00_stub.sql $(ls $W/0[0-9][0-9][0-9]_*.sql); do su postgres -c "psql -q -v ON_ERROR_STOP=1 -d rs -f $f" >/dev/null 2>$W/err || { echo "FAILED $(basename $f)"; cat $W/err | head -5; exit 1; }; done
echo "local database ready"
