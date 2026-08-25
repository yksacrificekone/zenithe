"""
ZENITH — core/thread_manager.py
Centralized thread registry. Every worker thread spawned by a module registers
here. The global kill switch (top-bar skull button) calls kill_all(). Per-module
stop buttons call kill_module(). UI polls get_active_count() for the status bar.
"""

from __future__ import annotations
import threading
import queue
from dataclasses import dataclass, field
from typing import Callable
from core.event_bus import bus
import core.logger as log


@dataclass
class ManagedThread:
    name:        str
    module:      str
    thread:      threading.Thread
    stop_event:  threading.Event
    started_at:  float = field(default_factory=lambda: __import__("time").time())


class ThreadManager:
    def __init__(self) -> None:
        self._lock    = threading.Lock()
        self._threads: dict[str, ManagedThread] = {}
        self._global_stop = threading.Event()

        bus.subscribe(bus.KILL_ALL, lambda _: self.kill_all())

    # ── Spawning ───────────────────────────────────────────────────────────────

    def spawn(
        self,
        module:   str,
        target:   Callable,
        args:     tuple = (),
        kwargs:   dict  | None = None,
        name:     str   | None = None,
    ) -> ManagedThread | None:
        """Spawn a daemon thread registered under *module*. Returns ManagedThread."""
        if self._global_stop.is_set():
            log.warning("SYSTEM", "Global kill active — rejecting new thread spawn.")
            return None

        stop_event = threading.Event()
        thread_name = name or f"{module}-{id(stop_event) & 0xFFFF:04X}"

        def _wrapper() -> None:
            bus.publish(bus.THREAD_STARTED, {"module": module, "thread_id": thread_name})
            try:
                target(*args, stop_event=stop_event, **(kwargs or {}))
            except Exception as exc:
                log.error(module, f"Thread {thread_name} raised: {exc}")
            finally:
                bus.publish(bus.THREAD_STOPPED, {"module": module, "thread_id": thread_name})
                self._remove(thread_name)

        t = threading.Thread(target=_wrapper, name=thread_name, daemon=True)
        managed = ManagedThread(name=thread_name, module=module,
                                thread=t, stop_event=stop_event)
        with self._lock:
            self._threads[thread_name] = managed
        t.start()
        log.info(module, f"Thread started: {thread_name}")
        return managed

    # ── Control ────────────────────────────────────────────────────────────────

    def kill_module(self, module: str) -> int:
        """Signal all threads belonging to *module* to stop. Returns count stopped."""
        count = 0
        with self._lock:
            targets = [m for m in self._threads.values() if m.module == module]
        for m in targets:
            m.stop_event.set()
            count += 1
        log.warning(module, f"Kill signal sent to {count} thread(s).")
        return count

    def kill_all(self) -> None:
        """Global kill — signal every registered thread to stop."""
        self._global_stop.set()
        with self._lock:
            targets = list(self._threads.values())
        for m in targets:
            m.stop_event.set()
        log.critical("SYSTEM", f"GLOBAL KILL SWITCH — {len(targets)} thread(s) signalled.")

    def reset_global_stop(self) -> None:
        """Clear the global stop flag so new threads can be spawned again."""
        self._global_stop.clear()
        log.info("SYSTEM", "Global kill switch reset.")

    # ── Queries ────────────────────────────────────────────────────────────────

    def get_active_count(self, module: str | None = None) -> int:
        with self._lock:
            if module:
                return sum(1 for m in self._threads.values()
                           if m.module == module and m.thread.is_alive())
            return sum(1 for m in self._threads.values() if m.thread.is_alive())

    def get_active_modules(self) -> list[str]:
        with self._lock:
            return list({m.module for m in self._threads.values() if m.thread.is_alive()})

    def is_module_running(self, module: str) -> bool:
        return self.get_active_count(module) > 0

    def list_threads(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "name":   m.name,
                    "module": m.module,
                    "alive":  m.thread.is_alive(),
                }
                for m in self._threads.values()
            ]

    def _remove(self, name: str) -> None:
        with self._lock:
            self._threads.pop(name, None)


# ── Global singleton ──────────────────────────────────────────────────────────
manager = ThreadManager()
