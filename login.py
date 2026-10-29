"""Log in with your normal Chrome (no automation flags), then save the session.

Cloudflare rejects browsers launched by Playwright, so this opens the installed
Chrome as an ordinary program. You log in and tick the captcha yourself; after
you press Enter, the cookies are exported to storage_state.json for the bot.
"""
import os
import shutil
import socket
import subprocess
import time

from playwright.sync_api import sync_playwright

import browser
import config

PORT = 9222
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    shutil.which("google-chrome") or "",
    shutil.which("chromium") or "",
]


def find_chrome():
    for path in CHROME_CANDIDATES:
        if path and os.path.exists(path):
            return path
    raise SystemExit("Chrome/Edge not found; edit CHROME_CANDIDATES in login.py")


def wait_port(port, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.3)
    raise SystemExit("Chrome did not start")


browser.load_env()
login, password = os.environ.get("SITE_LOGIN"), os.environ.get("SITE_PASSWORD")

proc = subprocess.Popen([
    find_chrome(),
    f"--remote-debugging-port={PORT}",
    f"--user-data-dir={config.ROOT / 'login_profile'}",
    "--no-first-run",
    "--no-default-browser-check",
    config.POST_PAGE,
])
try:
    wait_port(PORT)
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
        ctx = b.contexts[0]
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if login and password:
            try:
                page.wait_for_selector(".addpost_login", timeout=20000)
                page.click(".addpost_login")
                page.fill("#lin_email", login)
                page.fill("#lin_pswd", password)
                print(f"Filled {login}. Tick the Cloudflare box and click Login.")
            except Exception:
                print("Could not auto-fill (already logged in?). Log in by hand if needed.")
        else:
            print("No credentials in .env; log in by hand.")
        while True:
            input("When you are logged in, press Enter here to save the session... ")
            # same check the bot relies on: the site returns your user id only when logged in
            uid = ctx.request.post(f"{config.BASE_URL}/php/get_user_id.php").text().strip()
            if uid:
                break
            print("The site still sees you as logged out. Finish logging in, then press Enter again.")
        ctx.storage_state(path=str(config.STATE_FILE))
        b.close()
    print(f"Logged in OK. Saved {config.STATE_FILE}")
finally:
    proc.terminate()
