"""Every model folder in turn -> post to the site.

python run.py          all folders, one after another
python run.py --pick   choose a single folder
python run.py --watch  all folders, then keep checking for new downloads (the service)
"""
import sys
import time

import common
import config

WATCH_INTERVAL_S = 5 * 60      # pause between scans for new downloads
STOPPED_RETRY_S = 60 * 60      # pause after the site refused (limit / not approved)


def pick_folder():
    subs = common.folders()
    if subs == [config.VIDEOS_DIR]:
        return config.VIDEOS_DIR
    for n, p in enumerate(subs, 1):
        print(f"{n}. {p.name} ({len(common.queue(p))} videos)")
    while True:
        a = input("Select folder number: ").strip()
        if a.isdigit() and 1 <= int(a) <= len(subs):
            return subs[int(a) - 1]


def main(folder=None, watch=False):
    import upload_manual   # imported here: it pulls in playwright
    upload_manual.open_chrome()
    if folder:
        upload_manual.process_queue(folder)
        return
    while True:
        finished = upload_manual.process_all()
        if not watch:
            return
        wait = WATCH_INTERVAL_S if finished else STOPPED_RETRY_S
        print(f"Sleeping {wait // 60} min before checking for new videos ...", flush=True)
        time.sleep(wait)


if __name__ == "__main__":
    main(pick_folder() if "--pick" in sys.argv else None, watch="--watch" in sys.argv)
