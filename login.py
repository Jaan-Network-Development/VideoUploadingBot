"""The one script to run: python login.py

Opens your normal Chrome (no automation flags), logs in if the saved session has
expired, then uploads everything in videos/ (see upload_manual.py).

Cloudflare rejects browsers launched by Playwright, so Chrome is started as an
ordinary program and the bot is attached only in short bursts, never while
Cloudflare is verifying.
"""
import json
import os
import random
import subprocess
import sys
import time

from playwright.sync_api import sync_playwright

import browser
import config
import upload_manual

PORT = 9222


def logged_in(ctx):
    # the site returns your user id only when logged in (same check the bot relies on)
    return bool(ctx.request.post(f"{config.BASE_URL}/php/get_user_id.php").text().strip())


def restore_session():
    """Load the saved cookies into Chrome; True if the site accepts them."""
    try:
        cookies = json.loads(config.STATE_FILE.read_text())["cookies"]
    except (OSError, ValueError, KeyError):
        return False
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
        ctx = b.contexts[0]
        ctx.add_cookies(cookies)
        ok = logged_in(ctx)
        if ok:
            (ctx.pages[0] if ctx.pages else ctx.new_page()).reload()
        b.close()  # only disconnects; Chrome stays open
    return ok


def pause():
    time.sleep(random.uniform(*config.ACTION_DELAY))


def site_page(ctx):
    """The site's own tab (ads and extension welcome pages are other tabs)."""
    host = config.BASE_URL.split("//", 1)[1]
    return next((pg for pg in ctx.pages if host in pg.url), None)


def open_login_form(login, password):
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
        ctx = b.contexts[0]
        page = site_page(ctx)
        if page is None:
            page = ctx.pages[0]
            page.goto(config.POST_PAGE)
        print(f"   Using tab: {page.url}")
        # the "Add your post" bar opens pop-up ads on the first clicks; close them and retry
        for attempt in range(1, 21):
            for extra in ctx.pages:
                if extra != page:
                    extra.close()
            if config.BASE_URL.split("//", 1)[1] not in page.url:
                # an ad redirected the site's tab itself; go back
                page.goto(config.POST_PAGE)
            page.bring_to_front()
            if page.is_visible("#lin_email"):
                break
            try:
                page.click(".addpost_login", timeout=3000)
            except Exception as e:
                print(f"   click {attempt} failed: {str(e).splitlines()[0]}")
            else:
                print(f"   clicked {attempt}")
            page.wait_for_timeout(1500)
        if page.is_visible("#lin_email") and login and password:
            page.fill("#lin_email", login)
            page.fill("#lin_pswd", password)
            print(f"2. Login form opened and filled with {login}.")
        elif page.is_visible("#lin_email"):
            print("2. Login form opened. Enter your credentials.")
        else:
            print("2. Could not open the login form (already logged in?). Open it by hand if needed.")
        b.close()  # only disconnects; Chrome stays open


def click_login_until_ok():
    while True:
        print("3. Tick the Cloudflare box in the form if it asks; the bot clicks Login automatically...")
        pause()
        with sync_playwright() as p:
            b = p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
            ctx = b.contexts[0]
            page = site_page(ctx) or ctx.pages[0]
            try:
                page.click("#modal_lin_btn", timeout=5000)
                page.wait_for_timeout(4000)
            except Exception:
                print("   Login button not found; click it by hand if the form is open.")
            ok = logged_in(ctx)
            if ok:
                ctx.storage_state(path=str(config.STATE_FILE))
            b.close()
        if ok:
            return
        print("   The site still sees you as logged out (wrong password or check not finished); retrying.")
        pause()


def main():
    browser.load_env()
    login, password = os.environ.get("SITE_LOGIN"), os.environ.get("SITE_PASSWORD")

    # uBlock can block Cloudflare's challenge scripts, so it is off unless: python login.py --adblock
    adblock = "--adblock" in sys.argv
    proc = subprocess.Popen([
        browser.find_chrome(),
        f"--remote-debugging-port={PORT}",
        f"--user-data-dir={config.ROOT / 'login_profile'}",
        *([f"--disable-extensions-except={config.ADBLOCK_DIR}",
           f"--load-extension={config.ADBLOCK_DIR}"] if adblock else []),
        "--no-first-run",
        "--no-default-browser-check",
        config.POST_PAGE,
    ])
    try:
        browser.wait_port(PORT)
        if restore_session():
            print("Already logged in (saved session still valid).")
        else:
            print("1. Waiting for the site to load, then opening the login form...")
            pause()
            open_login_form(login, password)
            click_login_until_ok()
            print(f"Logged in OK. Saved {config.STATE_FILE}")
    except BaseException:
        proc.terminate()
        raise
    # Chrome stays open and continues straight into the upload queue (same window, no restart)
    upload_manual.process_queue()


if __name__ == "__main__":
    main()
