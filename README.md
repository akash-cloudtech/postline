# Postline

A local app that turns phone videos into Instagram posts. You record a video, add it to a Google Photos album, and Postline does the rest. Nothing is published until you approve it.

```
Phone:  record → add to a shared Google Photos album
Mac:    Postline finds the new video → downloads it
        → Gemini watches it and writes caption + hashtags + 📍 city
        → ✋ Review queue: edit, approve, schedule or reject
        → posts an Instagram Reel (with caption) + Story
```

It runs on your Mac (built for an always-on Mac mini) at **http://localhost:8420**.

## Features

- **Album watching.** Checks a public Google Photos shared album every few minutes for new videos.
- **AI captions.** Gemini watches the video (frames and audio) and writes the caption, hashtags and a short title in your saved style. The 📍 city comes from the video's own GPS. If the main model is busy, Postline tries the fallback models.
- **Review queue.** Play the video, edit the caption and hashtags, preview it Instagram-style, regenerate with an instruction ("make it shorter"), then approve, schedule or reject. Keyboard: `A` approve, `R` reject, `←/→` navigate.
- **Reel + Story.** The Reel is posted with the caption. The Story is posted from the same video; Stories over 60 s are automatically trimmed to 59 s.
- **Calendar.** Month and week views. Drag videos to schedule or reschedule them.
- **History.** Every video with its status, Instagram links, a timeline, plain-English errors, and retry for the parts that failed.
- **Safe by design.**
  - No post goes out without approval.
  - A post interrupted by a restart is never re-posted automatically.
  - The location is hidden for videos recorded near home (optional home zone).
- **Notifications.** A macOS notification for each event, plus a live activity log in the app.

## Requirements

- macOS (uses `avconvert` and `osascript`) and Python 3.11+
- An **Instagram Professional account** (Creator or Business). Personal accounts can't post through the API.
- A **Meta developer app** with the Instagram API and the `instagram_business_content_publish` permission
- A free **Gemini API key** from [Google AI Studio](https://aistudio.google.com/apikey)
- A **Google Photos album** shared by link

## Setup

```bash
git clone https://github.com/akash-devsecops/postline.git
cd postline
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env        # then fill it in (see below)
```

### 1. Instagram token

1. In the Instagram app, switch your account to **Creator**: Settings → Account type and tools.
2. At [developers.facebook.com](https://developers.facebook.com), create an app (Business type) with the **Manage messaging & content on Instagram** use case.
3. **Use cases → Customize → Permissions and features:** add `instagram_business_basic` and `instagram_business_content_publish`.
4. **API setup with Unified login:**
   1. Connect a **business portfolio**. It's free and is required by Meta.
   2. Click **Add account** to connect your Instagram account.
   3. Click **Generate token**.
5. In `.env`, set:
   - `IG_ACCESS_TOKEN` to the token
   - `IG_USER_ID` to the numeric ID shown next to the account
   - `IG_HANDLE` to your username

Optionally, set `IG_TOKEN_EXPIRES_AT=YYYY-MM-DD` so the app warns you before the token expires.

Check the connection:

```bash
./venv/bin/python ig_post.py --check
```

### 2. Gemini

Put your key in `GEMINI_API_KEY`. Model availability changes. If a model returns "not available to new users", pick another from the list your key can see and set it as `GEMINI_MODEL` or in `GEMINI_FALLBACK_MODELS`.

### 3. Google Photos album

Create an album, tap **Share → Create link**, and put the link in `GPHOTOS_ALBUM_URL`. Check that it can be read:

```bash
./venv/bin/python gphotos_fetch.py "$GPHOTOS_ALBUM_URL"
```

> ⚠️ Anyone with the link can see the album, including the original files with GPS data. Keep the link private and only keep videos you're about to post in the album.

## Run

```bash
./venv/bin/python server.py
```

Open **http://localhost:8420**.

**On the first run, nothing is posted.** Every video already in the album is recorded as "seen" and skipped. Only videos added afterwards appear in the Review queue.

## Configuration

Secrets and paths live in `.env` (see `.env.example`). Everyday settings are changed on the app's **Settings** page and saved to `data/settings.json`:

| Setting | Where | Default |
|---|---|---|
| Album check interval | Settings → Source | 5 min |
| Caption style (your voice) | Settings → AI captions / `CAPTION_STYLE` | – |
| Number of hashtags | Settings → AI captions / `NUM_HASHTAGS` | 5 |
| Auto-add city, home-zone radius | Settings → Location & privacy / `HOME_LAT`, `HOME_LON`, `HOME_RADIUS_KM` | on, 5 km |
| Default destinations (Reel, Story) | Settings → Posting | both |
| Notifications per event | Settings → Notifications | on |
| Gemini models | `GEMINI_MODEL`, `GEMINI_FALLBACK_MODELS` | – |
| Port | `POSTLINE_PORT` | 8420 |

## Project layout

```
server.py         FastAPI app + background worker (album checks, captions, scheduling, posting)
store.py          SQLite storage: videos, statuses, activity (data/postline.db)
caption_gen.py    Gemini captions, title, hashtags; GPS → city; recording date
gphotos_fetch.py  Reads a public Google Photos shared album; downloads videos
ig_post.py        Instagram publishing: Reel (URL or upload), Story (trims > 60 s)
ui/               The app's interface (Claude Design export + its runtime)
docs/             Design brief used to create the UI
data/             Runtime data: videos, database, settings, log (git-ignored)
```

The command-line tools still work on their own:

| Command | Does |
|---|---|
| `python ig_post.py --check` | Verify the token and account |
| `python ig_post.py video.mp4 "caption"` | Post a Reel |
| `python caption_gen.py video.mov` | Write a caption for a video |
| `python gphotos_fetch.py "<album link>" [--download DIR]` | List or download the album's videos |

## How posting works (and why)

- **Reels are posted by URL.** Instagram fetches the video straight from Google Photos (the `=dv` link), at original quality and including HDR. Uploading full-length phone videos directly failed with `ProcessingFailedError`, so direct upload is only used for videos added from the Mac.
- **Stories have a 60 s limit.** Longer videos are trimmed to 59 s at 720p with macOS's built-in `avconvert` and then uploaded.
- **Captions** are composed as: caption + `📍 City` + hashtags. The API doesn't support captions on Stories.
- **Facebook.** Instagram's "also share to Facebook" doesn't apply to API posts, and Meta doesn't allow apps to post to personal profiles. Facebook **Pages** are supported by the API and are a future option.

## Limitations

- **Run only one instance.** Two servers on the same `data/` folder would both post.
- **The Mac has to be running** for checks and scheduled posts. Keep the Mac mini awake.
- **The Script ideas page shows sample content.** The morning script feature isn't connected yet.
- **The token isn't renewed automatically.** Generate a new one in the Meta dashboard when it expires.
- **Videos uploaded from the Mac** use direct upload, which can fail for long or large files.

## Security

- `.env` (tokens and keys) and `data/` (your videos and database) are git-ignored. Never put real values in `.env.example`.
- The server listens on `127.0.0.1` only, so other devices can't reach it.
- API keys are shown masked in the app.
