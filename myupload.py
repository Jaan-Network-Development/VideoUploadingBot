"""Upload a folder's videos to myupload.us, then write 5 random links into each .txt.

After the links are written it continues straight into upload_manual (the site upload).
Usage: python myupload.py [folder | --pick]  (no argument: every folder in turn)   (key in .env as MYUPLOAD_API_KEY; close all Chrome windows first)
Links are cached in <folder>/myupload_links.json so a re-run never uploads twice.
"""
import hashlib
import json
import os
import random
import time
from pathlib import Path

import httpx

import browser
import common
import config

API = "https://myupload.us/api/v1"
LINKS_PER_TXT = 5
CACHE_NAME = "myupload_links.json"
READY_WAIT_S = 15 * 60
WATCH_INTERVAL_S = 5 * 60      # service mode: pause between scans for new downloads
STOPPED_RETRY_S = 60 * 60      # service mode: pause after the site refused (limit / not approved)


def api_key():
    browser.load_env()
    key = os.environ.get("MYUPLOAD_API_KEY", "").strip()
    if not key:
        raise SystemExit("Set MYUPLOAD_API_KEY in .env")
    return key


def _find(obj, key):
    """First value stored under `key` anywhere in a nested JSON response."""
    if isinstance(obj, dict):
        if isinstance(obj.get(key), str):
            return obj[key]
        for v in obj.values():
            r = _find(v, key)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find(v, key)
            if r:
                return r
    return None


def upload_video(client, video: Path, salt=""):
    """POST the file; returns {'id', 'url'}. Idempotency-Key makes retries safe."""
    st = video.stat()
    idem = hashlib.sha1(f"{video.name}|{st.st_size}|{st.st_mtime_ns}|{salt}".encode()).hexdigest()
    while True:
        with video.open("rb") as f:
            r = client.post(
                f"{API}/videos",
                headers={"Idempotency-Key": idem},
                data={"title": common.title_for(video)},   # text fields go before the file
                files={"file": (video.name, f)},
            )
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", "60"))
            print(f"  rate limited, waiting {wait}s")
            time.sleep(wait)
            continue
        if r.status_code != 202 and not r.is_success:
            raise RuntimeError(f"{r.status_code} {r.text[:300]}")
        data = r.json()
        url, fid = _find(data, "url"), _find(data, "id")
        if not url:
            raise RuntimeError(f"no url in response: {str(data)[:300]}")
        return {"id": fid, "url": url}


def wait_ready(client, items):
    """Poll until every item is ready/failed (best effort; links work regardless)."""
    pending = {n: i for n, i in items.items() if i.get("id")}
    end = time.time() + READY_WAIT_S
    while pending and time.time() < end:
        for name, item in list(pending.items()):
            try:
                r = client.get(f"{API}/videos/{item['id']}")
                status = "failed" if r.status_code == 404 else _find(r.json(), "status")
            except Exception:  # noqa: BLE001
                continue
            if status in ("ready", "failed"):
                print(f"  {name}: {status}")
                pending.pop(name)
        if pending:
            time.sleep(10)
    if pending:
        print(f"  still processing after {READY_WAIT_S // 60} min: {', '.join(pending)}")


def upload_folder(folder: Path):
    """Upload every video in folder (skipping cached); returns {video name: {'id','url'}}."""
    cache_file = folder / CACHE_NAME
    try:
        items = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        items = {}
    videos = common.queue(folder)
    with httpx.Client(headers={"Authorization": f"Bearer {api_key()}"},
                      timeout=httpx.Timeout(60, read=config.UPLOAD_TIMEOUT)) as client:
        fresh = {}
        for v in videos:
            salt = ""
            if v.name in items:
                # a cached link is only reusable if the video still exists on myupload
                alive = client.get(f"{API}/videos/{items[v.name]['id']}").status_code == 200
                if alive:
                    print(f"{v.name}: already uploaded")
                    continue
                print(f"{v.name}: removed from myupload, uploading again")
                salt = str(time.time())   # new Idempotency-Key, else the deleted result is returned
            print(f"Uploading {v.name} to myupload ...")
            items[v.name] = fresh[v.name] = upload_video(client, v, salt)
            cache_file.write_text(json.dumps(items, indent=2), encoding="utf-8")
            print(f"  {items[v.name]['url']}")
        if fresh:
            wait_ready(client, fresh)
    return {v.name: items[v.name] for v in videos}


def write_links(folder: Path, items):
    """Append 5 random links (never the video's own) to each video's .txt."""
    for video in common.queue(folder):
        pool = [i["url"] for n, i in items.items() if n != video.name]
        links = random.sample(pool, min(LINKS_PER_TXT, len(pool)))
        base = common.description_for(video, limit=None)
        # drop links added by an earlier run, so re-running doesn't stack them
        base = "\n".join(l for l in base.splitlines() if l.strip() not in {i["url"] for i in items.values()}).strip()
        tail = "\n".join(links)
        room = config.MAX_DESCRIPTION - len(tail) - 1
        base = common._fit(base, max(room, 0))
        text = f"{base}\n{tail}" if base else tail
        video.with_suffix(".txt").write_text(text, encoding="utf-8")
        print(f"{video.name}: wrote {len(links)} links")


def pick_folder() -> Path:
    subs = common.folders()
    if subs == [config.VIDEOS_DIR]:
        return config.VIDEOS_DIR
    for n, p in enumerate(subs, 1):
        print(f"{n}. {p.name} ({len(common.queue(p))} videos)")
    while True:
        a = input("Select folder number: ").strip()
        if a.isdigit() and 1 <= int(a) <= len(subs):
            return subs[int(a) - 1]


def prepare(folder: Path):
    items = upload_folder(folder)
    if items:
        write_links(folder, items)


def main(folder: Path = None, watch=False):
    """Upload to myupload, write the links, then post to the site: one folder, or all in turn.

    watch: keep running, rescanning for new downloads (what the service does).
    """
    import upload_manual   # imported here: it pulls in playwright
    upload_manual.open_chrome()
    if folder:
        prepare(folder)
        upload_manual.process_queue(folder)
        return
    while True:
        finished = upload_manual.process_all(prepare)
        if not watch:
            return
        # after a clean pass wait for new downloads; after a stop (limit / not approved) wait longer
        wait = WATCH_INTERVAL_S if finished else STOPPED_RETRY_S
        print(f"Sleeping {wait // 60} min before checking for new videos ...", flush=True)
        time.sleep(wait)


if __name__ == "__main__":
    import sys
    # no argument: every folder in turn; --pick: choose one; --watch: all folders, forever
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if arg == "--pick":
        main(pick_folder())
    else:
        main(Path(arg) if arg and arg != "--watch" else None, watch=arg == "--watch")
