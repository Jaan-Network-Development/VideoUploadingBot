"""Uploader that works in your normal Chrome so Cloudflare's check passes.

Per video: the bot expands the post box, types the description and attaches the
video, then DISCONNECTS so Cloudflare verifies a quiet browser (it hangs or fails
while automation is attached). It reconnects only briefly to see whether the
check finished, then clicks Send. The captcha itself is never solved by the bot:
if the widget wants a tick, you tick it in Chrome. If anything fails, that video
falls back to the manual steps (paste, attach, Send yourself).
Usage: python upload_manual.py   (log in first with login.py, which also continues here)
"""
import json
import random
import subprocess
import time

from playwright.sync_api import sync_playwright

import browser
import common
import config

PORT = 9222
CAPTCHA_WAIT_S = 5 * 60
CAPTCHA_POLL_S = 5


def copy_to_clipboard(text):
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "[Console]::InputEncoding=[Text.Encoding]::UTF8; Set-Clipboard -Value ([Console]::In.ReadToEnd())"],
        input=text.encode("utf-8"), check=True,
    )


def open_chrome():
    """Start Chrome on the login profile and restore the saved session into it.

    login.py kills its Chrome right after saving, before the login cookie reaches
    the profile on disk, so the cookies are loaded from storage_state.json here.
    The bot is connected only for this moment, then released.
    """
    proc = subprocess.Popen([
        browser.find_chrome(),
        f"--remote-debugging-port={PORT}",
        f"--user-data-dir={config.ROOT / 'login_profile'}",
        "--no-first-run", "--no-default-browser-check",
        config.POST_PAGE,
    ])
    browser.wait_port(PORT)
    try:
        cookies = json.loads(config.STATE_FILE.read_text())["cookies"]
    except (OSError, ValueError, KeyError):
        print("No saved session; run login.py first.")
        return proc
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
        ctx = b.contexts[0]
        ctx.add_cookies(cookies)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.reload()
        b.close()  # only disconnects; Chrome stays open
    return proc


def site_page(ctx):
    """The sxyprn tab, with any ad tabs closed."""
    page = next((pg for pg in ctx.pages if "sxyprn" in pg.url), ctx.pages[0])
    for extra in ctx.pages:
        if extra != page:
            extra.close()
    page.bring_to_front()
    return page


def attach_and_fill(video, description):
    """Expand the post box, fill the description, attach the video; then disconnect."""
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
        page = site_page(b.contexts[0])
        page.goto(config.POST_PAGE, wait_until="domcontentloaded")
        try:
            page.wait_for_selector(".addpost_text", state="attached", timeout=15000)
        except Exception:
            raise RuntimeError("post form not found - session expired? run login.py")
        page.click(".addpost_text")  # expands the box (ad tabs are closed by the next check)
        page.fill(".addpost_text", description)
        page.set_input_files("#addpost_vid_file", str(video))
        page.wait_for_function(
            "() => typeof wantLoadVideoFile !== 'undefined' && wantLoadVideoFile === true",
            timeout=15000,
        )
        page.wait_for_timeout(1000)  # let cover thumbnails render
        b.close()  # disconnect so Cloudflare verifies without automation attached


def captcha_passed():
    """Brief reconnect: has the Cloudflare check finished? (Also closes ad tabs.)"""
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
        page = site_page(b.contexts[0])
        needed = page.evaluate("() => typeof recap_ok !== 'undefined' && !recap_ok")
        b.close()
    return not needed


def wait_for_captcha():
    print(f"  Waiting for Cloudflare's check (tick it in Chrome if it asks, up to {CAPTCHA_WAIT_S // 60} min)...")
    end = time.time() + CAPTCHA_WAIT_S
    while time.time() < end:
        time.sleep(CAPTCHA_POLL_S)
        if captcha_passed():
            return
    raise common.CaptchaRequired("Cloudflare check not finished in time")


def send_and_wait():
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
        page = site_page(b.contexts[0])
        page.click(".blog_send")
        page.wait_for_function(
            """() => {
                const t = document.querySelector('.addpost_mainprogressbar_wrap > span')?.innerText || '';
                return /All Done|Error|not approved|limit/i.test(t);
            }""",
            timeout=config.UPLOAD_TIMEOUT * 1000,
        )
        text = page.locator(".addpost_mainprogressbar_wrap > span").inner_text()
        b.close()
    low = text.lower()
    if "limit" in low:
        raise common.DailyLimitReached(text)
    if "not approved" in low:
        raise common.NotApproved(text)
    if "error" in low:
        raise RuntimeError(text)


def upload_auto(video, description):
    attach_and_fill(video, description)
    wait_for_captcha()
    send_and_wait()


def manual_fallback(video, description):
    """Returns the answer: '' uploaded, 's' skip, 'f' failed, 'q' quit."""
    copy_to_clipboard(description)
    # one string, path quoted: a list makes Explorer fall back to Documents when the name has spaces
    subprocess.Popen(f'explorer /select,"{video}"')
    print("  Description copied to clipboard. In Chrome: click the post box, paste (Ctrl+V), "
          "Attach the file, tick the captcha, Send.")
    while True:
        answer = input("  Enter = uploaded, s = skip, f = failed, q = quit: ").strip().lower()
        if answer in ("", "s", "f", "q"):
            return answer


def process_queue(folder=None):
    videos = common.queue(folder)
    if not videos:
        print(f"No videos in {config.VIDEOS_DIR}")
        return
    for video in videos:
        if common.uploads_last_24h() >= config.DAILY_LIMIT:
            print("Daily cap from config reached, stopping.")
            return
        description = common.description_for(video)
        print(f"\nUploading {video.name} ...")
        try:
            upload_auto(video, description)
            answer = ""
        except (common.DailyLimitReached, common.NotApproved) as e:
            print(f"Stopping: {e!r}")
            return
        except Exception as e:  # noqa: BLE001
            print(f"  Automatic upload failed ({e!r}); doing this one by hand.")
            answer = manual_fallback(video, description)
        if answer == "q":
            return
        if answer == "s":
            continue
        if answer == "f":
            common.finish(video, ok=False)
            continue
        common.record_upload()
        common.finish(video, ok=True)
        print("  done")
        if video != videos[-1]:
            lo, hi = config.DELAY_BETWEEN_UPLOADS
            wait = random.randint(lo, hi)
            print(f"  next video in {wait}s (Ctrl+C to skip the wait)")
            try:
                time.sleep(wait)
            except KeyboardInterrupt:
                pass


def main():
    open_chrome()
    process_queue()


if __name__ == "__main__":
    main()
