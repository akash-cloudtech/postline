"""
Generate an Instagram caption, 5 hashtags, and a location for a video.

Steps:
  1. Read GPS + recording date from the video's own metadata (iPhone .mov/.mp4)
  2. Reverse-geocode GPS to a city-level place name (OpenStreetMap Nominatim;
     coordinates are rounded to ~1 km before being sent)
  3. Upload the video to Gemini, which watches the frames AND listens to the audio
  4. Ask for structured JSON: caption, hashtags, summary
  5. Delete the uploaded file from Gemini

Usage:
    python caption_gen.py path/to/video.mov
"""
import json
import math
import os
import re
import sys
import time
from pathlib import Path

import requests
from pydantic import BaseModel, Field

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"))
except ImportError:
    pass

from google import genai
from google.genai import types

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
GEMINI_FALLBACK_MODELS = [m.strip() for m in
                          os.environ.get("GEMINI_FALLBACK_MODELS", "").split(",") if m.strip()]

# Optional voice/style guidance appended to the prompt
CAPTION_STYLE = os.environ.get("CAPTION_STYLE", "").strip()

# Optional "home zone": never attach a location within HOME_RADIUS_KM of home
HOME_LAT = os.environ.get("HOME_LAT", "").strip()
HOME_LON = os.environ.get("HOME_LON", "").strip()
HOME_RADIUS_KM = float(os.environ.get("HOME_RADIUS_KM", "5"))

NUM_HASHTAGS = int(os.environ.get("NUM_HASHTAGS", "5"))


def log(msg: str) -> None:
    print(f"[caption_gen] {msg}", flush=True)


# ---------------------------------------------------------------- metadata ---

def _find_moov(path: Path) -> bytes:
    """Return the raw bytes of the top-level 'moov' box (where metadata lives)."""
    with path.open("rb") as f:
        while True:
            header = f.read(8)
            if len(header) < 8:
                return b""
            size = int.from_bytes(header[:4], "big")
            box = header[4:8]
            hdr_len = 8
            if size == 1:  # 64-bit size
                size = int.from_bytes(f.read(8), "big")
                hdr_len = 16
            elif size == 0:  # box runs to end of file
                return f.read() if box == b"moov" else b""
            if box == b"moov":
                return f.read(size - hdr_len)
            f.seek(size - hdr_len, 1)


_ISO6709_RE = re.compile(rb"([+-]\d{1,2}\.\d+)([+-]\d{1,3}\.\d+)(?:[+-]\d+\.\d+)?/")
_DATE_RE = re.compile(rb"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4})")


def read_video_metadata(path: Path) -> dict:
    moov = _find_moov(path)
    meta: dict = {"lat": None, "lon": None, "recorded_at": None}
    m = _ISO6709_RE.search(moov)
    if m:
        meta["lat"], meta["lon"] = float(m.group(1)), float(m.group(2))
    d = _DATE_RE.search(moov)
    if d:
        meta["recorded_at"] = d.group(1).decode()
    else:
        # .mp4 files (e.g. Google Photos re-encodes) only carry the 'mvhd' creation
        # time: seconds since 1904-01-01 UTC
        i = moov.find(b"mvhd")
        if i >= 0:
            v = moov[i + 4]
            secs = int.from_bytes(moov[i + 8:i + (16 if v == 1 else 12)], "big")
            if secs > 0:
                from datetime import datetime, timedelta, timezone
                dt = datetime(1904, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=secs)
                if dt.year >= 2010:
                    meta["recorded_at"] = dt.isoformat(timespec="seconds")
    return meta


# ---------------------------------------------------------------- location ---

def _distance_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def place_name(lat: float | None, lon: float | None,
               home_radius_km: float | None = None) -> tuple[str | None, bool]:
    """(city-level place name or None, recorded_inside_home_zone)."""
    if lat is None or lon is None:
        return None, False
    radius = HOME_RADIUS_KM if home_radius_km is None else home_radius_km
    if HOME_LAT and HOME_LON and _distance_km(lat, lon, float(HOME_LAT), float(HOME_LON)) <= radius:
        log("video was recorded inside the home zone - not adding a location")
        return None, True
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": round(lat, 2), "lon": round(lon, 2), "format": "jsonv2",
                    "zoom": 10, "accept-language": "en"},
            headers={"User-Agent": "social-media-handler/0.1"},
            timeout=20,
        )
        addr = resp.json().get("address", {})
    except Exception as e:
        log(f"reverse geocoding failed ({e}) - continuing without location")
        return None, False
    city = (addr.get("city") or addr.get("town") or addr.get("village")
            or addr.get("municipality") or addr.get("county"))
    region = addr.get("state") or addr.get("country")
    return (", ".join(p for p in (city, region) if p) or None), False


# ------------------------------------------------------------------ gemini ---

class CaptionResult(BaseModel):
    title: str = Field(description="2-4 word internal title for the video, e.g. 'First snow'")
    caption: str = Field(description="Instagram caption body, without hashtags")
    hashtags: list[str] = Field(description=f"Exactly {NUM_HASHTAGS} hashtags, each starting with #")
    summary: str = Field(description="One sentence describing what the video is about, for the reviewer")


