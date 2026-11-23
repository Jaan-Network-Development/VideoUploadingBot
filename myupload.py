"""myupload.us integration.

Two independent parts:
  * uploading (run by myupload_worker.py, separate from the sxyprn posting): upload_pending()
    sends every downloaded video to myupload and records it in myupload_index.json;
  * description_for(): the sxyprn post description = normal description + the channel's 5 most
    viewed myupload videos (fetched live from the API, never this video's own link).

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


def _actress_key(video: Path, source=None) -> str:
    """Who the 'related' links are grouped by: the actress, or the channel for xhaccess."""
    meta = common._meta(video)
    if (source or config.SOURCE) == "xhaccess":
        name = meta.get("channel") or video.parent.name
        return re.sub(r"[^0-9a-z]+", "", str(name).lower())
    names = meta.get("actresses") or meta.get("pornstars") or []
    name = meta.get("model") or (names[0] if names else "") or video.parent.name
    return re.sub(r"[^0-9a-z]+", "", str(name).lower())


def _entry_key(video: Path, source=None) -> str:
    return f"{source or config.SOURCE}:{common.video_key(video)}"


def _norm(text) -> str:
    return re.sub(r"[^0-9a-z]+", "", str(text or "").lower())


_ALL_CACHE = {"t": 0.0, "items": []}


def _all_videos(max_age=300) -> list:
    """Every ready video on the account (id, title, views, url), cached for a few minutes."""
    if _ALL_CACHE["items"] and time.time() - _ALL_CACHE["t"] < max_age:
        return _ALL_CACHE["items"]
    items, page = [], 1
    with _client() as c:
        while page <= 200:
            r = c.get("/videos", params={"page": page, "pageSize": 100})
            r.raise_for_status()
            batch = r.json().get("items", [])
            items += [it for it in batch if it.get("status") == "ready" and it.get("url")]
            if len(batch) < 100:
                break
            page += 1
    _ALL_CACHE.update(t=time.time(), items=items)
    return items


def top_links(video: Path, n=TOP_N) -> list:
    """The n most viewed myupload videos of this video's channel/actress, live from the API.
    The video's own link is skipped (the next one is taken). Fewer than n -> fewer returned."""
    akey = _actress_key(video)
    if not akey or not api_key():
        return []
    own_id = (_read_index().get(_entry_key(video)) or {}).get("url", "")
    try:
        items = _all_videos()
    except Exception as e:  # noqa: BLE001
        print(f"  myupload: could not list videos ({e!r:.150}); no related links")
        return []
    # channel = the "<channel> - <title>" prefix (same text title_for() gave the upload)
    same = [it for it in items
            if _norm(str(it.get("title") or "").split(" - ", 1)[0]) == akey
            and canon(it["url"]) != own_id]
    same.sort(key=lambda it: (int(it.get("views") or 0), it.get("createdAt", "")), reverse=True)
    return [canon(it["url"]) for it in same[:n]]


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


def is_pending(video: Path, source=None) -> bool:
    """True while this video still has to go to myupload (so its files must be kept)."""
    if not enabled():
        return False
    return _entry_key(video, source) not in _read_index()


def ensure_link(video: Path, source=None):
    """Upload this video to myupload (once) and return its share url. None = not (yet) available."""
    source = source or config.SOURCE
    if not enabled():
        return None
    key = _entry_key(video, source)
    known = _read_index().get(key)
    if known:
        return canon(known["url"]) if known.get("url") else None
    meta = common._meta(video)
    src = meta.get("download_url")
    entry = {"title": common.title_for(video), "actress_key": _actress_key(video, source),
             "source": source, "views": meta.get("views") or 0,
             "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    if not src and source != "xhaccess":
        print("  myupload: no download_url in the .json; skipping this video for good")
        _add_index(key, {**entry, "skipped": "no download_url"})
        return None
    try:
        if src:
            print("  myupload: uploading by URL ...")
            item = upload_by_url(src, key)
        else:   # xhaccess: no public file URL, send the local file
            print(f"  myupload: sending the file ({video.stat().st_size / 1e6:.0f} MB) ...")
            item = upload_file(video, key)
    except Exception as e:  # noqa: BLE001
        print(f"  myupload failed ({e!r:.200}); will retry on the next pass")
        if "over the 1 GiB limit" in str(e):
            _add_index(key, {**entry, "skipped": "too big"})
        return None
    _add_index(key, {**entry, "url": canon(item["url"])})
    _ALL_CACHE["t"] = 0.0
    print(f"  myupload: {canon(item['url'])}")
    return canon(item["url"])


def upload_pending() -> int:
    """Upload every downloaded video (all sources) that is not on myupload yet, then delete the
    files of those the sxyprn worker has already posted. Returns how many were uploaded."""
    if not enabled():
        return 0
    n = 0
    for source, cfg in config.SOURCES.items():
        root = cfg["videos_dir"]
        if not root.is_dir():
            continue
        dirs = [root] + sorted(p for p in root.iterdir() if p.is_dir())
        for d in dirs:
            for video in common.queue(d, include_uploaded=True):
                key = _entry_key(video, source)
                if key not in _read_index():
                    print(f"[myupload:{source}] {video.name}")
                    if ensure_link(video, source):
                        n += 1
                if key in _read_index() and common.is_uploaded(video):
                    common.finish(video, ok=True)   # both sites done: now the files can go
    return n


# ---------------------------------------------------------------- description
def description_for(video: Path, limit=config.MAX_DESCRIPTION) -> str:
    """Normal description + the channel's 5 most viewed myupload videos (not this video's own link)."""
    base = common.description_for(video, limit=None)
    more = top_links(video)
    if not more:
        return common._fit(base, limit)
    label = "More from her" if config.SOURCE == "hqporner" else "More from this channel"
    extra = f"\n{label}:\n" + "\n".join(more)
    room = (limit or 10**6) - len(extra)
    return common._fit(base, max(room, 0)) + extra
