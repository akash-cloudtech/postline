"""
Post a local video file to Instagram as a Reel.

Works with both token types Meta hands out:
  - "IG..."  tokens (Instagram login)          -> graph.instagram.com
  - "EAA..." tokens (Facebook / Unified login) -> graph.facebook.com
The publishing endpoints are the same on both hosts.

Flow:
  1. Create a REELS media container with upload_type=resumable
  2. Upload the video bytes directly to rupload.facebook.com
  3. Poll the container until Instagram finishes processing it
  4. Publish the container

Usage:
    python ig_post.py --check                       # verify token + account
    python ig_post.py path/to/video.mp4 "caption"   # post a Reel

From code:
    from ig_post import post_reel
    media_id = post_reel(Path("video.mp4"), "caption")
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"))
except ImportError:
    pass

IG_ACCESS_TOKEN = os.environ.get("IG_ACCESS_TOKEN", "").strip()
IG_USER_ID = os.environ.get("IG_USER_ID", "").strip()
IG_API_VERSION = os.environ.get("IG_API_VERSION", "v23.0")

IS_FB_TOKEN = IG_ACCESS_TOKEN.startswith("EAA")
GRAPH_HOST = "graph.facebook.com" if IS_FB_TOKEN else "graph.instagram.com"
GRAPH = f"https://{GRAPH_HOST}/{IG_API_VERSION}"
RUPLOAD = f"https://rupload.facebook.com/ig-api-upload/{IG_API_VERSION}"

POLL_INTERVAL_S = 5
POLL_TIMEOUT_S = 10 * 60


class IGPostError(Exception):
    pass


def _log(msg: str) -> None:
    print(f"[ig_post] {msg}", flush=True)


def _check_response(resp: requests.Response, step: str) -> dict:
    try:
        data = resp.json()
    except ValueError:
        raise IGPostError(f"{step}: HTTP {resp.status_code}, non-JSON response: {resp.text[:500]}")
    if resp.status_code != 200 or "error" in data:
        raise IGPostError(f"{step}: HTTP {resp.status_code}: {data.get('error', data)}")
    return data


def _require_config() -> None:
    if not IG_ACCESS_TOKEN:
        raise IGPostError("IG_ACCESS_TOKEN is not set (see .env.example)")
    if not IG_USER_ID:
        raise IGPostError("IG_USER_ID is not set - copy the numeric ID shown next to your "
                          "account under 'Generate access tokens' in the Meta app dashboard")


def check_account() -> dict:
    _require_config()
    resp = requests.get(
        f"{GRAPH}/{IG_USER_ID}",
        params={"fields": "id,username,media_count", "access_token": IG_ACCESS_TOKEN},
        timeout=30,
    )
    return _check_response(resp, "account check")


def _create_container(caption: str) -> tuple[str, str]:
    resp = requests.post(
        f"{GRAPH}/{IG_USER_ID}/media",
        params={"access_token": IG_ACCESS_TOKEN},
        data={"media_type": "REELS", "upload_type": "resumable", "caption": caption},
        timeout=30,
    )
    data = _check_response(resp, "create container")
    container_id = data["id"]
    upload_uri = data.get("uri") or f"{RUPLOAD}/{container_id}"
    return container_id, upload_uri


# Optional progress hook (the app sets this): called with 1 = container created /
# uploading, 2 = Instagram processing. Posting runs one at a time, so a module-level
# hook is enough.
on_progress = None


def _progress(step: int) -> None:
    if on_progress:
        try:
            on_progress(step)
        except Exception:
            pass


def _upload_video(upload_uri: str, video: Path) -> None:
    _progress(1)
    size = video.stat().st_size
    with video.open("rb") as f:
        resp = requests.post(
            upload_uri,
            headers={
                "Authorization": f"OAuth {IG_ACCESS_TOKEN}",
                "offset": "0",
                "file_size": str(size),
            },
            data=f,
            timeout=600,
        )
    _check_response(resp, "upload video")


def _wait_until_ready(container_id: str) -> None:
    _progress(2)
    deadline = time.monotonic() + POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        resp = requests.get(
            f"{GRAPH}/{container_id}",
            params={"fields": "status_code,status", "access_token": IG_ACCESS_TOKEN},
            timeout=30,
        )
        data = _check_response(resp, "poll status")
        status = data.get("status_code")
        _log(f"processing status: {status}")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise IGPostError(f"Instagram rejected the video: {data.get('status')}")
        time.sleep(POLL_INTERVAL_S)
    raise IGPostError(f"Timed out after {POLL_TIMEOUT_S}s waiting for Instagram to process the video")


def _publish(container_id: str) -> str:
    resp = requests.post(
        f"{GRAPH}/{IG_USER_ID}/media_publish",
        params={"access_token": IG_ACCESS_TOKEN},
        data={"creation_id": container_id},
        timeout=60,
    )
    return _check_response(resp, "publish")["id"]


def post_reel_from_url(video_url: str, caption: str) -> str:
    """Publish a Reel that Meta downloads itself from a public URL.

    Preferred path: direct uploads of full-length phone videos to rupload fail
    with ProcessingFailedError, while URL fetches (e.g. a Google Photos "=dv"
    link) process fine, HDR included.
    """
    _require_config()
    _log(f"Creating Reel container from URL (via {GRAPH_HOST})...")
    resp = requests.post(
        f"{GRAPH}/{IG_USER_ID}/media",
        params={"access_token": IG_ACCESS_TOKEN},
        data={"media_type": "REELS", "video_url": video_url, "caption": caption},
        timeout=60,
    )
    container_id = _check_response(resp, "create container")["id"]
    _progress(1)
    _log(f"Container {container_id}; Instagram is fetching and processing the video...")
    _wait_until_ready(container_id)
    media_id = _publish(container_id)
    _log(f"Published! media id: {media_id}")
    return media_id


def get_permalink(media_id: str) -> str | None:
    """Public instagram.com link for a published post (None if unavailable)."""
    try:
        resp = requests.get(
            f"{GRAPH}/{media_id}",
            params={"fields": "permalink", "access_token": IG_ACCESS_TOKEN},
            timeout=30,
        )
        return resp.json().get("permalink")
    except Exception:
        return None


STORY_MAX_S = 60


def video_duration_s(path: Path) -> float | None:
    """Duration from the file's 'mvhd' box (works for iPhone .mov and .mp4)."""
    with path.open("rb") as f:
        while True:
            header = f.read(8)
            if len(header) < 8:
                return None
            size, box = int.from_bytes(header[:4], "big"), header[4:8]
            hdr = 8
            if size == 1:
                size, hdr = int.from_bytes(f.read(8), "big"), 16
            if box == b"moov":
                moov = f.read(size - hdr)
                i = moov.find(b"mvhd")
                if i < 0:
                    return None
                v = moov[i + 4]
                if v == 1:
                    ts = int.from_bytes(moov[i + 24:i + 28], "big")
                    dur = int.from_bytes(moov[i + 28:i + 36], "big")
                else:
                    ts = int.from_bytes(moov[i + 16:i + 20], "big")
                    dur = int.from_bytes(moov[i + 20:i + 24], "big")
                return dur / ts if ts else None
            f.seek(size - hdr, 1)


