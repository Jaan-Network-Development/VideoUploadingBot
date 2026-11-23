"""Send videos to myupload.us through its API WITHOUT saving them on this PC.

  python myupload_direct.py xhaccess tuktuk-patrol [--max-videos 5] [--quality 720] [--parallel 3]
      xhaccess: only videos with the "full video" badge are sent (--all-videos for every video);
      there is no length limit
  python myupload_direct.py hqporner --actress little-caprice [--max-videos 5]
  python myupload_direct.py hqporner https://hqporner.com/hdporn/80483-some-title.html ...
  python myupload_direct.py veporn --latest 10
  python myupload_direct.py veporn https://veporn.com/<video-slug> ...
  add --dry-run to only list what would be sent, --min-minutes N to skip short videos

How each source gets to myupload:
  hqporner, veporn  the page's direct .mp4 link is handed to myupload (POST /videos/remote);
                    myupload fetches it itself, nothing passes through this PC.
  xhaccess          its stream links are signed for this PC's IP, so myupload cannot fetch them.
                    The HLS segments are read here and pushed straight into a chunked (tus)
                    upload, held in memory 50 MB at a time and never written to disk.

Every link is recorded in myupload_index.json (same index as myupload_worker.py, so nothing is
ever sent twice). Titles are "<channel or actress> - <title>", the form the sxyprn descriptions
use to find a channel's top videos. Needs MYUPLOAD_API_KEY in .env.
"""
import argparse
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin

import httpx

DOWNLOADER = Path(__file__).resolve().parent.parent / "VideoDowloaderBot"
sys.path.insert(0, str(DOWNLOADER))   # reuse the downloaders' page-reading code

import myupload  # noqa: E402

TUS_CHUNK = myupload.TUS_CHUNK
MAX_BYTES = myupload.MAX_DIRECT_BYTES
SHOW_PROGRESS = True


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def title_of(who: str, title: str) -> str:
    who, title = (who or "").strip(), (title or "").strip()
    return f"{who} - {title}" if who and not title.lower().startswith(who.lower()) else title


def already(key: str) -> bool:
    return key in myupload._read_index()


def record(key, source, group, title, item, views=0):
    url = myupload.canon(item["url"])
    myupload._add_index(key, {"url": url, "title": title, "actress_key": myupload._norm(group),
                              "source": source, "views": views or 0,
                              "time": time.strftime("%Y-%m-%d %H:%M:%S")})
    log(f"  -> {url}")


# ---------------------------------------------------------------- streaming tus upload
def tus_stream(chunks, filename: str, title: str, tag: str = "") -> dict:
    """Push an iterator of byte pieces to myupload over tus (length unknown up front)."""
    hdr = {"Authorization": f"Bearer {myupload.api_key()}", "Tus-Resumable": "1.0.0"}
    meta = ",".join(f"{k} {myupload._b64(v)}" for k, v in (
        ("filename", filename), ("filetype", "video/mp4"), ("kind", "video"), ("title", title)))
    with myupload._client() as api, httpx.Client(timeout=httpx.Timeout(60, write=10 * 60)) as up:
        before = myupload._list_ids(api)
        r = up.post(myupload.TUS_URL, headers={**hdr, "Upload-Defer-Length": "1", "Upload-Metadata": meta})
        if r.status_code != 201 or not r.headers.get("location"):
            raise RuntimeError(f"tus create HTTP {r.status_code} {r.text[:200]}")
        loc, offset = r.headers["location"], 0

        def send(data: bytes, final: bool):
            nonlocal offset
            for attempt in range(1, 4):
                h = {**hdr, "Upload-Offset": str(offset), "Content-Type": "application/offset+octet-stream"}
                if final:
                    h["Upload-Length"] = str(offset + len(data))
                p = up.patch(loc, headers=h, content=data)
                if p.status_code == 204:
                    offset = int(p.headers["upload-offset"])
                    return
                head = up.head(loc, headers=hdr)   # did the server get it anyway?
                if head.headers.get("upload-offset") == str(offset + len(data)):
                    offset += len(data)
                    return
                if attempt == 3:
                    raise RuntimeError(f"tus upload HTTP {p.status_code} {p.text[:200]}")
                time.sleep(5 * attempt)

        try:
            buf, shown = bytearray(), time.time()
            for piece in chunks:
                buf += piece
                while len(buf) > TUS_CHUNK:
                    send(bytes(buf[:TUS_CHUNK]), final=False)
                    del buf[:TUS_CHUNK]
                if offset + len(buf) > MAX_BYTES:
                    raise RuntimeError("video is over myupload's 1 GiB file limit")
                if time.time() - shown > (5 if SHOW_PROGRESS else 60):
                    msg = f"sent {(offset + len(buf)) / 1e6:.0f} MB"
                    if SHOW_PROGRESS:
                        print(f"\r    {msg}", end="", flush=True)
                    else:
                        log(f"  [{tag}] {msg}")
                    shown = time.time()
            send(bytes(buf), final=True)
            if SHOW_PROGRESS:
                print()
        except BaseException:
            try:
                up.delete(loc, headers=hdr)   # drop the half-finished upload
            except Exception:  # noqa: BLE001
                pass
            raise
        item, end = None, time.time() + myupload.APPEAR_TIMEOUT_S
        while item is None and time.time() < end:
            time.sleep(4)
            new = [v for i, v in myupload._list_ids(api).items()
                   if i not in before and v.get("originalName") == filename]
            if new:
                item = max(new, key=lambda v: v.get("createdAt", ""))
        if item is None:
            raise RuntimeError("uploaded but the item never showed up in the list")
        return myupload._wait_ready(api, item)


