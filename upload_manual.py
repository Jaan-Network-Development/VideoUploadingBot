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
import shutil
import subprocess
import sys
import time

from playwright.sync_api import sync_playwright

import browser
import common
import config

PORT = 9222
CAPTCHA_WAIT_S = 5 * 60
CAPTCHA_POLL_S = 5


def copy_to_clipboard(text):
    commands = [
        ["powershell", "-NoProfile", "-Command",
         "[Console]::InputEncoding=[Text.Encoding]::UTF8; Set-Clipboard -Value ([Console]::In.ReadToEnd())"],
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
    ]
    for cmd in commands:
        if shutil.which(cmd[0]):
            subprocess.run(cmd, input=text.encode("utf-8"), check=True)
            return
    print("No clipboard tool found (install xclip: sudo apt install xclip). Description:")
    print(text)


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
        *browser.server_flags(),
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
    host = config.BASE_URL.split("//", 1)[1]
    page = next((pg for pg in ctx.pages if host in pg.url), None)
    if page is None:   # an ad hijacked the site's tab: reuse a tab and go back
        page = ctx.pages[0]
        page.goto(config.POST_PAGE, wait_until="domcontentloaded")
    for extra in ctx.pages:
        if extra != page:
            extra.close()
    page.bring_to_front()
    return page


def set_file(page, selector, path):
    """Attach a file over raw CDP: set_input_files refuses files over 50 MB on a CDP connection."""
    cdp = page.context.new_cdp_session(page)
    try:
        root = cdp.send("DOM.getDocument")["root"]["nodeId"]
        node = cdp.send("DOM.querySelector", {"nodeId": root, "selector": selector})["nodeId"]
        cdp.send("DOM.setFileInputFiles", {"nodeId": node, "files": [str(path)]})
    finally:
        cdp.detach()


def attach_and_fill(video, description):
    """Expand the post box, fill the description, attach the video; then disconnect."""
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
        # the first clicks open pop-up ads (or redirect the tab); close them and retry
        for attempt in range(1, 6):
            page = site_page(b.contexts[0])
            try:
                page.goto(config.POST_PAGE, wait_until="domcontentloaded")
                try:
                    page.wait_for_selector(".addpost_text", state="attached", timeout=15000)
                except Exception:
                    shot = config.ROOT / "last_failure.png"
                    try:
                        page.screenshot(path=str(shot))
                    except Exception:  # noqa: BLE001
                        pass
                    print(f"  page: {page.url!r} title: {page.title()!r} (screenshot: {shot})")
                    raise RuntimeError("post form not found - session expired? run login.py")
                page.click(".addpost_text", timeout=10000)  # expands the box
                site_page(b.contexts[0])  # close ad tabs the click opened
                page.fill(".addpost_text", description, timeout=10000)
                set_file(page, "#addpost_vid_file", video)
                break
            except RuntimeError:
                raise
            except Exception as e:  # noqa: BLE001
                print(f"  ad interfered (attempt {attempt}/5): {e!r:.200}")
                if attempt == 5:
                    raise
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
    if not sys.stdin.isatty():   # running as a service: nobody to ask, retry on the next pass
        print("  No terminal to ask; leaving this video in the queue for the next pass.")
        return "s"
    copy_to_clipboard(description)
    if sys.platform.startswith("win"):
        # one string, path quoted: a list makes Explorer fall back to Documents when the name has spaces
        subprocess.Popen(f'explorer /select,"{video}"')
    elif shutil.which("xdg-open"):
        subprocess.Popen(["xdg-open", str(video.parent)])
    print(f"  File: {video}")
    print("  Description copied to clipboard. In Chrome: click the post box, paste (Ctrl+V), "
          "Attach the file, tick the captcha, Send.")
    while True:
        answer = input("  Enter = uploaded, s = skip, f = failed, q = quit: ").strip().lower()
        if answer in ("", "s", "f", "q"):
            return answer


def process_queue(folder=None):
    """Post every pending video in folder. Returns False when the whole run should stop."""
    videos = common.queue(folder)
    skipped = len(common.queue(folder, include_uploaded=True)) - len(videos)
    if skipped:
        print(f"{skipped} video(s) already uploaded before, skipping them.")
    if not videos:
        print(f"No new videos in {folder or config.VIDEOS_DIR}")
        return True
    for video in videos:
        description = common.description_for(video)
        print(f"\nUploading {video.name} ...")
        try:
            upload_auto(video, description)
            answer = ""
        except (common.DailyLimitReached, common.NotApproved) as e:
            print(f"Stopping: {e!r}")
            return False
        except Exception as e:  # noqa: BLE001
            print(f"  Automatic upload failed ({e!r}); doing this one by hand.")
            answer = manual_fallback(video, description)
        if answer == "q":
            return False
        if answer == "s":
            continue
        if answer == "f":
            common.finish(video, ok=False)
            continue
        common.record_upload(video)
        common.finish(video, ok=True)
        print("  done")
        wait = random.uniform(*config.DELAY_BETWEEN_UPLOADS)
        print(f"  next video in {wait:.0f}s (Ctrl+C to skip the wait)")
        try:
            time.sleep(wait)
        except KeyboardInterrupt:
            pass
    return True


def process_all(prepare=None):
    """Go through every model folder in turn; prepare(folder) runs before each one.

    Returns False if the run was stopped (site limit, account not approved, quit)."""
    for folder in common.folders():
        print(f"\n=== {folder.name} ===")
        if prepare:
            prepare(folder)
        if not process_queue(folder):
            return False
        if folder != config.VIDEOS_DIR and not common.queue(folder):
            common.record_actress_done(folder.name)
            print(f"{folder.name}: all videos uploaded.")
    print("\nAll folders done.")
    return True


def main():
    open_chrome()
    process_all()


if __name__ == "__main__":
    main()
