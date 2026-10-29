"""Browser-driven uploader (Playwright). Usage: python upload_browser.py [--show] [--devtools]

With --show (or --devtools) the browser is visible and the bot waits for you
to tick the captcha when one appears. Headless runs stop instead."""
import sys

from playwright.sync_api import sync_playwright

import browser
import common
import config


CAPTCHA_WAIT_MS = 5 * 60 * 1000


def make_uploader(page, wait_for_human):
    def upload_one(video, description):
        page.goto(config.POST_PAGE, wait_until="domcontentloaded")
        # the form is injected after login; fail clearly if the session expired
        try:
            page.wait_for_selector(".addpost_text", state="attached", timeout=15000)
        except Exception:
            raise RuntimeError("post form not found - session expired? run login.py")
        page.click(".addpost_text")
        page.fill(".addpost_text", description)
        page.set_input_files("#addpost_vid_file", str(video))
        page.wait_for_function(
            "() => typeof wantLoadVideoFile !== 'undefined' && wantLoadVideoFile === true",
            timeout=15000,
        )
        page.wait_for_timeout(1000)  # let cover thumbnails render

        if page.evaluate("() => typeof recap_ok !== 'undefined' && !recap_ok"):
            if not wait_for_human:
                raise common.CaptchaRequired("captcha needs a human; run with --show and solve it")
            print("  Captcha shown - tick it in the browser window (waiting up to 5 min)...")
            try:
                page.wait_for_function("() => recap_ok === true", timeout=CAPTCHA_WAIT_MS)
            except Exception:
                raise common.CaptchaRequired("captcha not solved in time")
        page.click(".blog_send")

        page.wait_for_function(
            """() => {
                const t = document.querySelector('.addpost_mainprogressbar_wrap > span')?.innerText || '';
                return /All Done|Error|not approved|limit/i.test(t);
            }""",
            timeout=config.UPLOAD_TIMEOUT * 1000,
        )
        text = page.locator(".addpost_mainprogressbar_wrap > span").inner_text()
        if "limit" in text.lower():
            raise common.DailyLimitReached(text)
        if "not approved" in text.lower():
            raise common.NotApproved(text)
        if "error" in text.lower():
            raise RuntimeError(text)

    return upload_one


def main():
    devtools = "--devtools" in sys.argv
    headless = "--show" not in sys.argv and not devtools
    if not config.STATE_FILE.exists():
        sys.exit("No session. Run: python login.py")
    with sync_playwright() as p:
        ctx = browser.launch(p, headless=headless, devtools=devtools)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            common.run_queue(make_uploader(page, wait_for_human=not headless))
        finally:
            ctx.storage_state(path=str(config.STATE_FILE))
            ctx.close()


if __name__ == "__main__":
    main()
