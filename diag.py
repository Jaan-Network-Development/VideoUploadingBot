"""Diagnostics for a long-running bot: a log file plus a stuck detector.
(The same file is in VideoDowloaderBot and VideoUploaderBot - keep both copies identical.)

- Everything the bot prints (stdout + stderr) is also written, timestamped, to
  logs/<name>-<date>.log, so the history survives a closed or frozen window.
- Uncaught exceptions and hard crashes are logged with their full traceback;
  diag.log_exc() adds the traceback of a caught error to the log only.
- A watchdog thread notices when the bot has gone quiet for longer than its current
  step allows and writes a STUCK report: what it was doing, for how long, extra info
  (e.g. the buffer count) and the stack of every thread, i.e. the exact line it hangs on.
  If it starts moving again, a RECOVERED line says after how long.
- Every 5 minutes a [heartbeat] line (log file only) records the current status.

Use:
    diag.start("downloader", log_dir)        once, at startup
    diag.status("scraping 1234", allow=600)  what the bot is doing now / max quiet seconds
    diag.sleep(seconds, "why")               a sleep that does not count as being stuck
    diag.add_info(lambda: "...")             text appended to every STUCK report
    diag.log_exc()                           inside `except`: full traceback to the log
"""
import datetime
import faulthandler
import os
import sys
import threading
import time
import traceback
from pathlib import Path

STALL_S = 10 * 60        # default: quiet this long -> STUCK report
REPEAT_S = 30 * 60       # repeat the report while it stays stuck
HEARTBEAT_S = 5 * 60     # log-only status line
KEEP_DAYS = 14           # older log files are deleted at start

_lock = threading.RLock()
_log = None
_day = None
_name = "bot"
_log_dir = Path("logs")
_last = time.time()      # last sign of life (any output, status, sleep tick)
_status = "starting"
_status_since = time.time()
_allow = STALL_S
_info = []
_console = None          # the real console stream, for the short STUCK notice


def _stamp():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _fmt(seconds):
    seconds = int(seconds)
    if seconds < 120:
        return f"{seconds}s"
    if seconds < 7200:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"


def _open():
    global _log, _day
    _day = datetime.date.today()
    _log_dir.mkdir(parents=True, exist_ok=True)
    _log = open(_log_dir / f"{_name}-{_day}.log", "a", encoding="utf-8", errors="replace")
    faulthandler.enable(file=_log, all_threads=True)    # hard crashes (segfault, abort)


def _write_log(text):
    """Log file only - never the console, which may be the very thing that is frozen."""
    if _log is None:
        return
    with _lock:
        try:
            if datetime.date.today() != _day:            # new day, new file
                _log.close()
                _open()
            _log.write(text)
            _log.flush()
        except Exception:   # noqa: BLE001 - logging must never break the bot
            pass


def log(msg):
    """A line for the log file only (not shown in the window)."""
    _write_log(f"{_stamp()} {msg}\n")


class _Tee:
    """Console stream that also copies every finished line, timestamped, into the log."""

    def __init__(self, stream, tag=""):
        self._stream, self._tag, self._buf = stream, tag, ""

    def write(self, text):
        beat()
        self._buf += text
        lines = self._buf.split("\n")
        self._buf = lines.pop()
        if "\r" in self._buf:                            # progress line: keep only the latest
            self._buf = self._buf.rsplit("\r", 1)[1]
        if lines:
            _write_log("".join(f"{_stamp()} {self._tag}{line.rsplit(chr(13), 1)[-1]}\n"
                               for line in lines))
        # log first: if the console blocks here, the log already has the line
        return self._stream.write(text)

    def flush(self):
        return self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


def beat():
    global _last
    _last = time.time()


def status(text, allow=None):
    """Say what the bot is doing now; `allow` = how many quiet seconds are normal for it."""
    global _status, _status_since, _allow
    with _lock:
        if text != _status:
            _status, _status_since = text, time.time()
            log(f"[status] {text}")
        _allow = allow or STALL_S
    beat()


def sleep(seconds, why):
    """time.sleep that keeps signalling life, so long planned waits are not reported."""
    status(why)
    end = time.time() + seconds
    while True:
        left = end - time.time()
        if left <= 0:
            break
        time.sleep(min(left, 15))
        beat()


def add_info(fn):
    _info.append(fn)


def log_exc(note=""):
    """Inside an `except` block: write the full traceback to the log file only."""
    _write_log(f"{_stamp()} [traceback] {note}\n{traceback.format_exc()}")