def _trim_for_story(video: Path) -> Path:
    """59-second 720p H.264 copy via macOS's built-in avconvert."""
    out = video.with_name(f"{video.stem}_story.mp4")
    result = subprocess.run(
        ["avconvert", "--source", str(video), "--preset", "Preset1280x720",
         "--duration", str(STORY_MAX_S - 1), "--output", str(out), "--replace"],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not out.exists():
        raise IGPostError(f"avconvert failed: {result.stderr or result.stdout}")
    return out


def post_story(video: Path, video_url: str | None = None) -> str:
    """Publish `video` as an Instagram Story.

    <= 60 s and a public URL available -> Meta fetches the URL (original quality).
    Longer videos -> trimmed to 59 s locally and uploaded (Instagram refuses
    to publish Stories over 60 s: error_subcode 2207032).
    """
    _require_config()
    duration = video_duration_s(video)
    if video_url and duration is not None and duration <= STORY_MAX_S:
        _log("Creating Story container from URL...")
        resp = requests.post(
            f"{GRAPH}/{IG_USER_ID}/media",
            params={"access_token": IG_ACCESS_TOKEN},
            data={"media_type": "STORIES", "video_url": video_url},
            timeout=60,
        )
        container_id = _check_response(resp, "create story container")["id"]
    else:
        upload = video
        if duration is None or duration > STORY_MAX_S:
            _log(f"video is {duration or '?'}s - trimming to {STORY_MAX_S - 1}s for the Story")
            upload = _trim_for_story(video)
        _log(f"Creating Story container; uploading {upload.name} "
             f"({upload.stat().st_size / 1_000_000:.1f} MB)...")
        resp = requests.post(
            f"{GRAPH}/{IG_USER_ID}/media",
            params={"access_token": IG_ACCESS_TOKEN},
            data={"media_type": "STORIES", "upload_type": "resumable"},
            timeout=60,
        )
        data = _check_response(resp, "create story container")
        container_id = data["id"]
        _upload_video(data.get("uri") or f"{RUPLOAD}/{container_id}", upload)
    _wait_until_ready(container_id)
    media_id = _publish(container_id)
    _log(f"Story published! media id: {media_id}")
    return media_id


def post_reel(video: Path, caption: str) -> str:
    """Upload and publish `video` as a Reel. Returns the published media id."""
    _require_config()
    if not video.is_file():
        raise IGPostError(f"Video not found: {video}")

    _log(f"Creating Reel container (via {GRAPH_HOST})...")
    container_id, upload_uri = _create_container(caption)
    _log(f"Container {container_id}; uploading {video.name} "
         f"({video.stat().st_size / 1_000_000:.1f} MB)...")
    _upload_video(upload_uri, video)
    _log("Upload done; waiting for Instagram to process...")
    _wait_until_ready(container_id)
    media_id = _publish(container_id)
    _log(f"Published! media id: {media_id}")
    return media_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Post a video to Instagram as a Reel.")
    parser.add_argument("video", nargs="?", type=Path, help="Path to .mp4/.mov file")
    parser.add_argument("caption", nargs="?", default="", help="Caption text")
    parser.add_argument("--check", action="store_true", help="Only verify token/account")
    args = parser.parse_args()

    try:
        if args.check:
            data = check_account()
            _log(f"API host:     {GRAPH_HOST}")
            _log(f"username:     {data.get('username')}")
            _log(f"user_id:      {data.get('id')}")
            _log(f"media_count:  {data.get('media_count')}")
            return
        if not args.video:
            parser.error("video path is required unless --check is used")
        post_reel(args.video, args.caption)
    except IGPostError as e:
        _log(f"ERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
