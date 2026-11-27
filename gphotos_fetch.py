"""
List and download media from a PUBLIC Google Photos shared-album link.

This is unofficial: the Google Photos API can no longer read albums it didn't
create, so we read the public album web page instead. Each item on the page
has a stable media key ("AF1Qip...") and a base URL on lh3.googleusercontent.com.
Appending "=dv" to a base URL downloads the item as a video; for photos that
request doesn't return a video, which is how we tell them apart.

Limitation: only the items embedded in the first page load are seen (roughly
the first few hundred). Keep the "To Post" album small and that's never an issue.

Usage:
    python gphotos_fetch.py "<album link>"                 # list items
    python gphotos_fetch.py "<album link>" --download DIR  # download all videos
"""
import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

import requests

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

# ["AF1Qip<key>",["https://lh3.googleusercontent.com/pw/<...>",<width>,<height>
_ITEM_RE = re.compile(
    r'\["(AF1Qip[A-Za-z0-9_-]+)",\["(https://lh3\.googleusercontent\.com/pw/[^"]+)",(\d+),(\d+)'
)
_ALBUM_KEY_RE = re.compile(r"/share/(AF1Qip[A-Za-z0-9_-]+)")


class GPhotosError(Exception):
    pass


@dataclass
class AlbumItem:
    media_key: str
    base_url: str
    width: int
    height: int


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def resolve_album_url(album_url: str) -> str:
    """Turn a photos.app.goo.gl short link into the full photos.google.com/share/... URL.

    For browser user agents the short link now serves a JavaScript "deep link"
    page instead of redirecting, so resolve it with a plain non-browser request.
    """
    if "photos.app.goo.gl" not in album_url:
        return album_url
    resp = requests.get(album_url, headers={"User-Agent": "python-requests"},
                        allow_redirects=False, timeout=30)
    target = resp.headers.get("Location", "")
    if resp.status_code not in (301, 302, 303, 307, 308) or "photos.google.com" not in target:
        raise GPhotosError(f"Couldn't resolve short link (HTTP {resp.status_code}) - "
                           "is it a public shared album link?")
    return target


def list_items(album_url: str, session: requests.Session | None = None) -> list[AlbumItem]:
    """Return every item (photo or video) on the shared album page, in page order."""
    s = session or _session()
    resp = s.get(resolve_album_url(album_url), timeout=30)
    if resp.status_code != 200:
        raise GPhotosError(f"Album page returned HTTP {resp.status_code}")

    m = _ALBUM_KEY_RE.search(resp.url)
    album_key = m.group(1) if m else None

    items: dict[str, AlbumItem] = {}
    for key, url, w, h in _ITEM_RE.findall(resp.text):
        if key == album_key or key in items:
            continue
        # URLs in the page's embedded JSON may contain \u escapes
        url = json.loads(f'"{url}"')
        items[key] = AlbumItem(key, url, int(w), int(h))

    if not items and "AF1Qip" not in resp.text:
        raise GPhotosError("No media found - is the link a public shared album?")
    return list(items.values())


def download_video(item: AlbumItem, dest_dir: Path,
                   session: requests.Session | None = None) -> Path | None:
    """Download `item` as a video into dest_dir. Returns None if it isn't a video."""
    s = session or _session()
    dest_dir.mkdir(parents=True, exist_ok=True)
    with s.get(f"{item.base_url}=dv", stream=True, timeout=60) as resp:
        ctype = resp.headers.get("Content-Type", "")
        if resp.status_code != 200 or not ctype.startswith("video/"):
            return None
        ext = ".mov" if "quicktime" in ctype else ".mp4"
        out = dest_dir / f"{item.media_key}{ext}"
        tmp = out.with_suffix(out.suffix + ".part")
        with tmp.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)
        tmp.rename(out)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="List/download a public Google Photos album.")
    parser.add_argument("album_url")
    parser.add_argument("--download", type=Path, metavar="DIR",
                        help="Download every video in the album into DIR")
    args = parser.parse_args()

    s = _session()
    items = list_items(args.album_url, s)
    print(f"[gphotos] {len(items)} item(s) in album")
    for it in items:
        print(f"  {it.media_key[:24]}...  {it.width}x{it.height}")
        if args.download:
            path = download_video(it, args.download, s)
            if path:
                print(f"    -> video saved: {path} ({path.stat().st_size / 1_000_000:.1f} MB)")
            else:
                print("    -> not a video, skipped")


if __name__ == "__main__":
    main()
