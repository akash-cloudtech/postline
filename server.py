"""
Postline - local app server.

    ./venv/bin/python server.py        ->  http://localhost:8420

One process does everything:
  - serves the UI (ui/Postline.dc.html) and a JSON API for it
  - background loop: checks the Google Photos album every N minutes, downloads
    new videos, has Gemini write captions, and parks them in the Review queue
  - posts to Instagram (Reel + Story) only after you approve, now or on schedule

Replaces running pipeline.py by hand - don't run both at the same time.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"))
except ImportError:
    pass

import uvicorn
from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

import ig_post
import store
from caption_gen import GEMINI_FALLBACK_MODELS, GEMINI_MODEL, generate_caption
from gphotos_fetch import _session, download_video, list_items
from ig_post import IGPostError, get_permalink, post_reel, post_reel_from_url, post_story

ROOT = Path(__file__).parent
DATA = ROOT / "data"
VIDEO_DIR = DATA / "videos"
UI_DIR = ROOT / "ui"
LOG_FILE = DATA / "postline.log"
SETTINGS_FILE = DATA / "settings.json"
PORT = int(os.environ.get("POSTLINE_PORT", "8420"))

ALBUM_URL = os.environ.get("GPHOTOS_ALBUM_URL", "").strip()
HANDLE = "@" + os.environ.get("IG_HANDLE", "akash.verma.dso").lstrip("@")
TOKEN_EXPIRES_AT = os.environ.get("IG_TOKEN_EXPIRES_AT", "").strip()  # YYYY-MM-DD

DEFAULT_SETTINGS = {
    "paused": False, "paused_at": None,
    "interval": "5 min",
    "set": {"autoCity": True, "approval": True, "nNew": True, "nReview": True, "nPosted": True,
            "nFailed": True, "nToken": True, "nPaused": False, "defReel": True, "defStory": True},
    "radius": float(os.environ.get("HOME_RADIUS_KM", "5")),
    "hashN": int(os.environ.get("NUM_HASHTAGS", "5")),
    "vstyle": os.environ.get("CAPTION_STYLE", ""),
}
INTERVALS = {"1 min": 60, "2 min": 120, "5 min": 300, "15 min": 900, "30 min": 1800, "1 hour": 3600}

# ------------------------------------------------------------------ logging --

_LEVEL_RE = re.compile(r"\b(ERROR|FAILED|failed|Traceback)\b")


class _LogTee:
    """Mirror stdout to the terminal and to data/postline.log as
    'HH:MM:SS<TAB>LEVEL<TAB>module<TAB>message' lines (what the UI's log drawer shows)."""

    def __init__(self, stream):
        self._s, self._buf = stream, ""
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._f = LOG_FILE.open("a", buffering=1)

    def write(self, s: str) -> int:
        self._s.write(s)
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                m = re.match(r"\[([\w_]+)\]\s*(.*)", line)
                mod, msg = (m.group(1), m.group(2)) if m else ("app", line)
                mod = {"ig_post": "ig", "caption_gen": "gemini", "pipeline": "app"}.get(mod, mod)
                lvl = "ERROR" if _LEVEL_RE.search(msg) else ("WARN" if "trimming" in msg else "INFO")
                self._f.write(f"{datetime.now():%H:%M:%S}\t{lvl}\t{mod}\t{msg}\n")
        return len(s)

    def flush(self) -> None:
        self._s.flush()
    def __getattr__(self, name):  # isatty(), fileno(), encoding... -> real stream
        return getattr(self._s, name)


def log(msg: str) -> None:
    print(f"[app] {msg}", flush=True)


# ----------------------------------------------------------------- settings --

_settings_lock = threading.Lock()


def load_settings() -> dict:
    with _settings_lock:
        s = json.loads(SETTINGS_FILE.read_text()) if SETTINGS_FILE.exists() else {}
    merged = {**DEFAULT_SETTINGS, **s}
    merged["set"] = {**DEFAULT_SETTINGS["set"], **s.get("set", {})}
    return merged


def save_settings(patch: dict) -> dict:
    cur = load_settings()
    for k, v in patch.items():
        if k == "set" and isinstance(v, dict):
            cur["set"].update(v)
        elif k in DEFAULT_SETTINGS:
            cur[k] = v
    with _settings_lock:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(cur, indent=2, ensure_ascii=False))
    return cur


