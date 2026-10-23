# We One Aviation Reel Studio: architecture (v1)

Goal: anyone on the team opens a web page, picks or writes a topic, confirms, and receives a finished,
fact-checked Hinglish Reel (1080x1920, 60 fps, -14 LUFS) produced on the office Mac Mini M4, with Claude as
the decision-maker. Zero paid services except what the team already has (Claude Pro plan).

## 1. Components

```
 Browser (team)                      Supabase (free tier)                     Mac Mini M4 (24x7)
 ┌──────────────────────┐   HTTPS    ┌──────────────────────────────┐  HTTPS   ┌─────────────────────────────────────┐
 │ Static web app (SPA) │──────────▶ │ Auth (email+password, invite) │ ◀─────── │ reelworker (Python, launchd)        │
 │ Cloudflare Pages     │ ◀───────── │ Postgres: jobs, events,       │  outbound│  ├ heartbeat 15 s + ping answer 3 s  │
 │ (or Vercel Pro)      │  polling   │   workers, pings, members     │   only   │  ├ lane A: suggest / optimize jobs   │
 └──────────────────────┘            │ (Realtime enabled, optional)  │          │  ├ lane B: reel jobs (1 at a time)   │
                                     │ Storage: private bucket reels │          │  ├ brain: `claude -p` (Pro login)    │
                                     │ RPC: create_job, claim_job…   │          │  └ media engine: Chatterbox (MPS),   │
                                     └──────────────────────────────┘          │     faster-whisper, UTMOS, skia,     │
                                                                               │     ffmpeg (VideoToolbox), Pexels/   │
                                                                               │     Pixabay APIs                     │
                                                                               └─────────────────────────────────────┘
```

* The browser never talks to the Mac. Both talk to Supabase, so the Mac needs **no open ports, no tunnel, no
  static IP** and keeps working behind any router.
* The web app is a static build: no server code, no secrets in the cloud. It holds only the public anon key;
  Row Level Security decides what a logged-in member may read or write.
* The Mac holds the secrets (Supabase service key, Pexels/Pixabay keys) in `~/ReelStudio/.env` (chmod 600).
* Runtime home is `~/ReelStudio` (code, venv, library copy, jobs, logs, masters) – outside Desktop/Documents, so the
  launchd service needs no macOS privacy permission. `setup_mac.sh` fills it from `EduReelStudio/library`.
* The web app vendors supabase-js 2.45.4 (no CDN at runtime) and polls every 2–3 s (simple, robust on free tier).

## 2. User flows

1. **Login** – email + password; sign-ups are disabled, members are invited from the Supabase dashboard.
2. **Connect to Server** – the page inserts a `pings` row; the worker answers within ~3 s (no answer in 15 s →
   red, with `workers.last_seen`). Two lights: **Mac** (ping answered) and **Claude**: if the last Claude check is
   older than 10 min the ping triggers one fresh tiny `claude -p` check in the background and the page waits for
   it (otherwise the heartbeat re-checks every 30 min, so usage stays negligible). Green → continue; otherwise **Retry** with the reason
   (Mac offline since …, Claude login expired, Claude usage limit until …).
3. **Topic**
   * *Generate suggestions* → job `suggest` → 5 cards (hook, angle, why it can go viral, fact sources, risk notes)
     → pick one → **Confirm**.
   * *My topic* → textarea → job `optimize` → improved concept (hook, outline, facts to verify) → **Confirm** or
     **Regenerate** (sends feedback, new `optimize` job).
4. **Production** – job `reel` with live stage list, progress bar, log lines and ETA.
5. **Review** – preview player, facts + sources, flagged words, quality scores → **Approve** (download link) or
   **Request changes** (notes → new `reel` job that reuses finished stages where possible).
6. **Library** – all past jobs, status, downloads.

## 3. Job model (Postgres)

`jobs(id, kind[suggest|optimize|reel], status[queued|running|review|done|failed|cancelled|waiting_quota],
stage, progress, input jsonb, output jsonb, error, attempts, lease_owner, lease_expires_at, parent_id,
created_by, timestamps)` + `job_events` (append-only log) + `workers` (heartbeat) + `pings` + `members`.

* **Lease queue**: `claim_job(worker, lanes[], lease_s)` picks the oldest queued job of the lane (or a running job
  whose lease expired) with `FOR UPDATE SKIP LOCKED`, sets a 90 s lease; the worker renews it every 30 s.
  If the Mac crashes or loses network, the lease expires and the job is picked up again on reconnect.
* **Checkpoints**: every reel stage writes its outputs into `jobs/<id>/` on the Mac plus `state.json`; on restart
  the runner skips completed stages, so a power cut costs at most one stage.
