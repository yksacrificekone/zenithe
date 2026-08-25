"""
ZENITH — core/logger.py
Unified logging. Writes to:
  - SQLite logs table (via database.py)
  - Rotating file logs/zenith.log
  - event_bus LOG_EVENT (non-blocking queue into UI)
Never imports UI components — zero circular deps.
"""

from __future__ import annotations
import os
import threading
import queue
import logging
import logging.handlers
from datetime import datetime

from core.event_bus import bus
from core.database import SessionLocal, Log

_LEVEL_MAP = {
    "DEBUG":    10,
    "INFO":     20,
    "SUCCESS":  25,
    "WARNING":  30,
    "ERROR":    40,
    "CRITICAL": 50,
}

logging.addLevelName(25, "SUCCESS")
_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
os.makedirs(_LOG_DIR, exist_ok=True)

_file_handler = logging.handlers.RotatingFileHandler(
    os.path.join(_LOG_DIR, "zenith.log"),
    maxBytes=10 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
_file_handler.setFormatter(
    logging.Formatter("[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s",
                      datefmt="%H:%M:%S")
)

_root = logging.getLogger("ZENITH")
_root.setLevel(logging.DEBUG)
_root.addHandler(_file_handler)

# Non-blocking queue feeds the UI live log panel
_log_queue: queue.Queue[dict] = queue.Queue(maxsize=2000)
_db_queue:  queue.Queue[dict] = queue.Queue(maxsize=5000)
_db_thread_stop = threading.Event()


def _db_writer() -> None:
    """Background thread drains _db_queue into SQLite."""
    while not _db_thread_stop.is_set():
        try:
            entry = _db_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        try:
            with SessionLocal() as session:
                session.add(Log(
                    timestamp=entry["timestamp"],
                    level=entry["level"],
                    module=entry["module"],
                    message=entry["message"],
                    thread_id=entry.get("thread_id"),
                    user_id=entry.get("user_id"),
                ))
                session.commit()
        except Exception:
            pass


_db_writer_thread = threading.Thread(target=_db_writer, daemon=True, name="LogDBWriter")
_db_writer_thread.start()


# ── Public API ─────────────────────────────────────────────────────────────────

def _emit(level: str, module: str, message: str,
          thread_id: str | None = None, user_id: int | None = None) -> None:
    entry = {
        "timestamp": datetime.utcnow(),
        "level":     level,
        "module":    module,
        "message":   message,
        "thread_id": thread_id or threading.current_thread().name,
        "user_id":   user_id,
    }
    # File log
    lvl = _LEVEL_MAP.get(level, 20)
    _root.getChild(module).log(lvl, message)
    # Non-blocking queue for UI
    try:
        _log_queue.put_nowait(entry)
    except queue.Full:
        pass
    # DB (async)
    try:
        _db_queue.put_nowait(entry)
    except queue.Full:
        pass
    # Event bus for any other listeners
    bus.publish(bus.LOG_EVENT, entry)


def debug(module: str, msg: str, **kw)    -> None: _emit("DEBUG",    module, msg, **kw)
def info(module: str, msg: str, **kw)     -> None: _emit("INFO",     module, msg, **kw)
def success(module: str, msg: str, **kw)  -> None: _emit("SUCCESS",  module, msg, **kw)
def warning(module: str, msg: str, **kw)  -> None: _emit("WARNING",  module, msg, **kw)
def error(module: str, msg: str, **kw)    -> None: _emit("ERROR",    module, msg, **kw)
def critical(module: str, msg: str, **kw) -> None: _emit("CRITICAL", module, msg, **kw)


def get_log_queue() -> queue.Queue:
    """Return the live-feed queue. UI drains this via after() polling."""
    return _log_queue


def get_recent_logs(limit: int = 500, level: str | None = None,
                    module: str | None = None) -> list[dict]:
    """Query DB for recent log entries (for Logs panel)."""
    from core.database import Log as LogModel
    with SessionLocal() as session:
        q = session.query(LogModel).order_by(LogModel.timestamp.desc())
        if level:
            q = q.filter(LogModel.level == level)
        if module:
            q = q.filter(LogModel.module == module)
        rows = q.limit(limit).all()
        return [
            {"timestamp": r.timestamp, "level": r.level,
             "module": r.module, "message": r.message}
            for r in reversed(rows)
        ]


def shutdown() -> None:
    _db_thread_stop.set()
