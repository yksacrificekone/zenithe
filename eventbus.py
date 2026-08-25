"""
ZENITH — core/event_bus.py
Lightweight synchronous pub/sub. Modules publish events; UI widgets and other
modules subscribe. All dispatch happens on the publisher's thread — subscribers
must be thread-safe (queue into UI, never touch tkinter widgets directly).
"""

from __future__ import annotations
import threading
from collections import defaultdict
from typing import Callable, Any


class EventBus:
    """Thread-safe publish/subscribe event dispatcher."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)

    # ── Subscription ──────────────────────────────────────────────────────────

    def subscribe(self, event: str, callback: Callable[[dict], None]) -> None:
        """Register *callback* to be called whenever *event* is published."""
        with self._lock:
            self._subscribers[event].append(callback)

    def unsubscribe(self, event: str, callback: Callable) -> None:
        with self._lock:
            try:
                self._subscribers[event].remove(callback)
            except ValueError:
                pass

    # ── Publishing ────────────────────────────────────────────────────────────

    def publish(self, event: str, data: dict | None = None) -> None:
        """Dispatch *event* to every registered subscriber."""
        data = data or {}
        with self._lock:
            handlers = list(self._subscribers.get(event, []))
        for handler in handlers:
            try:
                handler(data)
            except Exception:
                pass  # never let a bad subscriber crash the publisher

    # ── Built-in event constants ──────────────────────────────────────────────

    # Log events
    LOG_EVENT      = "log"            # data: {level, module, message, thread_id}

    # Thread / module state
    THREAD_STARTED = "thread.started" # data: {module, thread_id}
    THREAD_STOPPED = "thread.stopped" # data: {module, thread_id}
    KILL_ALL       = "kill.all"       # data: {}

    # Stats updates (dashboard counters)
    STAT_UPDATE    = "stat.update"    # data: {key: str, value: int | str}

    # Proxy / token status
    PROXY_DEAD     = "proxy.dead"     # data: {address}
    TOKEN_INVALID  = "token.invalid"  # data: {token_masked, platform}

    # Module results
    ACTION_RESULT  = "action.result"  # data: {module, action, target, result}

    # Notifications
    NOTIFY         = "notify"         # data: {title, message, level}


# ── Global singleton ──────────────────────────────────────────────────────────
bus = EventBus()
