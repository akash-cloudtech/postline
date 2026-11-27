# Postline (social media automation)

## Goal
Long term: I record raw vertical videos on my phone (no editing); the app
detects them, writes platform-specific captions/hashtags, asks for my
approval, and posts to Instagram, TikTok, YouTube, Facebook, LinkedIn,
Threads and X.

**Current scope: Instagram only (Reel + Story).**
```
Phone: record -> add to public shared Google Photos album "SocialMedia"
Mac:   server finds new video -> download -> Gemini caption/hashtags/city
       -> Review queue (approve / schedule / reject) -> Reel + Story
```

## Run
`./venv/bin/python server.py` -> http://localhost:8420 (see README.md).
Only one server at a time (two would both post). Don't restart it while a
post is in progress (check data/postline.log); interrupted posts are marked
Failed/"Interrupted" on startup and need a manual Retry, never auto-repost.

## Code map
- server.py: FastAPI API + background worker (album checks, caption jobs,
  scheduled posts, posting). Settings in data/settings.json, log in
  data/postline.log.
- store.py: SQLite data/postline.db (items + events).
- caption_gen.py: Gemini caption/title/hashtags with model fallbacks; GPS ->
  city (Nominatim, rounded coords); recording date (.mov ISO date or .mp4 mvhd).
- gphotos_fetch.py: scrapes the public album page (the Photos API can't read
  user albums since Mar 2025). Short links must be resolved with a non-browser
  UA. `=dv` on a base URL downloads the video.
- ig_post.py: publishing. Token is an "EAA" Unified-login token -> host
  graph.facebook.com; IG user id 17841464196783143.
- ui/Postline.dc.html + ui/support.js: Claude Design export (dc runtime, React
  from unpkg). Only the `<script data-dc-script>` logic was rewritten to call
  /api/*; keep the markup as designed.

## Hard-won facts (don't re-learn these)
- Reels: post by URL (post_reel_from_url with the Google Photos `=dv` link).
  Direct rupload of full-length phone videos fails with ProcessingFailedError.
- Stories: >60 s publishes fail (error_subcode 2207032) -> trim to 59 s with
  `avconvert` and upload. Stories can't carry captions.
- Facebook is out of scope: personal profile only (no Page), apps can't post
  to profiles, and in-app IG->FB cross-posting ignores API posts.
- Gemini: 2.5 models are closed to new users; free-tier models are often
  503/overloaded -> fallback list in .env. Lite models sometimes emit stray
  CJK characters (prompt forbids it).

## Stack decisions
- Python; Gemini API (free tier) for captions, chosen for cost
- macOS launchd for running the server (not set up yet)
- Runs on a Mac mini M4

## Status / next steps
- Done: full album -> caption -> review -> Reel + Story flow, scheduling,
  history/retry, interrupted-post recovery. Repo: github.com/akash-devsecops/postline
  (also pushed to akash-cloudtech/postline).
- Next: launchd job so the server starts at login; token-expiry handling;
  Script ideas page is still sample data (morning script/print feature on
  hold; old files removed Nov 2026, recoverable from git history).
- Later: more platforms (Facebook Page, TikTok, YouTube Shorts, ...).
