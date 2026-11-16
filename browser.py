"""Shared browser setup: persistent profile, uBlock Origin Lite, devtools helpers."""
import json
import os
import shutil
import socket
import time

import config

# The site flags devtools as open when outer size differs from inner size.
# Report matching sizes so devtools can stay open without tripping that check.
DEVTOOLS_ENABLER_JS = """
Object.defineProperty(window, 'outerWidth',  {get: () => window.innerWidth});
Object.defineProperty(window, 'outerHeight', {get: () => window.innerHeight});
"""


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
    raise SystemExit("Chrome/Edge not found; edit CHROME_CANDIDATES in browser.py")


def server_flags():
    """Extra Chrome flags for a Linux VPS.

    --no-sandbox is only added when running as root (run as a normal user to avoid it).
    --disable-gpu is deliberately NOT used: it makes WebGL report a software renderer,
    which Cloudflare treats as a bot signal.
    Set BOT_LANG (e.g. en-GB) in .env to match the VPN country; set the timezone with
    BOT_TZ (e.g. Europe/London), applied through launch_env().
    """
    flags = []
    if os.name == "posix" and os.geteuid() == 0:
        flags += ["--no-sandbox", "--disable-dev-shm-usage"]
    lang = os.environ.get("BOT_LANG")
    if lang:
        flags += [f"--lang={lang}", f"--accept-lang={lang}"]
    # no GPU on a VPS: Chrome blocklists WebGL, which Cloudflare treats as a bot signal.
    # Force software WebGL (SwiftShader) so WebGL exists.
    flags += ["--ignore-gpu-blocklist", "--use-gl=angle", "--use-angle=swiftshader",
              "--enable-unsafe-swiftshader"]
    # two Chromes share the screen: keep an overlapped/background one running at full speed
    # (Cloudflare's check stalls in a throttled window) and tile them side by side.
    flags += ["--disable-features=CalculateNativeWinOcclusion",
              "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
              "--disable-background-timer-throttling",
              f"--window-position={config.WINDOW_SLOT * 700},0", "--window-size=1000,900"]
    # stop WebRTC exposing the VPS's real IP outside the VPN
    flags += ["--force-webrtc-ip-handling-policy=disable_non_proxied_udp"]
    return flags


def launch_env():
    """Environment for the Chrome process: timezone matching the VPN exit country."""
    env = dict(os.environ)
    if os.environ.get("BOT_TZ"):
        env["TZ"] = os.environ["BOT_TZ"]
    return env


def wait_port(port, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.3)
    raise SystemExit(
        "Chrome opened but not with the debug port. A Chrome using the same profile is probably "
        "already running: close ALL Chrome windows (check Task Manager) and run again."
    )


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
