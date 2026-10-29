"""Browser-less uploader: replays the site's own requests with httpx.

Flow (taken from main2.js):
  POST /php/get_user_id.php                    -> user id
  POST /php/get_code.php {uid, recap_response} -> "uid|code|gss" | napp | limm
  POST <gss>/blog/vid_loader_n.php  mode=vid, vidfile, user_id, code, cover_time, post_text

Auth: reuses cookies from storage_state.json (created once by login.py).
If the site demands a captcha, pass a token YOU solved via env RECAP_RESPONSE.
This script does not solve captchas.
"""
import json
import os
import sys

import httpx

import common
import config

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Referer": config.POST_PAGE,
    "Origin": config.BASE_URL,
    "X-Requested-With": "XMLHttpRequest",
}


def load_cookies() -> httpx.Cookies:
    jar = httpx.Cookies()
    state = json.loads(config.STATE_FILE.read_text())
    for c in state["cookies"]:
        jar.set(c["name"], c["value"], domain=c["domain"], path=c.get("path", "/"))
    return jar


def make_uploader(client: httpx.Client):
    def upload_one(video, description):
        uid = client.post(f"{config.BASE_URL}/php/get_user_id.php").text.strip()
        if not uid:
            raise RuntimeError("not logged in - session expired, run login.py")

        recap = os.environ.get("RECAP_RESPONSE", "")
        code = client.post(
            f"{config.BASE_URL}/php/get_code.php",
            data={"uid": uid, "recap_response": recap},
        ).text.strip()
        if code == "napp":
            raise common.NotApproved("account not approved")
        if code == "limm":
            raise common.DailyLimitReached("site daily limit reached")
        if "|" not in code:
            raise common.CaptchaRequired(f"no upload code (got {code[:80]!r}); captcha token missing/invalid?")

        user_id, upload_code, gss = code.split("|")[:3]
        with video.open("rb") as fh:
            r = client.post(
                f"{gss}/blog/vid_loader_n.php",
                data={
                    "mode": "vid",
                    "user_id": user_id,
                    "cover_time": "3",
                    "code": upload_code,
                    "post_text": description,
                },
                files={"vidfile": (video.name, fh, "video/mp4")},
                timeout=httpx.Timeout(30, read=config.UPLOAD_TIMEOUT, write=config.UPLOAD_TIMEOUT),
            )
        if r.status_code != 200:
            raise RuntimeError(f"upload failed: HTTP {r.status_code} {r.text[:200]}")
        print("  server:", r.text[:200])

    return upload_one


def main():
    if not config.STATE_FILE.exists():
        sys.exit("No session. Run: python login.py")
    with httpx.Client(cookies=load_cookies(), headers=HEADERS, follow_redirects=True) as client:
        common.run_queue(make_uploader(client))


if __name__ == "__main__":
    main()
