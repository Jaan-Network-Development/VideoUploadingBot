"""Separate myupload.us uploader (independent of the sxyprn posting).

python myupload_worker.py           one pass over every source's folder
python myupload_worker.py --watch   keep scanning for new downloads (the service)

Needs MYUPLOAD_API_KEY in .env. Files the sxyprn workers have already posted are kept until
this worker has sent them to myupload, then deleted here.
"""
import sys
import time

import myupload

WATCH_INTERVAL_S = 2 * 60


def main(watch=False):
    if not myupload.enabled():
        raise SystemExit("myupload disabled: set MYUPLOAD_API_KEY in .env (and MYUPLOAD_ENABLED = True)")
    while True:
        try:
            n = myupload.upload_pending()
            print(f"myupload pass done, {n} uploaded", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"myupload pass failed: {e!r:.300}", flush=True)
        if not watch:
            return
        time.sleep(WATCH_INTERVAL_S)


if __name__ == "__main__":
    main("--watch" in sys.argv[1:])
