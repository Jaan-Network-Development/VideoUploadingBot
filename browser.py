"""Shared browser setup: persistent profile, uBlock Origin Lite, devtools helpers."""
import json
import os

import config

# The site flags devtools as open when outer size differs from inner size.
# Report matching sizes so devtools can stay open without tripping that check.
DEVTOOLS_ENABLER_JS = """
Object.defineProperty(window, 'outerWidth',  {get: () => window.innerWidth});
Object.defineProperty(window, 'outerHeight', {get: () => window.innerHeight});
"""


def load_env():
    """Tiny .env reader (KEY=value per line) so no extra dependency is needed."""
    try:
        for line in config.ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except OSError:
        pass


def launch(p, headless=True, devtools=False):
    """Return a persistent context with the adblock extension loaded.

    Extensions need a persistent context and the new headless mode, so
    headless uses --headless=new on the full chromium channel.
    """
    ext = str(config.ADBLOCK_DIR)
    args = [f"--disable-extensions-except={ext}", f"--load-extension={ext}"]
    if headless:
        args.append("--headless=new")
    if devtools:
        args.append("--auto-open-devtools-for-tabs")
    ctx = p.chromium.launch_persistent_context(
        str(config.PROFILE_DIR),
        channel="chromium",
        headless=False,  # headless handled by --headless=new above, required for extensions
        args=args,
        no_viewport=not headless,
    )
    if devtools:
        ctx.add_init_script(DEVTOOLS_ENABLER_JS)
    # session comes from login.py (done in a normal Chrome, see there)
    try:
        ctx.add_cookies(json.loads(config.STATE_FILE.read_text())["cookies"])
    except (OSError, ValueError, KeyError):
        pass
    return ctx
