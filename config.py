import os
from pathlib import Path

BASE_URL = "https://sxyprn.com"
POST_PAGE = f"{BASE_URL}/blog/all/0.html"

ROOT = Path(__file__).parent
# Two download sources are uploaded in parallel, each by its own worker process with its own
# Chrome (own profile + debug port) so the two never touch each other's tabs:
#   hqporner: <id>-<name>.mp4 in one subfolder per model   (the downloader's default --out)
#   veporn:   <id>-<name>.mp4 flat in one folder           (veporn_bot.py, default --out)
# VIDEOBOT_DOWNLOADS / VIDEOBOT_DOWNLOADS_VEPORN override the folders.
_DL = ROOT.parent / "VideoDowloaderBot"
_WIN = os.name == "nt"
SOURCES = {
    "hqporner": {
        "videos_dir": Path(os.environ.get("VIDEOBOT_DOWNLOADS")
                           or (_DL / "downloads" if _WIN else "/var/lib/videobot/upload_downloads")),
        "port": 9222, "profile": "login_profile", "slot": 0,
    },
    "veporn": {
        "videos_dir": Path(os.environ.get("VIDEOBOT_DOWNLOADS_VEPORN")
                           or (_DL / "downloads_veporn" if _WIN else "/var/lib/videobot/upload_downloads_veporn")),
        "port": 9223, "profile": "login_profile_veporn", "slot": 1,
    },
}
START_STAGGER_S = 25           # second worker starts this much later, so the two never post in lockstep
WORKER_RESTART_S = 30          # supervisor restarts a worker that crashed after this long

# This process's source: the worker sets VIDEOBOT_SOURCE; default is the first (single-browser, as before).
SOURCE = os.environ.get("VIDEOBOT_SOURCE") or next(iter(SOURCES))
VIDEOS_DIR = SOURCES[SOURCE]["videos_dir"]
DEBUG_PORT = SOURCES[SOURCE]["port"]
CHROME_PROFILE = ROOT / SOURCES[SOURCE]["profile"]
WINDOW_SLOT = SOURCES[SOURCE]["slot"]   # windows are tiled side by side, one per slot
DONE_DIR = ROOT / "done"
FAILED_DIR = ROOT / "failed"
DELETE_AFTER_UPLOAD = True     # delete the video, .txt and .json once posted (False: move to DONE_DIR)
STATE_FILE = ROOT / "storage_state.json"   # session saved by login.py
UPLOADED_FILE = ROOT / "uploaded.json"     # every video posted so far; these are never uploaded again
ENV_FILE = ROOT / ".env"                   # SITE_LOGIN / SITE_PASSWORD
PROFILE_DIR = ROOT / "profile"             # persistent browser profile
ADBLOCK_DIR = ROOT / "extensions" / "ublock"   # uBlock Origin Lite (unpacked)

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".f4v", ".m4v"}

# Description: clip.mp4 uses clip.txt if present, else clip.json ("<actress> - <title>" plus
# the categories as #hashtags), else this template.
# {title} is the filename without extension, underscores/dashes turned into spaces.
DESCRIPTION_TEMPLATE = "{title}"
MYUPLOAD_ENABLED = True       # add myupload.us links to descriptions (needs MYUPLOAD_API_KEY in .env)
MAX_DESCRIPTION = 500          # the textarea limit on the site

DELAY_BETWEEN_UPLOADS = (15, 20)   # random seconds between uploads
ACTION_DELAY = (1.0, 4.5)          # random seconds before each automated step (kept under 5s)
UPLOAD_TIMEOUT = 60 * 60       # seconds to wait for a single upload

IDLE_POLL_S = 60               # when the folder is empty, look again after this many seconds
RETRY_AFTER_STOP_S = 30 * 60   # after a daily limit / not-approved stop, wait this long before retrying