def _hint(frame):
    """A plain-words guess at why a thread is blocked, from its innermost frames."""
    frames, f = [], frame
    for _ in range(8):
        if f is None:
            break
        frames.append((f.f_code.co_filename.replace("\\", "/").lower(), f.f_code.co_name))
        f = f.f_back
    me = __file__.replace("\\", "/").lower()
    if frames and frames[0] == (me, "write"):
        return ("blocked writing to the console window - the window is frozen "
                "(text selection / QuickEdit, or the window is paused)")
    if frames and frames[0] == (me, "sleep"):
        return "in a planned wait (diag.sleep) - not stuck itself"
    files = " ".join(path for path, _ in frames)
    if "playwright" in files or "greenlet" in files:
        return "blocked inside a Playwright call - the browser or page is not responding"
    if any(k in files for k in ("/requests/", "/urllib3/", "/httpx/", "/httpcore/", "/ssl.py", "/socket.py")):
        return "blocked on a network request"
    return None


def _report(idle, allow, st, since):
    now = time.time()
    out = ["", "=" * 78,
           f"{_stamp()} STUCK? no activity for {_fmt(idle)} (normal for this step: up to {_fmt(allow)})",
           f"  doing: {st}  (since {_fmt(now - since)})",
           f"  pid {os.getpid()}, threads {threading.active_count()}"]
    for fn in _info:
        try:
            out.append(f"  {fn()}")
        except Exception as e:   # noqa: BLE001
            out.append(f"  (info failed: {e!r})")
    names = {t.ident: t.name for t in threading.enumerate()}
    for ident, frame in sys._current_frames().items():
        if ident == threading.get_ident():
            continue                                     # the watchdog itself
        hint = _hint(frame)
        out.append(f"--- thread {names.get(ident, ident)}" + (f"  >> {hint}" if hint else ""))
        out.append("".join(traceback.format_stack(frame)).rstrip())
    out.append("=" * 78)
    _write_log("\n".join(out) + "\n")
    # tell the window too, from a throwaway thread: if the console is frozen this write
    # blocks, and the watchdog must not block with it
    path = _log_dir / f"{_name}-{_day}.log"
    threading.Thread(target=_notice, daemon=True,
                     args=(f"\n{time.strftime('%H:%M:%S')} [diag] no activity for {_fmt(idle)} "
                           f"while: {st} - details in {path}\n",)).start()


def _notice(text):
    try:
        _console.write(text)
        _console.flush()
    except Exception:   # noqa: BLE001
        pass


def _watchdog():
    stuck_since, stuck_status, reported, last_hb = None, "", 0.0, time.time()
    while True:
        time.sleep(15)
        now = time.time()
        with _lock:
            idle, allow, st, since, last = now - _last, _allow, _status, _status_since, _last
        if idle > allow:
            if stuck_since is None:
                stuck_since, stuck_status = last, st
            if now - reported >= REPEAT_S:
                reported = now
                _report(idle, allow, st, since)
        elif stuck_since is not None:
            log(f"RECOVERED: moving again after {_fmt(last - stuck_since)} of silence "
                f"(was stuck while: {stuck_status})")
            stuck_since, reported = None, 0.0
        if now - last_hb >= HEARTBEAT_S:
            last_hb = now
            log(f"[heartbeat] doing: {st} for {_fmt(now - since)}, last activity {_fmt(idle)} ago")


def _cleanup_old():
    cutoff = time.time() - KEEP_DAYS * 86400
    for f in _log_dir.glob(f"{_name}-*.log"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def start(name, log_dir):
    """Turn everything on. Safe to call more than once (later calls do nothing)."""
    global _name, _log_dir, _console
    if _log is not None:
        return
    _name, _log_dir = name, Path(log_dir)
    try:
        _open()
    except OSError as e:
        print(f"[diag] cannot open log file in {_log_dir}: {e}", file=sys.stderr)
        return
    _cleanup_old()
    _console = sys.stderr or sys.stdout
    if sys.stdout is not None:
        sys.stdout = _Tee(sys.stdout)
    if sys.stderr is not None:
        sys.stderr = _Tee(sys.stderr, "[stderr] ")

    prev_hook = sys.excepthook

    def excepthook(t, v, tb):
        log(f"UNCAUGHT {t.__name__} - the bot is exiting")
        prev_hook(t, v, tb)                              # prints the traceback (into the log too)
    sys.excepthook = excepthook

    prev_thread_hook = threading.excepthook

    def thread_hook(args):
        log(f"UNCAUGHT {args.exc_type.__name__} in thread {args.thread.name if args.thread else '?'}")
        prev_thread_hook(args)
    threading.excepthook = thread_hook

    import atexit
    atexit.register(lambda: log(f"===== {_name} exited"))
    threading.Thread(target=_watchdog, name="diag-watchdog", daemon=True).start()
    log(f"===== {_name} started: pid {os.getpid()}, python {sys.version.split()[0]}, "
        f"cwd {os.getcwd()}, argv {sys.argv}")
