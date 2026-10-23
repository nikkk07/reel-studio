# We One Aviation – Reel Studio

A small website where you pick (or type) a topic and the Mac mini produces the finished Hinglish aviation reel – same pipeline and quality as the hand-made reels: researched script with sources, cloned female voice (best of 3 takes, clarity + naturalness scored), stock footage chosen by Claude from contact sheets, animated graphics, mixed and mastered to −14 LUFS, Claude frame-check, then your approval.

```
Browser (static site, Vercel)  ──►  Supabase (login, job queue, video storage)  ◄──  Mac mini worker (outbound only)
                                                                                                 └─ Claude Code (your Pro login) = the brain
```
The website never connects to the Mac. The Mac polls the queue, so it needs no open ports, and jobs simply wait when it is off. Details: `docs/ARCHITECTURE.md`, plan and risks: `docs/ROADMAP.md`.

## One-time setup (about 30 minutes)

### 1. Supabase (free plan)
1. supabase.com → New project (region: Mumbai). Save the database password somewhere safe.
2. SQL Editor → paste all of `supabase/migrations/0001_init.sql` → Run. (It also adds weoneaviation8@gmail.com as owner.)
3. Authentication → Users → **Add user** → your email + a strong password, tick *Auto Confirm User*.
4. Authentication → Sign In / Providers → turn **off** "Allow new users to sign up".
5. Project Settings → API: keep this tab open; you need the **Project URL**, the **anon public** key (website) and the **service_role** key (Mac only – never put it in the website).

### 2. Mac mini
In Terminal:
```bash
cd ~/Desktop/Documentary/EduReelStudio/reelstudio/worker/scripts
bash setup_mac.sh
```
It installs Python 3.11 + ffmpeg (Homebrew), copies code and library to `~/ReelStudio` (outside Desktop, so the background service needs no privacy permission), asks for the Supabase URL and service_role key (hidden typing, saved only in `~/ReelStudio/.env`), opens Claude Code once so you sign in with your **Claude Pro** account, runs a self-test (test render + one voice take) and starts the service.

Then in System Settings: prevent automatic sleep, start up after power failure, wake for network access, and automatic login.

### 3. Website (Vercel, deployed)
Live at **https://weone-reelstudio.vercel.app** (project `weone-reelstudio`, team weone404's projects). `web/config.js` holds the Supabase URL and the publishable key (safe in a browser; Row Level Security protects the data).
To publish a change: `cd reelstudio/web && npx vercel deploy --prod --scope weone404s-projects`.
On a phone, open the site and use Add to Home Screen to install it like an app; allow notifications to get the "reel ready" alert.

Adding a teammate: Supabase → Authentication → Add user, then in SQL Editor:
`insert into public.members(email, role) values ('their@email.com', 'member');`

## Using it
1. **Connect to Server** – green Mac light = the Mac answered within seconds; green Claude light = Claude Code is signed in and not at its usage limit. Otherwise you get the reason and a Retry button.
2. **Suggestions** → Generate → pick one of 5 → Confirm & Create Reel. Or **My topic** → type it → Optimize → Confirm, or Re-generate with feedback.
3. **Production** shows each stage live. A reel takes about 35–60 minutes on the Mac (voice takes are the slow part).
4. **Review**: watch, check the fact sources and the "listen closely" words, then **Approve** (goes to Library) or **Request changes** with notes (a new version is made with your notes).
5. **Library**: download approved reels. Cloud copies are deleted after 30 days; full-quality masters stay on the Mac in `~/ReelStudio/deliverables` (also linked as `EduReelStudio/ReelStudio_deliverables`).

## Limits and behaviour
- Supabase Free (1 GB storage, 50 MB per file – reels are kept ≤ 45 MB), Vercel (your plan), Claude Code with your existing Pro plan (no API key, no API billing).
- Claude Pro limits are shared with your normal Claude chats and reset every 5 hours (plus weekly caps). If a limit is hit, the job shows *Paused: Claude usage limit* and resumes by itself afterwards.
- At most 3 reels per day and 3 waiting in the queue (change `REELSTUDIO_DAILY_REEL_CAP` in `~/ReelStudio/.env`).
- Mac off, asleep or offline: the site shows "Server offline", new jobs wait in the queue, a job that was running resumes from its last finished stage when the Mac is back.
- Supabase pauses free projects after a week without activity; the Mac's heartbeat keeps it active while the Mac runs.
- New English words in a script are listed on the review card; add approved spellings to `EduReelStudio/library/voice/pronunciation_lexicon.json` and run `bash setup_mac.sh --update`.

## Maintenance
```bash
tail -f ~/ReelStudio/logs/worker.log                                  # live log
cd ~/ReelStudio/app && ../.venv/bin/python -m reelworker.selftest     # health check
bash ~/Desktop/Documentary/EduReelStudio/reelstudio/worker/scripts/setup_mac.sh --update   # after code/lexicon changes
launchctl kickstart -k gui/$(id -u)/com.weoneaviation.reelstudio      # restart
```

## Tests (developer)
- `worker/tests/e2e_local.py` – full reel (real voice, render, mix, master) with mock Claude and a fake database.
- `supabase/tests/supabase_stub.sql` + PostgREST – migration, row-level security and queue functions.
- `web/tests/mock_supabase.py` + `web/tests/ui_smoke.py` – website flows in Chromium (mock mode, or proxying a real PostgREST with the real worker).
