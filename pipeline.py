"""
Album -> Instagram pipeline.

    Phone: record -> add to shared album "To Post"
    Mac:   check album -> new video found -> download -> Gemini caption
           -> post as Instagram Reel (with caption) + Instagram Story

Each run checks the album once. Items already handled are remembered in
data/state.json so nothing is ever posted twice.

First run safety: if there's no state yet, every item currently in the album is
recorded as "seen" and NOTHING is posted. Only videos added afterwards get posted.

Usage:
    python pipeline.py               # check once
    python pipeline.py --watch       # keep checking every POLL_INTERVAL_S seconds
    python pipeline.py --dry-run     # show what would be posted, post nothing
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"))
except ImportError:
    pass

from gphotos_fetch import GPhotosError, _session, download_video, list_items
from caption_gen import generate_caption
from ig_post import IGPostError, get_permalink, post_reel_from_url, post_story

ROOT = Path(__file__).parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(ROOT / "data")))
VIDEO_DIR = DATA_DIR / "videos"
STATE_FILE = DATA_DIR / "state.json"
LOG_FILE = DATA_DIR / "pipeline.log"
NOTIFY = os.environ.get("NOTIFY", "1") != "0"

ALBUM_URL = os.environ.get("GPHOTOS_ALBUM_URL", "").strip()
DEFAULT_CAPTION = os.environ.get("DEFAULT_CAPTION", "")
POLL_INTERVAL_S = int(os.environ.get("POLL_INTERVAL_S", "300"))
MAX_ATTEMPTS = 3


class _Tee:
    """Send everything printed (by this file and the modules it calls) to the
    terminal AND to data/pipeline.log."""

    def __init__(self, stream, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._stream, self._file = stream, path.open("a", buffering=1)

    def write(self, s: str) -> int:
        self._stream.write(s)
        self._file.write(s)
        return len(s)

    def flush(self) -> None:
        self._stream.flush()
        self._file.flush()


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] [pipeline] {msg}", flush=True)


def notify(title: str, message: str) -> None:
    """macOS notification banner (Notification Centre)."""
    if not NOTIFY:
        return
    esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
    subprocess.run(
        ["osascript", "-e",
         f'display notification "{esc(message)}" with title "{esc(title)}" sound name "Glass"'],
        capture_output=True,
    )


def load_state() -> dict | None:
    if not STATE_FILE.exists():
        return None
    return json.loads(STATE_FILE.read_text())


def save_state(state: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_FILE)


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def run_once(dry_run: bool = False) -> None:
    if not ALBUM_URL:
        raise SystemExit("GPHOTOS_ALBUM_URL is not set in .env")

    s = _session()
    items = list_items(ALBUM_URL, s)
    state = load_state()

    if state is None:
        state = {"items": {k.media_key: {"status": "baseline", "at": now()} for k in items}}
        if not dry_run:
            save_state(state)
        log(f"First run: recorded {len(items)} existing item(s) as seen, posted nothing. "
            f"Add a NEW video to the album to test posting.")
        return

    seen = state["items"]
    new = [it for it in items
           if it.media_key not in seen
           or (seen[it.media_key]["status"] == "failed"
               and seen[it.media_key].get("attempts", 0) < MAX_ATTEMPTS)]
    log(f"{len(items)} item(s) in album, {len(new)} new/retryable")

    for it in new:
        rec = seen.setdefault(it.media_key, {"attempts": 0})
        log(f"New item {it.media_key[:20]}... ({it.width}x{it.height})")

        if dry_run:
            log("  dry run: would download and post if it's a video")
            continue

        try:
            path = download_video(it, VIDEO_DIR, s)
        except Exception as e:
            rec.update(status="failed", attempts=rec.get("attempts", 0) + 1,
                       error=f"download: {e}", at=now())
            save_state(state)
            log(f"  download failed: {e}")
            continue

        if path is None:
            rec.update(status="not_video", at=now())
            save_state(state)
            log("  not a video, skipping")
            continue

        log(f"  downloaded {path.name} ({path.stat().st_size / 1_000_000:.1f} MB)")
        notify("📥 New video found", "Writing caption and posting to Instagram…")
        rec["file"] = str(path)
        video_url = f"{it.base_url}=dv"  # public URL Meta fetches the Reel from

        def fail(step: str, err: Exception) -> None:
            rec.update(status="failed", attempts=rec.get("attempts", 0) + 1,
                       error=f"{step}: {err}", at=now())
            save_state(state)
            log(f"  {step} failed: {err}")
            left = MAX_ATTEMPTS - rec["attempts"]
            notify(f"❌ Instagram {step} failed",
                   f"{str(err)[:120]} — " + (f"will retry ({left} left)" if left > 0
                                            else "no retries left, check data/pipeline.log"))

        # 1. Caption (reused on retries so a retry posts the same text)
        if "caption" not in rec:
            try:
                cap = generate_caption(path)
            except Exception as e:
                if not DEFAULT_CAPTION:
                    fail("caption", e)
                    continue
                log(f"  caption generation failed ({e}) - using DEFAULT_CAPTION")
                cap = {"full_caption": DEFAULT_CAPTION, "summary": "", "model": None}
            rec.update(caption=cap["full_caption"], summary=cap.get("summary"),
                       caption_model=cap.get("model"))
            save_state(state)
            log(f"  caption ({cap.get('model')}): {cap['full_caption'][:80]!r}...")

        # 2. Reel. Mark "posting" BEFORE publishing so a crash mid-post can't
        #    cause a duplicate on the next run (stuck items need a manual look).
        if "reel_id" not in rec:
            rec.update(status="posting_reel", at=now())
            save_state(state)
            try:
                rec["reel_id"] = post_reel_from_url(video_url, rec["caption"])
            except IGPostError as e:
                fail("reel", e)
                continue
            rec["reel_url"] = get_permalink(rec["reel_id"])
            save_state(state)
            log(f"  Reel posted: {rec['reel_url'] or rec['reel_id']}")
            notify("✅ Reel posted", rec["reel_url"] or rec["caption"][:100])

        # 3. Story (same video; trimmed to 59 s if longer than 60 s)
        if "story_id" not in rec:
            rec.update(status="posting_story", at=now())
            save_state(state)
            try:
                rec["story_id"] = post_story(path, video_url)
            except IGPostError as e:
                fail("story", e)
                continue
            log(f"  Story posted, media id {rec['story_id']}")

        rec.update(status="posted", at=now())
        rec.pop("error", None)
        save_state(state)
        log("  done: Reel + Story live on Instagram")
        notify("✅ Story posted", "Reel + Story are both live on Instagram")


def main() -> None:
    parser = argparse.ArgumentParser(description="Google Photos album -> Instagram Reels")
    parser.add_argument("--watch", action="store_true",
                        help=f"Keep checking every POLL_INTERVAL_S seconds (now {POLL_INTERVAL_S})")
    parser.add_argument("--dry-run", action="store_true", help="Post nothing, change nothing")
    args = parser.parse_args()
    sys.stdout = _Tee(sys.stdout, LOG_FILE)

    while True:
        try:
            run_once(dry_run=args.dry_run)
        except GPhotosError as e:
            log(f"album check failed: {e}")
        if not args.watch:
            break
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    main()