# ---------------------------------------------------------------- xhaccess (streamed through here)
def listed_variants(xh, master_url) -> dict:
    """{height: playlist url} as listed inside the master playlist itself. Some videos list only
    AV1 variants there, each with its own signed address."""
    out = {}
    try:
        lines = xh.session.get(master_url, timeout=30).text.splitlines()
    except Exception:  # noqa: BLE001
        return out
    for i, line in enumerate(lines):
        m = re.search(r"RESOLUTION=\d+x(\d+)", line)
        if m and i + 1 < len(lines) and lines[i + 1].strip() and not lines[i + 1].startswith("#"):
            out[int(m.group(1))] = urljoin(master_url, lines[i + 1].strip())
    return out


def pick_variant(xh, master_url, max_height):
    """Best quality up to max_height: the H.264 playlist beside the master if it answers,
    else the variant the master itself lists (AV1 for some videos). 403/404 qualities are skipped."""
    base = master_url.replace("_TPL_.av1.mp4.m3u8", "")
    listed = listed_variants(xh, master_url)
    for h in sorted({1080, 720, 480, 240, 144} | set(listed), reverse=True):
        if h > max_height:
            continue
        for url in (f"{base}{h}p.h264.mp4.m3u8", listed.get(h)):
            if not url:
                continue
            try:
                r = xh.session.get(url, timeout=30)
            except Exception:  # noqa: BLE001
                continue
            if r.status_code == 200 and "#EXTINF" in r.text:
                return url, h
    raise RuntimeError("no quality of this video is available")


def open_browser(p):
    """Headless system Chrome (no bundled Chromium needed), else Playwright's own."""
    try:
        return p.chromium.launch(channel="chrome", headless=True)
    except Exception:  # noqa: BLE001
        return p.chromium.launch(headless=True)


def hls_pieces(xh, playlist_url, workers=8):
    """Init segment, then every media segment in order, fetched in parallel."""
    text = xh.get(playlist_url).text
    init = re.search(r'#EXT-X-MAP:URI="([^"]+)"', text)
    segs = [urljoin(playlist_url, s) for s in re.findall(r"^(?!#)\S+", text, re.M)]
    if not segs:
        raise RuntimeError("playlist has no segments")
    if init:
        yield xh.get(urljoin(playlist_url, init.group(1))).content
    with ThreadPoolExecutor(workers) as pool:
        for i in range(0, len(segs), workers * 3):
            yield from pool.map(lambda u: xh.get(u).content, segs[i:i + workers * 3])


