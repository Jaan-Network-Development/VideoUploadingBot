"""Find and remove broken xhaccess uploads (the ones that don't play) from myupload and the history.

  python myupload_cleanup.py scan              PC with Chrome: list them in broken_videos.json
                                               (changes nothing)
  python myupload_cleanup.py delete            same PC: delete that list from myupload and from
                                               this PC's history (asks first)
  python myupload_cleanup.py forget            any other machine (VPS): copy broken_videos.json
                                               there, removes those entries from its history only

Broken = failed on myupload, or the stored file is raw MPEG-TS or a fragmented MP4. Only uploads
named "<xhaccess id>-<title>.mp4" are looked at; anything that can't be checked is left alone.
Stop all upload programs before delete / forget. The history is backed up first.
"""
import json
import re
import shutil
import struct
import sys
import time
from pathlib import Path

import httpx

import myupload

LIST_FILE = Path(__file__).resolve().parent / "broken_videos.json"
NAME_RE = re.compile(r"^\d{5,}-.*\.(mp4|ts)$")


def all_videos() -> list:
    items, page = [], 1
    with myupload._client() as c:
        while True:
            for attempt in range(1, 6):          # the site sometimes answers 502 for a moment
                try:
                    r = c.get("/videos", params={"page": page, "pageSize": 100})
                    r.raise_for_status()
                    break
                except httpx.HTTPError:
                    if attempt == 5:
                        raise
                    time.sleep(5 * attempt)
            batch = r.json().get("items", [])
            items += batch
            if len(batch) < 100:
                return items
            page += 1


def classify(data: bytes) -> str:
    """'ok', 'mpeg-ts', 'fragmented' or 'unknown' from the first bytes of a stored file."""
    if len(data) > 376 and data[0] == data[188] == data[376] == 0x47:
        return "mpeg-ts"
    off, first = 0, True
    while off + 8 <= len(data):
        size, box = struct.unpack(">I4s", data[off:off + 8])
        if first and box != b"ftyp":
            return "unknown"
        first = False
        if box == b"moof":
            return "fragmented"
        if box == b"moov":
            if off + size > len(data):
                return "ok"            # a large index at the front: an ordinary MP4
            return "fragmented" if b"mvex" in data[off:off + size] else "ok"
        if box == b"mdat":
            return "ok"                # ordinary MP4 with the index at the end
        if size < 8:
            return "unknown"
        off += size
    return "unknown"


def stored_head(page, public_id) -> bytes:
    """First 256 KB of the file as the player gets it."""
    with page.expect_request(lambda r: "/stream/" in r.url, timeout=25000) as req:
        page.goto(f"https://myupload.us/embed/{public_id}", wait_until="domcontentloaded", timeout=45000)
        time.sleep(1.5)
        page.mouse.click(640, 360)      # the player asks for the file once play is pressed
    cookies = {c["name"]: c["value"] for c in page.context.cookies()}
    headers = {"User-Agent": page.evaluate("navigator.userAgent"), "Referer": page.url,
               "Range": "bytes=0-262143"}
    with httpx.stream("GET", req.value.url, headers=headers, cookies=cookies, timeout=60) as r:
        r.raise_for_status()
        data = b""
        for piece in r.iter_bytes():
            data += piece
            if len(data) >= 262144:
                break
    return data


def scan():
    from playwright.sync_api import sync_playwright
    import myupload_direct
    items = [i for i in all_videos() if NAME_RE.match(i.get("originalName") or "")]
    print(f"{len(items)} xhaccess uploads to check", flush=True)
    broken, counts = [], {}
    with sync_playwright() as p:
        browser = myupload_direct.open_browser(p)
        for n, it in enumerate(items, 1):
            status = it["status"]
            if status in ("failed", "disabled"):
                verdict = "failed"
            elif status != "ready":
                verdict = "not finished (skipped)"
            else:
                ctx = browser.new_context()
                try:
                    verdict = classify(stored_head(ctx.new_page(), it["publicId"]))
                except Exception as e:  # noqa: BLE001
                    verdict = "unknown"
                    print(f"  could not check {it['publicId']}: {str(e).splitlines()[0][:100]}", flush=True)
                finally:
                    ctx.close()
            counts[verdict] = counts.get(verdict, 0) + 1
            if verdict in ("failed", "mpeg-ts", "fragmented"):
                url = myupload.canon(it["url"])
                broken.append({"id": it["id"], "publicId": it["publicId"], "url": url, "why": verdict,
                               "title": it["title"], "created": it["createdAt"][:16]})
                print(f"{n}/{len(items)} {verdict:10} {url}  {it['title'][:60]}", flush=True)
            elif n % 20 == 0:
                print(f"{n}/{len(items)} checked", flush=True)
        browser.close()
    LIST_FILE.write_text(json.dumps(broken, indent=1, ensure_ascii=False), encoding="utf-8")
    print("\nresult:", ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
    print(f"{len(broken)} broken videos listed in {LIST_FILE.name}. Nothing was changed.")


def load_list() -> list:
    if not LIST_FILE.exists():
        raise SystemExit(f"{LIST_FILE.name} not found: run 'scan' first (or copy it here)")
    return json.loads(LIST_FILE.read_text(encoding="utf-8"))


def forget(public_ids: set):
    """Drop the history entries whose link is one of these videos (a good re-upload stays)."""
    if not myupload.INDEX_FILE.exists():
        print("no history file here")
        return
    backup = myupload.INDEX_FILE.with_name(f"myupload_index.backup-{time.strftime('%Y%m%d-%H%M%S')}.json")
    shutil.copy2(myupload.INDEX_FILE, backup)
    with myupload._lock(myupload.INDEX_FILE.with_suffix(".lock")):
        data = myupload._read_index()
        gone = [k for k, v in data.items() if v.get("url", "").rstrip("/").rsplit("/", 1)[-1] in public_ids]
        for k in gone:
            del data[k]
        tmp = myupload.INDEX_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(myupload.INDEX_FILE)
    print(f"history: removed {len(gone)} entries, {len(data)} left (backup: {backup.name})")


def delete():
    broken = load_list()
    if not broken:
        print("the list is empty")
        return
    for b in broken:
        print(f"{b['why']:10} {b['url']}  {b['title'][:60]}")
    print(f"\n{len(broken)} videos will be PERMANENTLY deleted from myupload. Their links stop working.")
    if input(f"Type DELETE {len(broken)} to continue: ").strip() != f"DELETE {len(broken)}":
        raise SystemExit("cancelled, nothing changed")
    removed = set()
    with myupload._client() as c:
        for b in broken:
            try:
                r = c.delete(f"/videos/{b['id']}")
                if r.status_code in (200, 202, 204, 404):
                    removed.add(b["publicId"])
                else:
                    print(f"  not deleted {b['url']}: HTTP {r.status_code}")
            except httpx.HTTPError as e:
                print(f"  not deleted {b['url']}: {e}")
            time.sleep(0.5)
    print(f"myupload: deleted {len(removed)} of {len(broken)}")
    forget(removed)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "scan":
        scan()
    elif cmd == "delete":
        delete()
    elif cmd == "forget":
        forget({b["publicId"] for b in load_list()})
    else:
        raise SystemExit(__doc__)
