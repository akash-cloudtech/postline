   # Social Media Automation Pipeline

   ## Goal
   Fully automated pipeline: generate a general-topic + tech-topic video script
   every morning at 7 AM, print both, I record raw video on my phone (no editing),
   the pipeline detects the new video, generates platform-specific captions/
   hashtags, asks for my approval, then posts to TikTok, YouTube, Instagram,
   Facebook, LinkedIn, Threads, and X.

   CURRENT SCOPE (narrowed Nov 2026): Instagram only. Target flow:
     Phone: record -> add to public shared Google Photos album "To Post"
     Mac:   check album -> new video found -> download -> Gemini caption
            -> post as Instagram Reel (captioned) AND Instagram Story
   Facebook is out of scope: user has a personal profile (no Page), the API
   can't post to profiles, and in-app IG->FB cross-posting skips API posts.
   Files: pipeline.py (loop + state in data/state.json), gphotos_fetch.py
   (scrapes the public album page; the Photos API can't read user albums since
   Mar 2025), ig_post.py (Reel publishing). Token is an "EAA" Unified-login
   token, so the API host is graph.facebook.com; IG user id 17841464196783143.
   Posting: use ig_post.post_reel_from_url() with the Google Photos "=dv"
   URL (Meta fetches it). Direct rupload of full-length phone videos fails
   with ProcessingFailedError (small clips work). caption_gen.py writes
   caption + 5 hashtags + city-level location (Gemini; model fallbacks and
   CAPTION_STYLE in .env). First Reel posted 2026-11-26.
   APP ("Postline", Nov 2026): `./venv/bin/python server.py` -> http://localhost:8420.
   server.py = FastAPI API + background worker (album check, captions, approval
   queue, scheduled posting); store.py = SQLite data/postline.db; settings in
   data/settings.json; log in data/postline.log. UI is the Claude Design export
   ui/Postline.dc.html + ui/support.js (dc runtime, React from unpkg). Only the
   <script data-dc-script> logic was rewritten to call /api/*; the markup is the
   design's. Nothing posts without approval. pipeline.py is the old CLI loop -
   don't run it alongside the server. Script ideas page still shows sample data.
   Morning schedule/scripts/printing (Phase 1) is on hold; Gemini captions,
   approval step, launchd scheduling, and other platforms come later.

   ## Stack decisions
   - Language: Python
   - AI provider: Google Gemini API (free tier) — chosen for cost, not Claude API
   - Scheduling: macOS launchd (not cron)
   - Printing: CUPS via `lp` command to a network printer
   - Runs on: Mac mini M4

   ## Status
   - Phase 1 (topic fetch -> script -> PDF -> print) is scaffolded: see
     main.py, topic_fetch.py, script_writer.py, render_pdf.py, print_job.py
   - Phase 2 (video detection, captions/hashtags, approval, posting) not started

   ## Next steps
   - Confirm Google Photos API access/scope actually works for auto-detecting
     new phone videos
   - Build caption/hashtag generator (structured JSON output per platform)
   - Build approval interface
   - Wire up posting for YouTube Shorts first and validate the full loop;
     add the remaining platforms (TikTok, Instagram, Facebook, LinkedIn,
     Threads, X) once that's solid