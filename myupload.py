"""myupload.us integration: upload a video by URL (the site fetches it itself, nothing is sent
from this machine), then put the resulting share link into the post description.

Needs MYUPLOAD_API_KEY in .env (Bearer key with 'Upload videos' + 'Read and list' permissions).

POST /api/v1/videos/remote answers 202 with no item, so the new item is found by diffing
GET /videos before/after (under a lock shared by both workers). Every link we create is kept in
myupload_index.json, which is also where "the 5 latest videos of this actress" comes from.

xhaccess has no public file URL (its HLS links are signed for the downloader's IP, so myupload's
servers get a 403), so for that source the local .mp4 is sent over tus instead
(upload_file). The related links there are the channel's 5 most viewed videos already on myupload.
"""
import base64
import json
import os
import re
import time
from pathlib import Path

import httpx

import browser
import common
import config

API = "https://myupload.us/api/v1"
INDEX_FILE = config.ROOT / "myupload_index.json"
LOCK_FILE = config.ROOT / "myupload.lock"
READY_TIMEOUT_S = 15 * 60
APPEAR_TIMEOUT_S = 3 * 60
TOP_N = 5
MAX_DIRECT_BYTES = 1 << 30   # myupload file size limit
TUS_URL = "https://myupload.us/files/"
TUS_CHUNK = 50 * 1024 * 1024   # Cloudflare rejects request bodies over ~100 MB
CANONICAL_HOST = "myupload.us"   # the API may hand out other domains (e.g. thedatingguide.site)


def canon(url: str) -> str:
    """Rewrite any share link's host to myupload.us (path, e.g. /v/<id>, is kept)."""
    return re.sub(r"^(https?://)[^/]+", r"\1" + CANONICAL_HOST, url or "")


def api_key() -> str:
    browser.load_env()   # .env (MYUPLOAD_API_KEY); no-op if already in the environment
    return os.environ.get("MYUPLOAD_API_KEY", "").strip()


def enabled() -> bool:
    return config.MYUPLOAD_ENABLED and bool(api_key())


def _client() -> httpx.Client:
    return httpx.Client(base_url=API, headers={"Authorization": f"Bearer {api_key()}"}, timeout=60)


