from pathlib import Path

BASE_URL = "https://sxyprn.com"
POST_PAGE = f"{BASE_URL}/blog/all/0.html"

ROOT = Path(__file__).parent
VIDEOS_DIR = ROOT / "videos"
DONE_DIR = ROOT / "done"
FAILED_DIR = ROOT / "failed"
STATE_FILE = ROOT / "storage_state.json"   # session saved by login.py
HISTORY_FILE = ROOT / "history.json"       # upload timestamps for the daily cap
ENV_FILE = ROOT / ".env"                   # SITE_LOGIN / SITE_PASSWORD
PROFILE_DIR = ROOT / "profile"             # persistent browser profile
ADBLOCK_DIR = ROOT / "extensions" / "ublock"   # uBlock Origin Lite (unpacked)

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".f4v", ".m4v"}

# Description: videos/clip.mp4 uses videos/clip.txt if present, else this template.
# {title} is the filename without extension, underscores/dashes turned into spaces.
DESCRIPTION_TEMPLATE = "{title}"
MAX_DESCRIPTION = 500          # the textarea limit on the site

DAILY_LIMIT = 5                # stop after this many uploads per 24h
DELAY_BETWEEN_UPLOADS = (6, 15)   # random seconds between uploads (10-25 min)
UPLOAD_TIMEOUT = 60 * 60       # seconds to wait for a single upload