def run_xhaccess(args):
    """Send a channel's videos, --parallel N at a time (each one streams through this PC on its own)."""
    import threading
    from concurrent.futures import FIRST_COMPLETED, wait
    import xhaccess_bot as xh
    global SHOW_PROGRESS
    SHOW_PROGRESS = args.parallel == 1      # \r progress bars would overwrite each other
    slug = xh.channel_slug(args.target)
    who = re.sub(r"[-_]+", " ", slug).strip().title()
    claimed, lock = set(), threading.Lock()

    def one(it):
        vid, key = str(it["id"]), f"xhaccess:{it['id']}"
        try:
            info = xh.scrape_video(it["pageURL"])
            variant, height = pick_variant(xh, info["master"], args.quality)
            title = title_of(who, info["title"])
            log(f"{vid} '{title}' {info['duration'] // 60} min, {height}p")
            if args.dry_run:
                return True
            fname = f"{vid}-{xh.safe(info['title'])}.mp4"
            item = tus_stream(hls_pieces(xh, variant), fname, title, tag=vid)
            record(key, "xhaccess", who, title, item, info.get("views"))
            return True
        except Exception as e:  # noqa: BLE001
            log(f"  FAILED {vid}: {e}")
            return False

    n, running = 0, set()
    with ThreadPoolExecutor(args.parallel) as pool:
        for it in xh.channel_videos(slug):
            if args.max_videos and n >= args.max_videos:
                break
            key = f"xhaccess:{it['id']}"
            if not (args.all_videos or it.get("hasProducerBadge")):
                continue      # only videos with the "full video" badge; no length limit
            with lock:
                if already(key) or key in claimed:
                    continue
                claimed.add(key)
            n += 1
            running.add(pool.submit(one, it))
            if len(running) >= args.parallel:       # keep N in flight, start the next as one ends
                done, running = wait(running, return_when=FIRST_COMPLETED)
        wait(running)
    log(f"[{slug}] done, {n} video(s)")


# ---------------------------------------------------------------- hqporner / veporn (myupload fetches the .mp4)
def http_pieces(src, referer, size=8 << 20):
    """The direct .mp4 read from its site and handed on in pieces (nothing is stored)."""
    import requests
    h = {"User-Agent": "Mozilla/5.0", "Referer": referer}
    with requests.get(src, headers=h, stream=True, timeout=60) as r:
        r.raise_for_status()
        yield from r.iter_content(size)


def send_url(key, source, group, title, src, referer, views=0, dry=False):
    """Let myupload fetch the .mp4 itself; if its servers are refused (links tied to this PC's
    IP, HTTP 404/403 upstream), stream the file through this PC instead."""
    log(f"{key} '{title}'")
    if dry:
        return
    try:
        item = myupload.upload_by_url(src, key)
    except Exception as e:  # noqa: BLE001
        log(f"  myupload could not fetch it ({str(e)[:80]}); streaming it through this PC ...")
        try:
            name = re.sub(r"[^\w.-]+", "_", key.split(":", 1)[1])[:80] + ".mp4"
            item = tus_stream(http_pieces(src, referer), name, title)
        except Exception as e2:  # noqa: BLE001
            log(f"  FAILED {key}: {e2}")
            return
    record(key, source, group, title, item, views)


def run_hqporner(args):
    import bot as hq
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = open_browser(p)
        page = browser.new_context(user_agent=hq.UA).new_page()
        todo = [(u, hq.BASE + "/girls") for u in args.urls]
        for slug in args.actress or []:
            url, referer = f"{hq.BASE}/actress/{slug}", hq.BASE + "/girls"
            while url and (not args.max_videos or len(todo) < args.max_videos):
                page.goto(url, wait_until="domcontentloaded", referer=referer)
                try:
                    page.wait_for_selector("h3.meta-data-title a", timeout=15000)
                except Exception:  # noqa: BLE001
                    break
                todo += [(urljoin(hq.BASE, h), url) for h, _ in hq.listing_videos(page)]
                referer, url = url, hq.next_page_url(page)
        n = 0
        for vurl, referer in todo:
            if args.max_videos and n >= args.max_videos:
                break
            m = re.search(r"/hdporn/(\d+)", vurl)
            if not m or already(f"hqporner:{m.group(1)}"):
                continue
            try:
                info = hq.scrape_video(page, vurl, referer=referer)
                picked = hq.ui_pick_source(info.pop("_page"), info.pop("_frame"), args.quality)
                q, src = picked or hq.pick_source(info["sources"], args.quality)
                if args.min_minutes:
                    dur = hq.mp4_duration(src, info["player"])
                    if dur is None or dur < args.min_minutes * 60:
                        log(f"  skip {m.group(1)}: shorter than {args.min_minutes:g} min or length unknown")
                        continue
                who = (info["actresses"] or [""])[0]
                n += 1
                send_url(f"hqporner:{m.group(1)}", "hqporner", who, title_of(who, info["title"]), src,
                         info["player"], dry=args.dry_run)
            except Exception as e:  # noqa: BLE001
                log(f"  FAILED {vurl}: {e}")
            time.sleep(args.delay)
        browser.close()