PROMPT = """You are a social media manager. Watch this vertical video (a Reel) carefully:
listen to everything that is said and look at what is on screen.

Write an Instagram Reel caption for it.

Rules:
- First line is a scroll-stopping hook based on the video's actual content.
- Then 1-3 short lines that add value or context - in the creator's own voice,
  first person, conversational. Do not just transcribe the video.
- End with a simple call to action (question, save, follow, comment).
- A few relevant emojis are fine; don't overdo it.
- Keep the caption under 1,000 characters. Do NOT put hashtags in the caption.
- Hashtags: exactly {n} - mix 2 broad + 3 niche tags that match the content.
  Lowercase or CamelCase, no spaces, each starts with #.
- Only describe what is really in the video; never invent facts.
- Write in English only: no characters from other scripts (e.g. Chinese,
  Japanese) - emojis are fine.
- Also give the video a 2-4 word plain title (no emojis) for the creator's dashboard.
{location_line}{style_line}{instruction_line}
If the creator's style notes conflict with the rules above (length, emoji use,
tone), follow the style notes."""


def _client() -> genai.Client:
    if not GEMINI_API_KEY:
        raise SystemExit("GEMINI_API_KEY is not set in .env")
    return genai.Client(
        api_key=GEMINI_API_KEY,
        # Fail fast on an overloaded model so we can fall back to the next one
        http_options=types.HttpOptions(timeout=180_000,
                                       retry_options=types.HttpRetryOptions(attempts=1)),
    )


def _upload(client: genai.Client, path: Path):
    log(f"uploading {path.name} ({path.stat().st_size / 1_000_000:.1f} MB) to Gemini...")
    f = client.files.upload(file=str(path))
    while f.state and f.state.name == "PROCESSING":
        time.sleep(3)
        f = client.files.get(name=f.name)
    if f.state and f.state.name != "ACTIVE":
        raise RuntimeError(f"Gemini could not process the video (state={f.state.name})")
    return f


def _normalize_hashtags(tags: list[str]) -> list[str]:
    out: list[str] = []
    for t in tags:
        t = "#" + re.sub(r"[^\w]", "", t.lstrip("#"))
        if len(t) > 1 and t.lower() not in (x.lower() for x in out):
            out.append(t)
    return out[:NUM_HASHTAGS]


def generate_caption(video: Path, *, style: str | None = None, n_tags: int | None = None,
                     auto_city: bool = True, home_radius_km: float | None = None,
                     instruction: str | None = None) -> dict:
    """Overrides (style, n_tags, ...) come from the app's Settings; None = .env value."""
    style = CAPTION_STYLE if style is None else style.strip()
    n_tags = n_tags or NUM_HASHTAGS
    meta = read_video_metadata(video)
    location, home = None, False
    if auto_city:
        location, home = place_name(meta["lat"], meta["lon"], home_radius_km)
    log(f"recorded_at={meta['recorded_at']}  location={location or '(none)'}")

    prompt = PROMPT.format(
        n=n_tags,
        location_line=(f"- The video was recorded in {location}. Mention it only if it "
                       f"fits naturally.\n" if location else ""),
        style_line=(f"- Creator's style notes: {style}\n" if style else ""),
        instruction_line=(f"- Extra instruction for this caption: {instruction}\n"
                          if instruction else ""),
    )

    client = _client()
    uploaded = _upload(client, video)
    try:
        last_err = None
        for model in [GEMINI_MODEL, *GEMINI_FALLBACK_MODELS]:
            t0 = time.time()
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=[uploaded, prompt],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=CaptionResult,
                    ),
                )
                result = CaptionResult.model_validate_json(resp.text)
                log(f"generated with {model} in {time.time() - t0:.1f}s")
                break
            except Exception as e:
                last_err = e
                log(f"{model} failed: {str(e)[:120]} - trying next model")
        else:
            raise RuntimeError(f"All Gemini models failed; last error: {last_err}")
    finally:
        try:
            client.files.delete(name=uploaded.name)
        except Exception:
            pass

    hashtags = _normalize_hashtags(result.hashtags)[:n_tags]
    gen_seconds = round(time.time() - t0)
    full_caption = result.caption.strip()
    if location:
        full_caption += f"\n\n📍 {location}"
    full_caption += "\n\n" + " ".join(hashtags)

    return {
        "video": str(video),
        "model": model,
        "gen_seconds": gen_seconds,
        "title": result.title.strip(),
        "home": home,
        "recorded_at": meta["recorded_at"],
        "location": location,
        "caption": result.caption.strip(),
        "hashtags": hashtags,
        "summary": result.summary.strip(),
        "full_caption": full_caption,
    }


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python caption_gen.py path/to/video.mov")
    out = generate_caption(Path(sys.argv[1]))
    print("\n" + "=" * 60)
    print(f"SUMMARY:  {out['summary']}")
    print(f"LOCATION: {out['location']}")
    print(f"MODEL:    {out['model']}")
    print("=" * 60)
    print(out["full_caption"])
    print("=" * 60)
    dest = Path(__file__).parent / "data" / "captions"
    dest.mkdir(parents=True, exist_ok=True)
    out_file = dest / (Path(out["video"]).stem + ".json")
    out_file.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    log(f"saved {out_file}")


if __name__ == "__main__":
    main()
