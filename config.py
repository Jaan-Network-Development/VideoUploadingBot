import os
from pathlib import Path

BASE_URL = "https://sxyprn.com"
POST_PAGE = f"{BASE_URL}/blog/all/0.html"

ROOT = Path(__file__).parent
# one subfolder per model, each holding <id>-<name>.mp4 plus <id>-<name>.json metadata
VIDEOS_DIR = Path(os.environ.get("VIDEOBOT_DOWNLOADS", "/var/lib/videobot/downloads"))
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
MAX_DESCRIPTION = 500          # the textarea limit on the site

DELAY_BETWEEN_UPLOADS = (15, 20)   # random seconds between uploads
ACTION_DELAY = (1.0, 4.5)          # random seconds before each automated step (kept under 5s)
UPLOAD_TIMEOUT = 60 * 60       # seconds to wait for a single upload

IDLE_POLL_S = 60               # when the folder is empty, look again after this many seconds
RETRY_AFTER_STOP_S = 30 * 60   # after a daily limit / not-approved stop, wait this long before retrying