def run_veporn(args):
    import veporn_bot as vp
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = open_browser(p)
        ctx = browser.new_context(user_agent=vp.UA)
        page = ctx.new_page(); page.set_default_timeout(30000)
        hrefs = [urljoin(vp.BASE, u) if u.startswith("/") else u for u in args.urls]
        if args.latest:
            vp.open_latest(page)
            while len(hrefs) < args.latest:
                links = vp.card_links(page)
                hrefs += [urljoin(vp.BASE, h) for h in links if urljoin(vp.BASE, h) not in hrefs]
                if len(hrefs) >= args.latest or not vp.load_more(page, len(links)):
                    break
            hrefs = hrefs[:args.latest]
        vpage = ctx.new_page(); vpage.set_default_timeout(30000)
        for url in hrefs:
            slug = url.split("veporn.com", 1)[-1].strip("/")
            if already(f"veporn:{slug}"):
                continue
            try:
                info = vp.scrape_video(vpage, url)
                if args.min_minutes:
                    dur = vp.mp4_duration(info["download_url"], vp.BASE + "/")
                    if dur is None or dur < args.min_minutes * 60:
                        log(f"  skip {slug}: shorter than {args.min_minutes:g} min or length unknown")
                        continue
                who = (info["pornstars"] or [""])[0]
                send_url(f"veporn:{slug}", "veporn", who, title_of(who, info["title"]),
                         info["download_url"], vp.BASE + "/", dry=args.dry_run)
            except Exception as e:  # noqa: BLE001
                log(f"  FAILED {url}: {e}")
            time.sleep(args.delay)
        browser.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="source", required=True)
    x = sub.add_parser("xhaccess"); x.add_argument("target", help="channel URL or slug")
    h = sub.add_parser("hqporner"); h.add_argument("urls", nargs="*", help="video page URLs")
    h.add_argument("--actress", nargs="*", help="actress slug(s), e.g. little-caprice")
    v = sub.add_parser("veporn"); v.add_argument("urls", nargs="*", help="video page URLs")
    v.add_argument("--latest", type=int, default=0, help="the N newest videos on veporn")
    x.add_argument("--parallel", type=int, default=1, help="videos sent at the same time (default 1)")
    x.add_argument("--all-videos", action="store_true",
                   help='also send videos without the "full video" badge (default: badge only)')
    for s in (x, h, v):
        s.add_argument("--max-videos", type=int, default=0, help="0 = no limit")
        s.add_argument("--min-minutes", type=float, default=0, help="skip shorter videos (default: none)")
        s.add_argument("--quality", type=int if s is x else str, default=720 if s is x else "720")
        s.add_argument("--delay", type=float, default=3.0, help="seconds between videos")
        s.add_argument("--dry-run", action="store_true", help="list what would be sent, send nothing")
    args = ap.parse_args()
    if not myupload.enabled():
        raise SystemExit("myupload disabled: set MYUPLOAD_API_KEY in .env")
    if args.source == "hqporner" and not (args.urls or args.actress):
        raise SystemExit("hqporner: give video URLs or --actress SLUG")
    if args.source == "veporn" and not (args.urls or args.latest):
        raise SystemExit("veporn: give video URLs or --latest N")
    {"xhaccess": run_xhaccess, "hqporner": run_hqporner, "veporn": run_veporn}[args.source](args)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    main()
