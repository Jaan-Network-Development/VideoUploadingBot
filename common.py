import json
import random
import shutil
import time
from pathlib import Path

import config


class DailyLimitReached(Exception):
    pass


class NotApproved(Exception):
    pass


class CaptchaRequired(Exception):
    pass


def queue():
    config.VIDEOS_DIR.mkdir(exist_ok=True)
    files = [p for p in config.VIDEOS_DIR.iterdir() if p.suffix.lower() in config.VIDEO_EXTS]
    return sorted(files, key=lambda p: p.stat().st_mtime)


def description_for(video: Path) -> str:
    sidecar = video.with_suffix(".txt")
    if sidecar.exists():
        text = sidecar.read_text(encoding="utf-8").strip()
    else:
        title = video.stem.replace("_", " ").replace("-", " ").strip()
        text = config.DESCRIPTION_TEMPLATE.format(title=title)
    return text[: config.MAX_DESCRIPTION]


def _history():
    try:
        return json.loads(config.HISTORY_FILE.read_text())
    except (OSError, ValueError):
        return []


def uploads_last_24h() -> int:
    cutoff = time.time() - 86400
    return sum(1 for t in _history() if t > cutoff)


def record_upload():
    h = [t for t in _history() if t > time.time() - 86400] + [time.time()]
    config.HISTORY_FILE.write_text(json.dumps(h))


def finish(video: Path, ok: bool):
    dest_dir = config.DONE_DIR if ok else config.FAILED_DIR
    dest_dir.mkdir(exist_ok=True)
    for f in (video, video.with_suffix(".txt")):
        if f.exists():
            shutil.move(str(f), dest_dir / f.name)


def pause():
    time.sleep(random.uniform(*config.DELAY_BETWEEN_UPLOADS))


def run_queue(upload_one):
    """upload_one(video, description) -> None, raises on failure."""
    for video in queue():
        if uploads_last_24h() >= config.DAILY_LIMIT:
            print("Daily cap from config reached, stopping.")
            return
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
        record_upload()
        finish(video, ok=True)
        print("  done")
        pause()
