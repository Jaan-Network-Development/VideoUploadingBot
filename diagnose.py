"""Open the bot's Chrome (same flags/profile) on diagnostic pages: python diagnose.py
Look at each tab through VNC, then press Enter here to close Chrome."""
import subprocess

import browser
import config

browser.load_env()
PAGES = [
    "https://browserleaks.com/ip",          # IP + timezone: must be the VPN's
    "https://browserleaks.com/webrtc",      # must NOT show the VPS's real IP
    "https://browserleaks.com/webgl",       # renderer must not be SwiftShader/llvmpipe
    "https://browserleaks.com/javascript",  # timezone/language vs VPN country, clock
]

proc = subprocess.Popen([
    browser.find_chrome(),
    f"--user-data-dir={config.ROOT / 'login_profile'}",
    "--no-first-run", "--no-default-browser-check",
    *browser.server_flags(), *PAGES,
], env=browser.launch_env())
input("Check the tabs, then press Enter to close Chrome... ")
proc.terminate()