def notify(kind: str, title: str, message: str) -> None:
    """macOS notification, respecting the per-event toggles in Settings."""
    if not load_settings()["set"].get(kind, True):
        return
    esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
    subprocess.run(["osascript", "-e", f'display notification "{esc(message)}" '
                    f'with title "{esc(title)}" sound name "Glass"'], capture_output=True)


# ---------------------------------------------------------------- formatting --

def _dt(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return d.astimezone().replace(tzinfo=None) if d.tzinfo else d
    except ValueError:
        return None


def _time(d: datetime) -> str:
    return d.strftime("%I:%M %p").lstrip("0")


def fmt_rec(iso):  # 26 Nov 2026 · 7:35 AM
    d = _dt(iso)
    return f"{d.day} {d:%b %Y} · {_time(d)}" if d else "—"


def fmt_short(iso):  # 26 Nov · 7:51 AM
    d = _dt(iso)
    return f"{d.day} {d:%b} · {_time(d)}" if d else "—"


def fmt_tl(iso):  # 26 Nov 7:51 AM
    d = _dt(iso)
    return f"{d.day} {d:%b} {_time(d)}" if d else ""


def day_label(d: datetime) -> str:
    today = datetime.now().date()
    if d.date() == today:
        return "Today"
    if d.date() == today + timedelta(days=1):
        return "Tomorrow"
    return f"{d:%a} {d.day} {d:%b}"


def ago(d: datetime | None) -> str:
    if not d:
        return "never"
    s = (datetime.now() - d).total_seconds()
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{int(s // 60)} min ago"
    if s < 86400:
        return f"{int(s // 3600)} h ago"
    return f"{int(s // 86400)} d ago"


def mask(v: str, keep: int = 4) -> str:
    return v[:keep] + "•" * 22 + v[-3:] if len(v) > keep + 3 else "not set"


def compose_caption(it: dict) -> str:
    text = (it["caption"] or "").strip()
    if it.get("loc") and not it.get("home"):
        text += f"\n\n📍 {it['loc']}"
    if it["tags"]:
        text += "\n\n" + " ".join(it["tags"])
    return text


def plain_error(err: str, part: str) -> tuple[str, str]:
    e = str(err)
    if "2207032" in e or "Create media fail" in e:
        return (f"{part} failed",
                "Instagram refused it, usually because a Story is longer than 60 s. Retry trims it to 59 s.")
    if "'code': 190" in e or "access token" in e.lower():
        return (f"{part} failed", "Your Instagram token has expired or is invalid. Reconnect in Platforms, then retry.")
    if "Timed out" in e:
        return (f"{part} failed", "Instagram took too long to process the video. Retry in a few minutes.")
    if "ProcessingFailedError" in e:
        return (f"{part} failed", "Instagram couldn't process the uploaded file. Try a shorter or smaller video.")
    return (f"{part} failed", e[:300])


# ------------------------------------------------------------------ runtime --

runtime = {"last_check": None, "last_check_new": 0, "last_check_err": None,
           "next_check": datetime.now(), "gemini": None}
cap_pool = ThreadPoolExecutor(1, thread_name_prefix="caption")
post_pool = ThreadPoolExecutor(1, thread_name_prefix="post")
_check_lock = threading.Lock()


def token_days() -> int | None:
    if not TOKEN_EXPIRES_AT:
        return None
    try:
        return (datetime.fromisoformat(TOKEN_EXPIRES_AT).date() - datetime.now().date()).days
    except ValueError:
        return None


# ------------------------------------------------------------------- album --

def _import_legacy_state(album_keys: list[str]) -> None:
    """First run: adopt pipeline.py's state.json so already-posted videos show in
    History and nothing that was already in the album gets posted."""
    legacy = DATA / "state.json"
    seen = json.loads(legacy.read_text())["items"] if legacy.exists() else {}
    for key in album_keys:
        rec = seen.get(key, {})
        capfile = DATA / "captions" / f"{key}.json"
        cap = json.loads(capfile.read_text()) if capfile.exists() else None
        if cap and cap.get("posted_media_id"):
            store.insert({
                "id": key, "source": "album", "title": "First snow", "file": cap.get("video"),
                "recorded_at": cap.get("recorded_at"), "status": "posted", "cap": "ok",
                "caption": cap.get("caption", ""), "tags": cap.get("hashtags", []),
                "loc": cap.get("location"), "summary": cap.get("summary", ""),
                "model": cap.get("model"), "d_reel": "ok",
                "d_story": "ok" if cap.get("posted_story_id") else "off",
                "reel_id": cap["posted_media_id"], "reel_url": get_permalink(cap["posted_media_id"]),
                "story_id": cap.get("posted_story_id"), "story_at": store.now_iso(),
                "posted_at": cap.get("recorded_at"), "step": 3,
            })
            store.add_event(key, "Imported from earlier pipeline runs (already posted)", "info")
        else:
            store.insert({"id": key, "source": "album", "status": "baseline", "title": rec.get("status")})
    log(f"first run: adopted {len(album_keys)} existing album item(s); nothing will be re-posted")


def check_album() -> int:
    """Look for new videos; queue each for download + caption. Returns # new."""
    if not ALBUM_URL:
        runtime["last_check_err"] = "GPHOTOS_ALBUM_URL is not set in .env"
        return 0
    with _check_lock:
        try:
            items = list_items(ALBUM_URL, _session())
        except Exception as e:
            runtime.update(last_check=datetime.now(), last_check_err=str(e))
            log(f"album check failed: {e}")
            return 0
        if store.count() == 0:
            _import_legacy_state([i.media_key for i in items])
        known = store.ids()
        new = [i for i in items if i.media_key not in known]
        log(f'check album items={len(items)} new={len(new)}')
        s = load_settings()["set"]
        for it in new:
            store.insert({"id": it.media_key, "source": "album", "base_url": it.base_url,
                          "w": it.width, "h": it.height, "status": "detected", "cap": "generating",
                          "title": "New video", "reel": s["defReel"], "story": s["defStory"]})
            store.add_event(it.media_key, "New video found in SocialMedia", "info")
            cap_pool.submit(process_new, it)
        runtime.update(last_check=datetime.now(), last_check_new=len(new), last_check_err=None)
        return len(new)


def process_new(album_item) -> None:
    key = album_item.media_key
    try:
        path = download_video(album_item, VIDEO_DIR, _session())
    except Exception as e:
        store.update(key, status="failed", cap="failed", err_t="Download failed", err_x=str(e)[:300])
        store.add_event(key, f"Download failed: {e}", "e")
        return
    if path is None:
        store.update(key, status="notvideo", cap="ok", title="Photo", d_reel="off", d_story="off")
        store.add_event(key, "Photo, not a video. Ignored.", "info")
        return
    store.update(key, file=str(path), size_bytes=path.stat().st_size,
                 dur=ig_post.video_duration_s(path) or 0)
    notify("nNew", "📥 New video found", "Writing the caption…")
    write_caption(key)


def write_caption(item_id: str, instruction: str | None = None) -> None:
    it = store.get(item_id)
    if not it or not it.get("file"):
        return
    st = load_settings()
    store.update(item_id, cap="generating")
    t0 = time.time()
    try:
        r = generate_caption(Path(it["file"]), style=st["vstyle"], n_tags=int(st["hashN"]),
                             auto_city=st["set"]["autoCity"], home_radius_km=float(st["radius"]),
                             instruction=instruction)
        runtime["gemini"] = {"ok": True, "model": r["model"], "secs": time.time() - t0, "at": datetime.now()}
        fields = dict(cap="ok", caption=r["caption"], tags=r["hashtags"], loc=r["location"],
                      home=r["home"], summary=r["summary"], model=r["model"],
                      gen_time=f"{r['gen_seconds']}s", cap_err=None)
        if not it.get("recorded_at") and r.get("recorded_at"):
            fields["recorded_at"] = r["recorded_at"]
        if it["title"] in (None, "", "New video") or it["source"] == "upload":
            fields["title"] = r.get("title") or it["title"]
        if it["status"] == "detected":
            fields["status"] = "review"
        store.update(item_id, **fields)
        store.add_event(item_id, f"Caption written ({r['model']}, {r['gen_seconds']}s)", "info")
        notify("nReview", "✍️ Caption ready for review", r["caption"][:100])
    except Exception as e:
        runtime["gemini"] = {"ok": False, "model": GEMINI_MODEL, "err": str(e), "at": datetime.now()}
        rec = _dt(it.get("recorded_at")) or datetime.now()
        fields = dict(cap="failed", cap_err=f"Gemini couldn't write a caption ({str(e)[:140]}). "
                      "We added a basic one. You can post with it, edit it, or try again.")
        if not it.get("caption"):
            fields.update(caption=f"New video · {rec.day} {rec:%b %Y} ✨", gen_time=None)
        if it["status"] == "detected":
            fields["status"] = "review"
        store.update(item_id, **fields)
        store.add_event(item_id, "Caption couldn't be written, used fallback", "warn")


# ------------------------------------------------------------------- posting --

def _fresh_video_url(it: dict) -> str | None:
    if it["source"] != "album":
        return None
    try:
        for a in list_items(ALBUM_URL, _session()):
            if a.media_key == it["id"]:
                store.update(it["id"], base_url=a.base_url)
                return f"{a.base_url}=dv"
    except Exception:
        pass
    return f"{it['base_url']}=dv" if it.get("base_url") else None


def post_item(item_id: str) -> None:
    it = store.get(item_id)
    if not it:
        return
    store.update(item_id, status="posting", step=0, err_t=None, err_x=None)
    ig_post.on_progress = lambda n: store.update(item_id, step=n)
    url = _fresh_video_url(it)
    file = Path(it["file"]) if it.get("file") else None
    errors = []
    try:
        if it["reel"] and it["d_reel"] != "ok":
            store.update(item_id, d_reel="posting")
            try:
                mid = post_reel_from_url(url, compose_caption(it)) if url else post_reel(file, compose_caption(it))
                store.update(item_id, d_reel="ok", reel_id=mid, reel_url=get_permalink(mid))
                store.add_event(item_id, "Reel posted", "ok")
                notify("nPosted", "✅ Reel posted", it["title"])
            except Exception as e:
                store.update(item_id, d_reel="fail")
                store.add_event(item_id, f"Reel failed: {str(e)[:120]}", "e")
                errors.append(plain_error(e, "Reel"))
        elif not it["reel"]:
            store.update(item_id, d_reel="off")

        if it["story"] and it["d_story"] != "ok":
            store.update(item_id, d_story="posting")
            try:
                sid = post_story(file, url)
                store.update(item_id, d_story="ok", story_id=sid, story_at=store.now_iso())
                store.add_event(item_id, "Story posted" + (" (trimmed to 59s)" if (it["dur"] or 0) > 60 else ""), "ok")
            except Exception as e:
                store.update(item_id, d_story="fail")
                store.add_event(item_id, f"Story failed: {str(e)[:120]}", "e")
                errors.append(plain_error(e, "Story"))
        elif not it["story"]:
            store.update(item_id, d_story="off")
    finally:
        ig_post.on_progress = None

    it = store.get(item_id)
    wanted = [d for d, on in ((it["d_reel"], it["reel"]), (it["d_story"], it["story"])) if on]
    ok = sum(d == "ok" for d in wanted)
    status = "posted" if ok == len(wanted) else "partial" if ok else "failed"
    attempts = it["attempts"] + 1
    fields = dict(status=status, attempts=attempts, step=3 if ok else it["step"])
    if ok and not it.get("posted_at"):
        fields["posted_at"] = store.now_iso()
    if errors:
        t, x = errors[0]
        fields.update(err_t=f"{t} · attempt {attempts} of 3", err_x=x)
        notify("nFailed", f"❌ {t}", x[:120])
    store.update(item_id, **fields)


def worker_loop() -> None:
    while True:
        try:
            st = load_settings()
            if not st["paused"]:
                if datetime.now() >= runtime["next_check"]:
                    check_album()
                    runtime["next_check"] = datetime.now() + timedelta(seconds=INTERVALS.get(st["interval"], 300))
                for it in store.due_scheduled(store.now_iso()):
                    store.update(it["id"], status="posting")
                    store.add_event(it["id"], "Scheduled time reached, posting", "accent")
                    post_pool.submit(post_item, it["id"])
        except Exception as e:
            log(f"worker error: {e}")
        time.sleep(5)


# ---------------------------------------------------------------- API state --

def _cal_entry(it: dict, when: datetime, status: str) -> dict:
    return {"id": it["id"], "date": when.strftime("%Y-%m-%d"),
            "mins": when.hour * 60 + when.minute, "title": it["title"], "status": status}


def build_state() -> dict:
    items = store.all_items()
    now = datetime.now()
    queue, hist, cal, uns = [], [], [], []
    for it in items:
        st = it["status"]
        rec = it.get("recorded_at") or it.get("created_at")
        story_dt = _dt(it.get("story_at"))
        d_story = it["d_story"]
        if d_story == "ok" and story_dt and now - story_dt > timedelta(hours=24):
            d_story = "expired"
        base = {"id": it["id"], "title": it["title"] or "Untitled", "dur": it["dur"] or 0,
                "rec": fmt_rec(rec), "summary": it["summary"] or "", "tags": it["tags"],
                "reelUrl": it.get("reel_url"),
                "storyUrl": f"https://www.instagram.com/stories/{HANDLE.lstrip('@')}/" if it.get("story_id") else None}
        recently_posted = st in ("posted", "partial") and _dt(it.get("posted_at")) and now - _dt(it["posted_at"]) < timedelta(minutes=10)
        if st in ("detected", "review", "scheduled", "posting") or recently_posted:
            queue.append({**base, "thumb": it["title"] or "", "res": f"{it['w'] or 1080}×{it['h'] or 1920}",
                          "size": f"{(it['size_bytes'] or 0) / 1_000_000:.1f} MB",
                          "status": "generating" if st == "detected" else st,
                          "cap": it["cap"], "caption": it["caption"] or "", "loc": it.get("loc"),
                          "home": it["home"], "model": it.get("model") or GEMINI_MODEL,
                          "genTime": it.get("gen_time") or "", "reel": it["reel"], "story": it["story"],
                          "err": it.get("cap_err"), "step": it["step"], "media": f"/media/{it['id']}",
                          "schedAt": (day_label(_dt(it["sched_at"])) + " · " + _time(_dt(it["sched_at"])))
                          if st == "scheduled" and it.get("sched_at") else None})
        if st in ("posted", "partial", "failed", "rejected", "notvideo"):
            d = [] if st == "notvideo" else [it["d_reel"] if it["reel"] else "off", d_story if it["story"] else "off"]
            left = 24 - (now - story_dt).total_seconds() / 3600 if story_dt else None
            tl = [[fmt_tl(e["ts"]), e["text"], "e" if e["kind"] == "e" else ""] for e in store.events(item_id=it["id"])]
            hist.append({**base, "status": st, "cap": it["caption"] or "",
                         "posted": fmt_short(it.get("posted_at")) if it.get("posted_at") else "—",
                         "d": d, "storyNote": f"Story live · {int(left)} h left" if left and left > 0 else None,
                         "err": {"t": it["err_t"], "x": it["err_x"]} if it.get("err_t") else None,
                         "tl": tl, "model": it.get("model") or "—"})
        if st == "scheduled" and it.get("sched_at"):
            cal.append(_cal_entry(it, _dt(it["sched_at"]), "scheduled"))
        elif st in ("posted", "partial", "failed") and (it.get("posted_at") or it.get("updated_at")):
            cal.append(_cal_entry(it, _dt(it.get("posted_at") or it["updated_at"]), st))
        if st == "review":
            uns.append({"id": it["id"], "title": it["title"], "dur": f"{int(it['dur'] // 60)}:{int(it['dur'] % 60):02d}"})

    st = load_settings()
    tdays = token_days()
    tok_warn = tdays is not None and tdays < 7
    lc = runtime["last_check"]
    nxt = runtime["next_check"]
    if st["paused"]:
        pipe = {"state": "paused", "sub": f"Since {_time(_dt(st['paused_at']))}. No checks, nothing posts." if st.get("paused_at") else "No checks, nothing posts."}
    elif runtime["last_check_err"]:
        pipe = {"state": "problem", "label": "Problem", "sub": "Album unreachable: " + runtime["last_check_err"][:60]}
    elif tok_warn:
        pipe = {"state": "problem", "sub": f"Instagram token expires in {tdays} days"}
    else:
        mins = max(0, int((nxt - now).total_seconds() // 60)) if nxt else 0
        pipe = {"state": "running", "sub": f"Checked {ago(lc)} · next in {mins} min" if lc else "Starting…"}

    g = runtime["gemini"]
    health = [
        {"k": "Last album check", "v": ago(lc) if lc else "Not yet",
         "sub": (f"{_time(lc)} · {runtime['last_check_new']} new video{'s' if runtime['last_check_new'] != 1 else ''}"
                 if lc else "—") if not runtime["last_check_err"] else "Error: " + runtime["last_check_err"][:40],
         "dot": "var(--danger)" if runtime["last_check_err"] else "var(--success)"},
        {"k": "Next check", "v": "Paused" if st["paused"] else f"In {max(0, int((nxt - now).total_seconds() // 60))} min",
         "sub": "Every " + st["interval"], "dot": "var(--muted)" if st["paused"] else "var(--success)"},
        {"k": "Instagram", "v": f"Token expires in {tdays} days" if tok_warn else "Connected", "sub": HANDLE,
         "dot": "var(--warning)" if tok_warn else "var(--success)",
         "vfg": "var(--warning-text)" if tok_warn else "var(--text)"},
        {"k": "Gemini", "v": "Not used yet" if not g else "Available" if g["ok"] else "Last call failed",
         "sub": (g or {}).get("model") or GEMINI_MODEL,
         "dot": "var(--muted)" if not g else "var(--success)" if g["ok"] else "var(--warning)"},
    ]
    models = []
    for i, m in enumerate([GEMINI_MODEL, *GEMINI_FALLBACK_MODELS]):
        used = g and g.get("model") == m
        models.append({"n": m, "r": "Primary" if i == 0 else f"Fallback {i}",
                       "dot": "var(--success)" if used and g["ok"] else "var(--muted)",
                       "st": f"Last used · {g['secs']:.0f} s" if used and g.get("secs") else "Standby"})
    events = [[_time(_dt(e["ts"])), e["text"],
               (store.get(e["item_id"]) or {}).get("title") or "" if e["item_id"] else "", e["kind"]]
              for e in store.events(limit=12)]
    log_lines = []
    if LOG_FILE.exists():
        for line in LOG_FILE.read_text(errors="replace").splitlines()[-300:]:
            p = line.split("\t", 3)
            if len(p) == 4:
                log_lines.append([p[0], p[1].ljust(5), p[2].ljust(7), p[3]])
    lcs = f"{_time(lc)} · {runtime['last_check_new']} new video{'s' if runtime['last_check_new'] != 1 else ''} found" if lc else "Not checked yet"
    return {"items": queue, "hist": hist, "cal": cal, "uns": uns, "events": events, "logLines": log_lines,
            "pipe": pipe, "health": health, "tokenDays": tdays if tdays is not None else 60,
            "tokenKnown": tdays is not None, "handle": HANDLE, "model": GEMINI_MODEL,
            "settings": {k: st[k] for k in ("set", "radius", "hashN", "interval", "vstyle")},
            "lastCheck": lcs, "models": models,
            "keys": [{"n": "Gemini API key", "v": mask(os.environ.get("GEMINI_API_KEY", ""))},
                     {"n": "Instagram access token", "v": mask(os.environ.get("IG_ACCESS_TOKEN", ""))},
                     {"n": "Google Photos album", "v": "Public link · " + (ALBUM_URL[-12:] if ALBUM_URL else "not set")}],
            "now": now.isoformat(timespec="minutes")}


# ---------------------------------------------------------------------- API --

app = FastAPI(title="Postline")


def _need(item_id: str) -> dict:
    it = store.get(item_id)
    if not it:
        raise HTTPException(404, "No such video")
    return it


EDITABLE = ("caption", "tags", "loc", "home", "reel", "story")


@app.get("/api/state")
def api_state():
    return build_state()


@app.patch("/api/items/{item_id}")
def api_edit(item_id: str, body: dict = Body(...)):
    _need(item_id)
    store.update(item_id, **{k: v for k, v in body.items() if k in EDITABLE})
    return {"ok": True}


@app.post("/api/items/{item_id}/approve")
def api_approve(item_id: str, body: dict = Body(default={})):
    it = _need(item_id)
    if it["status"] not in ("review", "scheduled") or it["cap"] == "generating":
        raise HTTPException(409, "This video isn't ready to post")
    store.update(item_id, **{k: v for k, v in body.items() if k in EDITABLE})
    it = store.get(item_id)
    if not it["reel"] and not it["story"]:
        raise HTTPException(400, "Pick at least one destination first")
    store.update(item_id, status="posting", step=0, sched_at=None)
    store.add_event(item_id, "Approved", "accent")
    post_pool.submit(post_item, item_id)
    return {"ok": True}


@app.post("/api/items/{item_id}/reject")
def api_reject(item_id: str):
    _need(item_id)
    store.update(item_id, status="rejected", d_reel="off", d_story="off")
    store.add_event(item_id, "Rejected", "warn")
    return {"ok": True}


@app.post("/api/items/{item_id}/restore")
def api_restore(item_id: str):
    it = _need(item_id)
    store.update(item_id, status="review", d_reel="pending", d_story="pending",
                 reel=bool(it["reel"]), story=bool(it["story"]))
    store.add_event(item_id, "Restored to Review queue", "info")
    return {"ok": True}


@app.post("/api/items/{item_id}/schedule")
def api_schedule(item_id: str, body: dict = Body(...)):
    it = _need(item_id)
    at = _dt(body.get("at"))
    if not at:
        raise HTTPException(400, "Bad date")
    if at < datetime.now() - timedelta(minutes=1):
        raise HTTPException(400, "That time has already passed")
    store.update(item_id, **{k: v for k, v in body.items() if k in EDITABLE})
    store.update(item_id, status="scheduled", sched_at=at.isoformat(timespec="minutes"))
    store.add_event(item_id, f"Scheduled for {day_label(at)}, {_time(at)}", "info")
    return {"ok": True}


@app.post("/api/items/{item_id}/unschedule")
def api_unschedule(item_id: str):
    _need(item_id)
    store.update(item_id, status="review", sched_at=None)
    store.add_event(item_id, "Moved back to Needs review", "info")
    return {"ok": True}


@app.post("/api/items/{item_id}/regenerate")
def api_regen(item_id: str, body: dict = Body(default={})):
    it = _need(item_id)
    if not it.get("file"):
        raise HTTPException(409, "Video file isn't downloaded yet")
    store.update(item_id, cap="generating")
    cap_pool.submit(write_caption, item_id, (body.get("instruction") or "").strip() or None)
    return {"ok": True}


@app.post("/api/items/{item_id}/retry")
def api_retry(item_id: str, body: dict = Body(default={})):
    it = _need(item_id)
    part = body.get("part", "all")
    fields = {}
    if part in ("reel", "all") and it["d_reel"] == "fail":
        fields["d_reel"] = "pending"
    if part in ("story", "all") and it["d_story"] == "fail":
        fields["d_story"] = "pending"
    store.update(item_id, status="posting", step=0, **fields)
    store.add_event(item_id, f"Retry ({part})", "accent")
    post_pool.submit(post_item, item_id)
    return {"ok": True}


@app.post("/api/upload")
def api_upload(file: UploadFile = File(...), dur: float = Form(0), w: int = Form(0), h: int = Form(0)):
    item_id = f"m{int(time.time() * 1000)}"
    ext = Path(file.filename or "video.mov").suffix.lower() or ".mov"
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    dest = VIDEO_DIR / f"{item_id}{ext}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    s = load_settings()["set"]
    store.insert({"id": item_id, "source": "upload", "title": Path(file.filename or "Upload").stem,
                  "file": str(dest), "dur": dur or ig_post.video_duration_s(dest) or 0, "w": w, "h": h,
                  "size_bytes": dest.stat().st_size, "recorded_at": store.now_iso(),
                  "status": "detected", "cap": "generating", "reel": s["defReel"], "story": s["defStory"]})
    store.add_event(item_id, "Uploaded from Mac", "info")
    cap_pool.submit(write_caption, item_id)
    return {"ok": True, "id": item_id}


@app.post("/api/pipeline/{action}")
def api_pipeline(action: str):
    if action == "pause":
        save_settings({"paused": True, "paused_at": store.now_iso()})
        store.add_event(None, "Pipeline paused", "warn")
        notify("nPaused", "⏸ Postline paused", "No checks, nothing posts")
    elif action == "resume":
        save_settings({"paused": False, "paused_at": None})
        runtime["next_check"] = datetime.now()
        store.add_event(None, "Pipeline resumed", "info")
    elif action == "check":
        n = check_album()
        runtime["next_check"] = datetime.now() + timedelta(seconds=INTERVALS.get(load_settings()["interval"], 300))
        return {"ok": True, "new": n, "err": runtime["last_check_err"]}
    else:
        raise HTTPException(404)
    return {"ok": True}


@app.put("/api/settings")
def api_settings(body: dict = Body(...)):
    st = save_settings(body)
    if "interval" in body:
        runtime["next_check"] = datetime.now() + timedelta(seconds=INTERVALS.get(st["interval"], 300))
    return {"ok": True}


@app.post("/api/settings/test-caption")
def api_test_caption():
    vids = [i for i in store.all_items() if i.get("file") and Path(i["file"]).exists()]
    if not vids:
        raise HTTPException(409, "No downloaded video to test on yet")
    st = load_settings()
    r = generate_caption(Path(vids[0]["file"]), style=st["vstyle"], n_tags=int(st["hashN"]),
                         auto_city=st["set"]["autoCity"], home_radius_km=float(st["radius"]))
    return {"caption": r["caption"] + "\n\n" + " ".join(r["hashtags"])}


@app.get("/media/{item_id}")
def media(item_id: str):
    it = _need(item_id)
    if not it.get("file") or not Path(it["file"]).exists():
        raise HTTPException(404)
    return FileResponse(it["file"])


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(UI_DIR / "Postline.dc.html", media_type="text/html")


@app.get("/support.js")
def support_js():
    return FileResponse(UI_DIR / "support.js", media_type="text/javascript")


def recover_interrupted() -> None:
    """Server stopped mid-job last time. Never auto-repost (it may have gone out):
    mark interrupted posts Failed so the creator checks Instagram and retries.
    Interrupted caption jobs are simply re-run."""
    for it in store.all_items():
        if it["status"] == "posting":
            fields = {"status": "failed" if not any(d == "ok" for d in (it["d_reel"], it["d_story"])) else "partial",
                      "err_t": "Interrupted", "err_x": "Postline stopped while posting. Check Instagram; "
                      "if it isn't there, press Retry."}
            for k in ("d_reel", "d_story"):
                if it[k] in ("posting", "pending") and it[k[2:]]:
                    fields[k] = "fail"
            store.update(it["id"], **fields)
            store.add_event(it["id"], "Posting interrupted by a restart", "e")
            log(f"recovered interrupted post {it['id'][:20]}… -> {fields['status']}")
        elif it["cap"] == "generating" and it.get("file"):
            cap_pool.submit(write_caption, it["id"])


def main() -> None:
    sys.stdout = _LogTee(sys.stdout)
    store.conn()
    log(f"Postline starting on http://localhost:{PORT}")
    recover_interrupted()
    threading.Thread(target=worker_loop, daemon=True, name="worker").start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
