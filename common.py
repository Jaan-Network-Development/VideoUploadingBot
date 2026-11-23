import json
import os
import random
import re
import shutil
import time
from contextlib import contextmanager
from pathlib import Path

import config


class DailyLimitReached(Exception):
    pass


class NotApproved(Exception):
    pass


class CaptchaRequired(Exception):
    pass



def queue(folder=None, include_uploaded=False):
    """Videos in folder, oldest first, minus those already posted."""
    folder = folder or config.VIDEOS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    files = [p for p in folder.iterdir() if p.suffix.lower() in config.VIDEO_EXTS]
    if not include_uploaded:
        done = _uploaded()
        files = [p for p in files if not is_uploaded(p, done)]
    return sorted(files, key=lambda p: p.stat().st_mtime)


def folders():
    """Every model folder under VIDEOS_DIR, in name order (VIDEOS_DIR itself if it has none)."""
    config.VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    subs = sorted(p for p in config.VIDEOS_DIR.iterdir() if p.is_dir())
    return subs or [config.VIDEOS_DIR]


def _meta(video: Path) -> dict:
    """The <name>.json next to the video (title, actresses, categories), or {}."""
    try:
        data = json.loads(video.with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def title_for(video: Path) -> str:
    """'<first actress> - <title>' from the json, else the filename."""
    meta = _meta(video)
    title = str(meta.get("title") or "").strip()
    if not title:
        return video.stem.replace("_", " ").replace("-", " ").strip()
    actresses = meta.get("actresses") or meta.get("pornstars") or []
    actress = str(actresses[0]).strip() if actresses else ""
    if not actress and meta.get("channel"):   # xhaccess: "tuktuk-patrol" -> "Tuktuk Patrol"
        actress = re.sub(r"[-_]+", " ", str(meta["channel"])).strip().title()
    if actress and not title.lower().startswith(actress.lower()):
        return f"{actress} - {title}"
    return title


def hashtags_for(video: Path) -> list:
    tags = []
    for cat in _meta(video).get("categories") or []:
        tag = "#" + re.sub(r"[^0-9A-Za-z]", "", str(cat))
        if len(tag) > 1 and tag not in tags:
            tags.append(tag)
    return tags


def _fit(text: str, limit) -> str:
    """Cut to limit without leaving half a word or hashtag at the end."""
    if not limit or len(text) <= limit:
        return text
    cut = text[:limit]
    if not text[limit].isspace():
        cut = cut.rsplit(None, 1)[0] if len(cut.split()) > 1 else cut
    return cut.rstrip()


def description_for(video: Path, limit=config.MAX_DESCRIPTION) -> str:
    sidecar = video.with_suffix(".txt")
    if sidecar.exists():
        text = sidecar.read_text(encoding="utf-8").strip()
    elif _meta(video):
        text = "\n".join(filter(None, [title_for(video), " ".join(hashtags_for(video))]))
    else:
        title = video.stem.replace("_", " ").replace("-", " ").strip()
        text = config.DESCRIPTION_TEMPLATE.format(title=title)
    return _fit(text, limit)


def video_key(video: Path) -> str:
    """The site id from the json when there is one, else the file name."""
    return str(_meta(video).get("id") or video.name)


def _state_file() -> Path:
    return config.VIDEOS_DIR / "state.json"


@contextmanager
def _state_lock(timeout=30):
    """Cross-process lock (state.json.lock), shared with the downloader's bot.py, so the two
    bots never read-modify-write state.json at the same moment and lose each other's entries."""
    lock = _state_file().with_name("state.json.lock")
    config.VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    end, acquired = time.time() + timeout, False
    while not acquired:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            acquired = True
        except (FileExistsError, PermissionError):
            try:
                if time.time() - lock.stat().st_mtime > 60:   # left behind by a crash
                    lock.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.time() > end:
                print("  warning: state.json.lock busy for 30s, writing anyway")
                break
            time.sleep(0.1)
    try:
        yield
    finally:
        if acquired:
            lock.unlink(missing_ok=True)


def _read_state() -> dict:
    """state.json as a dict. Retries while the other bot is replacing the file (Windows
    PermissionError) instead of returning {}, which would wipe both bots' history on the next write."""
    for attempt in range(30):
        try:
            data = json.loads(_state_file().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except PermissionError:
            if attempt == 29:
                raise
        except ValueError:     # only possible if written non-atomically; give it a moment
            if attempt == 29:
                print("  warning: state.json is corrupt, starting it fresh")
                return {}
        time.sleep(0.1)


def _write_state(state: dict):
    """Rewrite state.json atomically; keys we don't own (done_videos, done_actresses) stay untouched."""
    config.VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _state_file().with_name(f"state.json.{os.getpid()}.tmp")   # never the downloader's tmp
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
    for attempt in range(50):
        try:
            tmp.replace(_state_file())
            return
        except PermissionError:   # Windows: the other bot has the file open for a moment
            if attempt == 49:
                raise
            time.sleep(0.1)


def _uploaded() -> dict:
    """Ids of videos already posted (state.json 'uploaded_videos')."""
    up = _read_state().get("uploaded_videos")
    return up if isinstance(up, dict) else {}


def is_uploaded(video: Path, done=None) -> bool:
    """Posted before? Matched by site id, or by file name in case the .json is already gone."""
    done = _uploaded() if done is None else done
    return video_key(video) in done or any(
        isinstance(v, dict) and v.get("file") == video.name for v in done.values())


def record_upload(video: Path):
    """Remember a posted video in state.json so it is never uploaded again (call before finish deletes it)."""
    with _state_lock():
        state = _read_state()
        up = state.get("uploaded_videos")
        if not isinstance(up, dict):
            up = {}
        up[video_key(video)] = {
            "file": video.name,
            "title": title_for(video),
            "actress": video.parent.name if video.parent != config.VIDEOS_DIR else "",
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        state["uploaded_videos"] = up
        _write_state(state)


def record_actress_done(name: str):
    """Mark an actress folder as fully uploaded in state.json 'uploaded_actresses'."""
    with _state_lock():
        state = _read_state()
        lst = state.get("uploaded_actresses")
        if not isinstance(lst, list):
            lst = []
        if name not in lst:
            lst.append(name)
        state["uploaded_actresses"] = lst
        _write_state(state)


def _unlink(f: Path, tries=20) -> bool:
    """Delete, retrying while Chrome still holds the file open after the upload."""
    for attempt in range(tries):
        try:
            f.unlink(missing_ok=True)
            return True
        except PermissionError:
            time.sleep(0.5)
    return False


def cleanup_uploaded(folder=None):
    """Delete videos that are already posted but still on disk (deletion failed, or a crash
    between recording and deleting). Otherwise they sit there forever and fill the
    downloader's --buffer, which then stops downloading."""
    if not config.DELETE_AFTER_UPLOAD:
        return 0
    done = _uploaded()
    n = 0
    for video in queue(folder, include_uploaded=True):
        if is_uploaded(video, done):
            finish(video, ok=True)
            n += 1
    return n


def finish(video: Path, ok: bool):
    if ok and config.DELETE_AFTER_UPLOAD:
        # the video first and the .json last: if the video can't be deleted yet, its .json
        # (the site id) stays, so it is still recognised as uploaded and removed on the next pass
        if not _unlink(video):
            print(f"  could not delete {video.name} (in use); will retry on the next pass")
            return
        _unlink(video.with_suffix(".txt"))
        _unlink(video.with_suffix(".json"))
        return
    dest_dir = config.DONE_DIR if ok else config.FAILED_DIR
    if video.parent != config.VIDEOS_DIR:
        dest_dir = dest_dir / video.parent.name   # keep the model folder, names can repeat
    dest_dir.mkdir(parents=True, exist_ok=True)
    for f in (video, video.with_suffix(".txt"), video.with_suffix(".json")):
        if f.exists():
            shutil.move(str(f), dest_dir / f.name)


def pause():
    time.sleep(random.uniform(*config.DELAY_BETWEEN_UPLOADS))


def run_queue(upload_one):
    """upload_one(video, description) -> None, raises on failure."""
    for video in queue():
        print(f"Uploading {video.name} ...")
        try:
            upload_one(video, description_for(video))
        except (DailyLimitReached, NotApproved, CaptchaRequired) as e:
            print(f"Stopping: {e!r}")
            return
        except Exception as e:  # noqa: BLE001
            print(f"Failed {video.name}: {e!r}")
            finish(video, ok=False)
            continue
        record_upload(video)
        finish(video, ok=True)
        print("  done")
        pause()