def _lock(name=LOCK_FILE, timeout=240):
    """Tiny cross-process lock (O_EXCL file); stale after 5 min."""
    class _L:
        def __enter__(self):
            end = time.time() + timeout
            while True:
                try:
                    os.close(os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                    return self
                except (FileExistsError, PermissionError):
                    try:
                        if time.time() - name.stat().st_mtime > 300:
                            name.unlink(missing_ok=True)
                            continue
                    except OSError:
                        pass
                    if time.time() > end:
                        return self
                    time.sleep(0.3)

        def __exit__(self, *a):
            Path(name).unlink(missing_ok=True)
    return _L()


# ---------------------------------------------------------------- index of our own links
def _read_index() -> dict:
    try:
        data = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        for v in data.values():
            if isinstance(v, dict) and v.get("url"):
                v["url"] = canon(v["url"])
        return data
    except (OSError, ValueError):
        return {}


def _add_index(key, entry):
    with _lock(INDEX_FILE.with_suffix(".lock")):
        data = _read_index()
        data[key] = entry
        tmp = INDEX_FILE.with_name(f"{INDEX_FILE.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(INDEX_FILE)


def _actress_key(video: Path) -> str:
    """Who the 'related' links are grouped by: the actress, or the channel for xhaccess."""
    meta = common._meta(video)
    if config.SOURCE == "xhaccess":
        name = meta.get("channel") or video.parent.name
        return re.sub(r"[^0-9a-z]+", "", str(name).lower())
    names = meta.get("actresses") or meta.get("pornstars") or []
    name = meta.get("model") or (names[0] if names else "") or video.parent.name
    return re.sub(r"[^0-9a-z]+", "", str(name).lower())


def _entry_key(video: Path) -> str:
    return f"{config.SOURCE}:{common.video_key(video)}"


def _views(entry) -> int:
    try:
        return int(entry.get("views") or 0)
    except (TypeError, ValueError):
        return 0


def top_links(video: Path, n=TOP_N) -> list:
    """Related myupload links (never this video): the latest n of the same actress, or for
    xhaccess the n most viewed of the same channel (videos with 0 / unknown views fill the
    list when fewer than n have views, so it is never left short while anything exists)."""
    me, akey = _entry_key(video), _actress_key(video)
    if not akey:
        return []
    items = [(k, v) for k, v in _read_index().items()
             if k != me and v.get("actress_key") == akey and v.get("url")]
    if config.SOURCE == "xhaccess":
        items = [(k, v) for k, v in items if v.get("source") == "xhaccess"]
        items.sort(key=lambda kv: (_views(kv[1]), kv[1].get("time", "")), reverse=True)
    else:
        items.sort(key=lambda kv: kv[1].get("time", ""), reverse=True)
    return [v["url"] for _, v in items[:n]]


# ---------------------------------------------------------------- upload by URL
def _list_ids(c, size=50) -> dict:
    r = c.get("/videos", params={"page": 1, "pageSize": size})
    r.raise_for_status()
    return {it["id"]: it for it in r.json().get("items", [])}


def upload_by_url(source_url: str, idem_key: str) -> dict:
    """Queue the remote upload and return the new File item (has 'url', 'status')."""
    with _client() as c:
        with _lock():
            before = _list_ids(c)
            r = c.post("/videos/remote", json={"urls": [source_url]},
                       headers={"Idempotency-Key": re.sub(r"[^A-Za-z0-9_-]", "_", idem_key)[:100]})
            if r.status_code not in (200, 202):
                raise RuntimeError(f"myupload: HTTP {r.status_code} {r.text[:200]}")
            item, end = None, time.time() + APPEAR_TIMEOUT_S
            while item is None and time.time() < end:
                time.sleep(4)
                new = [v for i, v in _list_ids(c).items() if i not in before]
                if new:
                    item = new[0]
            if item is None:
                raise RuntimeError("myupload: queued but the item never showed up in the list")
        return _wait_ready(c, item)


def _wait_ready(c, item: dict) -> dict:
    """Poll the item until myupload has processed it."""
    end = time.time() + READY_TIMEOUT_S
    while time.time() < end:
        if item.get("status") == "ready":
            return item
        if item.get("status") in ("failed", "disabled"):
            why = item.get("disabledReason") or "source url refused?"
            raise RuntimeError(f"myupload: upload {item.get('status')} ({why})")
        time.sleep(10)
        r = c.get(f"/videos/{item['id']}")   # NOT /files/{id}: that 404s for videos
        if r.status_code == 200:
            j = r.json()
            item = j.get("file", j)
    print("  myupload: still processing after 15 min; using the link anyway")
    return item


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode()


def upload_file(video: Path, idem_key: str) -> dict:
    """Send the local file itself when there is no public URL.

    POST /videos is capped by Cloudflare at ~100 MB per request, so the file goes over the tus
    resumable protocol in 50 MB chunks. Metadata kind=video makes it a video item (/v/<id>
    player link); without it tus creates a plain /d/ download. tus returns no item, so the new
    one is found in GET /videos by its original file name (not under the lock: it can take minutes)."""
    size = video.stat().st_size
    if size > MAX_DIRECT_BYTES:
        raise RuntimeError(f"myupload: {video.name} is {size / 2**30:.2f} GiB, over the 1 GiB limit")
    hdr = {"Authorization": f"Bearer {api_key()}", "Tus-Resumable": "1.0.0"}
    meta = ",".join(f"{k} {_b64(v)}" for k, v in (
        ("filename", video.name), ("filetype", "video/mp4"), ("kind", "video"),
        ("title", common.title_for(video))))
    with _client() as api, httpx.Client(timeout=httpx.Timeout(60, write=10 * 60)) as up:
        before = _list_ids(api)
        r = up.post(TUS_URL, headers={**hdr, "Upload-Length": str(size), "Upload-Metadata": meta})
        if r.status_code != 201 or not r.headers.get("location"):
            raise RuntimeError(f"myupload: tus create HTTP {r.status_code} {r.text[:200]}")
        loc, offset = r.headers["location"], 0
        with open(video, "rb") as f:
            while offset < size:
                chunk = f.read(TUS_CHUNK)
                for attempt in range(1, 4):
                    p = up.patch(loc, headers={**hdr, "Upload-Offset": str(offset),
                                               "Content-Type": "application/offset+octet-stream"},
                                 content=chunk)
                    if p.status_code == 204:
                        break
                    if attempt == 3:
                        raise RuntimeError(f"myupload: tus upload HTTP {p.status_code} {p.text[:200]}")
                    time.sleep(5 * attempt)
                offset = int(p.headers["upload-offset"])
        item, end = None, time.time() + APPEAR_TIMEOUT_S
        while item is None and time.time() < end:
            time.sleep(4)
            new = [v for i, v in _list_ids(api).items()
                   if i not in before and v.get("originalName") == video.name]
            if new:
                item = max(new, key=lambda v: v.get("createdAt", ""))
        if item is None:
            raise RuntimeError("myupload: uploaded but the item never showed up in the list")
        return _wait_ready(api, item)


def ensure_link(video: Path):
    """Myupload share url for this video (uploads it by URL the first time). None = unavailable."""
    if not enabled():
        return None
    key = _entry_key(video)
    known = _read_index().get(key)
    if known and known.get("url"):
        return canon(known["url"])
    meta = common._meta(video)
    src = meta.get("download_url")
    if not src and config.SOURCE != "xhaccess":
        print("  myupload: no download_url in the .json; skipping")
        return None
    try:
        if src:
            print("  myupload: uploading by URL ...")
            item = upload_by_url(src, key)
        else:   # xhaccess: no public file URL, send the local file
            print(f"  myupload: sending the file ({video.stat().st_size / 1e6:.0f} MB) ...")
            item = upload_file(video, key)
    except Exception as e:  # noqa: BLE001
        print(f"  myupload failed ({e!r:.200}); posting without a myupload link")
        return None
    _add_index(key, {"url": canon(item["url"]), "title": common.title_for(video),
                     "actress_key": _actress_key(video), "source": config.SOURCE,
                     "views": meta.get("views") or 0,
                     "time": time.strftime("%Y-%m-%d %H:%M:%S")})
    print(f"  myupload: {canon(item['url'])}")
    return canon(item["url"])


# ---------------------------------------------------------------- description
def description_for(video: Path, limit=config.MAX_DESCRIPTION) -> str:
    """Normal description + the myupload link (+ latest 5 of the actress for hqporner,
    + the channel's 5 most viewed for xhaccess)."""
    base = common.description_for(video, limit=None)
    url = ensure_link(video)
    if not url:
        return common._fit(base, limit)
    extra = f"\nFull video: {url}"
    if config.SOURCE in ("hqporner", "xhaccess"):
        more = top_links(video)
        if more:
            label = "More from this channel" if config.SOURCE == "xhaccess" else "More from her"
            extra += f"\n{label}:\n" + "\n".join(more)
    room = (limit or 10**6) - len(extra)
    return common._fit(base, max(room, 0)) + extra
