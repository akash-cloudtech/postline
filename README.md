# Morning pipeline (phase 1)

Fetches today's general + tech topics from Gemini, writes a spoken 2-minute
script for each, renders each as a PDF, and sends both to your network
printer. This is the piece that runs at 7 AM before you record.

Not included yet (future phases): Google Photos/Drive video detection,
caption/hashtag generation, approval flow, and posting to each platform.

## Setup

1. Get a free Gemini API key from https://aistudio.google.com/apikey

2. Create a virtual environment and install dependencies:
   ```
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env` and fill in your values:
   ```
   cp .env.example .env
   ```
   - `GEMINI_API_KEY` - from step 1
   - `PRINTER_NAME` - run `lpstat -p` on your Mac to see your printer's queue
     name, and use that exact name here
   - `OUTPUT_DIR` / `LOG_DIR` - any folders you want the PDFs and logs kept in

4. Test it manually first:
   ```
   python main.py
   ```
   This should print progress lines, generate two PDFs in `OUTPUT_DIR`, and
   send them to your printer. If `PRINTER_NAME` is left blank, it will skip
   the actual print and just tell you where the PDF is, so you can check the
   content before wiring up the printer.

## Scheduling it with launchd (macOS)

`launchd` is more reliable than cron on macOS, especially around sleep/wake.

1. Edit `com.akash.morningpipeline.plist`: replace `/Users/akash/...` with
   your actual home directory paths, and confirm the Python path with
   `which python3` (use the venv's python3 path if you want the venv's
   packages available).

2. Copy it into place and load it:
   ```
   cp com.akash.morningpipeline.plist ~/Library/LaunchAgents/
   launchctl load ~/Library/LaunchAgents/com.akash.morningpipeline.plist
   ```

3. To test the schedule without waiting for 6:55 AM:
   ```
   launchctl start com.akash.morningpipeline
   ```

4. Check `stdout.log` / `stderr.log` in your configured `LOG_DIR` to confirm
   it ran and see any errors.

5. Your Mac needs to be awake at 6:55 AM for this to fire. Either keep the
   Mac mini set to never sleep (recommended, since this is meant to run as a
   small always-on server), or configure a scheduled wake via
   `pmset repeat wakeorpoweron MTWRFSU 06:50:00`.

## Notes / next steps

- `topic_fetch.py` currently asks Gemini to pick topics from its own
  knowledge. For genuinely "trending today" topics, this should be extended
  to pull real trend data (Google Trends via `pytrends`, a news API, or
  Gemini's search-grounding tool) and feed that into the prompt.
- Once this phase is solid, the next piece to build is the video watcher
  (folder polling for new videos) and the caption/hashtag generator, which
  will reuse the same Gemini client pattern used here.