* **Retries**: transient failures (network, API 5xx) retry with back-off (3 attempts per stage); Claude usage-limit
  errors move the job to `waiting_quota` and resume automatically after the reset time.

## 4. Reel pipeline (lane B, on the Mac)

| # | Stage | Who | Output |
|---|---|---|---|
| 1 | research_script | Claude (`WebSearch`, `WebFetch` only) | claim ledger with sources + **Reel spec** (voice lines per group/phrase, captions, shot queries, graphics plan, CTA) |
| 2 | lexicon | code | voice text with pronunciation dictionary applied; unknown English words flagged |
| 3 | footage | code + Claude (`Read` contact sheets) | Pexels/Pixabay search → contact sheets → Claude picks clips + in-points → 1296x2304 60 fps shots |
| 4 | voice | code | Chatterbox (female/male reference), 3 takes per group on MPS, scored by Whisper key-word hits + UTMOS naturalness; best take kept |
| 5 | align | code | Whisper word timestamps → phrase spans → timeline |
| 6 | compose | code | spec-driven compositor (component library: chip, big_text, counter, list_card, quote_card, compare, timer, bolt/flash, dim, plane_path, captions, logo, end card) |
| 7 | mix | code | VO + room tone + synthesized SFX/music, ducking, -14 LUFS / ≤ -1 dBTP |
| 8 | render | code | archive master (CRF 15, kept on Mac) + delivery file (≤ 45 MB, H.264 VideoToolbox) |
| 9 | qc | code + Claude (`Read` frame sheet + report) | loudness, duration, black frames, caption collisions, key-word clarity; Claude visual review → pass or a list of fixes (one automatic fix round) |
| 10 | publish | code | upload delivery file + thumbnail + report to Storage, status `review` |

Claude is called **3 times per Reel** (script, footage pick, QC) plus once per suggestion/optimization, each with
a JSON schema (`--output-format json --json-schema`) so answers are machine-checked, not parsed from prose.

## 5. Claude on the Mac

`claude -p <prompt> --output-format json --json-schema <schema> --permission-mode dontAsk
--allowedTools "<only what the step needs>"` run in an empty per-job directory.

* Uses the team's **Pro login** (no API key). Pro limits are **shared with claude.ai chat and reset every 5 hours,
  with weekly caps** (support.claude.com article 11145838). The worker enforces a daily Reel cap (default 3) and
  shows remaining capacity on the Connect screen.
* `--bare` is **not** used: bare mode ignores the subscription login and needs an API key.
* Least privilege: research steps get only `WebSearch,WebFetch`; review steps only `Read`. Claude never gets
  `Bash`/`Edit` in the pipeline, so a hostile web page cannot make it run commands (prompt-injection safety).

## 6. Platform limits that shape the design (verified 2026-10-16)

* Supabase Free: 500 MB DB, **1 GB storage, 50 MB max upload**, 2 active projects, **paused after 1 week of
  inactivity** (supabase.com/pricing). → delivery file encoded ≤ 45 MB; storage auto-pruned after 30 days
  (archive masters stay on the Mac); the worker's heartbeat keeps the project in use.
* Vercel Hobby is **non-commercial only** (vercel.com/docs/limits/fair-use-guidelines) → host the static app on
  **Cloudflare Pages** (free: 500 builds/month) or Vercel Pro.
* Claude Code `-p`: JSON output, `--json-schema`, `--allowedTools`, `--permission-mode`
  (code.claude.com/docs/en/headless).

## 7. Reliability and monitoring

* `launchd` keeps the worker alive (KeepAlive, RunAtLoad) under `caffeinate -is` (no idle/system sleep while it runs).
* Transient failures retry with back-off (`not_before` = 3 min × attempt, max 3 attempts); a crashed or
  disconnected worker's lease expires after 90 s and the job is reclaimed, resuming at its last finished stage.
* Daily housekeeping: events > 30 days, cloud videos > 30 days, local job folders > 14 days.
* Worker writes `version`, free disk, current job and Claude status into `workers.info`.
* Optional alert: Telegram bot message if a job fails or the heartbeat stops (sent by the worker / checked by
  the web page).
* Logs: `~/ReelStudio/logs/worker.log` (rotated) + `job_events` in the DB (visible in the UI).

## 8. Security

* Sign-ups off; `members` allow-list; RLS on every table; storage bucket private (signed URLs, 1 h).
* Service key only on the Mac; the web bundle contains only the anon key.
* Claude runs with least-privilege tools in a throwaway directory.
* Uploaded media is never executed; downloaded stock files are processed only by ffmpeg/opencv.
