"""Every model folder in turn -> post to the site.

python run.py          all folders, one after another
python run.py --pick   choose a single folder
python run.py --watch  all folders, then keep checking for new downloads (the service)

Every source in config.SOURCES (hqporner + veporn) is uploaded in parallel to the same site: run.py
starts one worker process per source, each with its own Chrome window (own profile and debug port).
python run.py --source veporn   run just one source in this process (what the workers do)
"""
import os
import subprocess
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


def supervise(passthrough, script=None, source_flag=True):
    """One worker process per source (staggered); restart a crashed worker; Ctrl+C stops all."""
    procs = {}

    def start(name):
        env = dict(os.environ, VIDEOBOT_SOURCE=name)
        cmd = [sys.executable, script or __file__, *(["--source", name] if source_flag else []), *passthrough]
        procs[name] = subprocess.Popen(cmd, env=env)
        print(f"[run] started worker '{name}' (pid {procs[name].pid})", flush=True)

    try:
        for i, name in enumerate(config.SOURCES):
            if i:
                time.sleep(config.START_STAGGER_S)
            start(name)
        while True:
            time.sleep(5)
            for name, proc in list(procs.items()):
                if proc.poll() is not None:
                    if "--watch" not in passthrough:     # one-shot run: a finished worker is just done
                        del procs[name]
                        continue
                    print(f"[run] worker '{name}' exited ({proc.returncode}); restarting in "
                          f"{config.WORKER_RESTART_S}s", flush=True)
                    time.sleep(config.WORKER_RESTART_S)
                    start(name)
            if not procs:
                return
    except KeyboardInterrupt:
        print("[run] stopping workers ...", flush=True)
    finally:
        for proc in procs.values():
            if proc.poll() is None:
                proc.terminate()


if __name__ == "__main__":
    args = sys.argv[1:]
    watch = "--watch" in args
    if "--source" in args:                    # worker: config already picked the source from the env
        name = args[args.index("--source") + 1]
        if name not in config.SOURCES:
            raise SystemExit(f"unknown source {name!r}; choose from {', '.join(config.SOURCES)}")
        if os.environ.get("VIDEOBOT_SOURCE") != name:   # started by hand, not by the supervisor
            os.environ["VIDEOBOT_SOURCE"] = name
            os.execv(sys.executable, [sys.executable, __file__, *args])
        main(pick_folder() if "--pick" in args else None, watch=watch)
    elif "--pick" in args:
        main(pick_folder())
    else:
        supervise([a for a in args if a == "--watch"])
