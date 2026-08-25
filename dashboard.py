"""
ZENITH — ui/dashboard.py
Home dashboard panel. 6 stat cards, live action feed, mini CPU/RAM graph,
quick-launch buttons, ZENITH STATUS banner. All updates via after() polling
from event_bus — never blocks UI thread.
"""

from __future__ import annotations
import time
import threading
import queue
import customtkinter as ctk
import tkinter as tk
from collections import deque
from datetime import datetime

import psutil

from core.event_bus import bus
import core.logger as log
from ui.components.widgets import (
    StatCard, SectionHeader, Badge, AccentButton, GhostButton,
    level_color, module_color, FONT, FONT_S, FONT_L,
    BG, CARD, ACCENT, DANGER, TEXT, SUB, BORDER, OK, WARN,
)

_POLL_MS      = 2000   # stats refresh
_LOG_POLL_MS  = 300    # log queue drain
_GRAPH_POINTS = 60     # data points in CPU/RAM sparklines


class MiniGraph(ctk.CTkCanvas):
    """Simple sparkline canvas for CPU / RAM usage."""

    def __init__(self, parent, color: str = ACCENT, **kwargs):
        super().__init__(parent, bg=CARD, highlightthickness=0,
                         height=48, **kwargs)
        self._color  = color
        self._points: deque[float] = deque([0.0] * _GRAPH_POINTS,
                                            maxlen=_GRAPH_POINTS)

    def push(self, value: float) -> None:
        self._points.append(max(0.0, min(100.0, value)))
        self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        w = self.winfo_width() or 180
        h = self.winfo_height() or 48
        n = len(self._points)
        if n < 2:
            return
        step = w / (n - 1)
        coords: list[float] = []
        for i, v in enumerate(self._points):
            x = i * step
            y = h - (v / 100.0) * (h - 4) - 2
            coords.extend([x, y])
        self.create_line(*coords, fill=self._color, width=1.5, smooth=True)
        # fill under line
        poly = [0, h] + coords + [w, h]
        self.create_polygon(*poly, fill=self._color, stipple="gray12", outline="")


class LiveLogRow(ctk.CTkFrame):
    """Single log line in the live feed."""

    def __init__(self, parent, entry: dict, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)
        ts  = entry.get("timestamp", datetime.utcnow())
        ts_str = ts.strftime("%H:%M:%S") if hasattr(ts, "strftime") else str(ts)
        lvl = entry.get("level", "INFO")
        mod = entry.get("module", "SYSTEM")
        msg = entry.get("message", "")

        ctk.CTkLabel(self, text=ts_str, font=FONT_S,
                     text_color=SUB, width=56, anchor="e").pack(side="left", padx=(4, 6))
        ctk.CTkLabel(self, text=f"[{mod[:8]}]", font=FONT_S,
                     text_color=module_color(mod), width=80).pack(side="left")
        ctk.CTkLabel(self, text=f"[{lvl}]", font=FONT_S,
                     text_color=level_color(lvl), width=70).pack(side="left")
        ctk.CTkLabel(self, text=msg[:90], font=FONT_S,
                     text_color=TEXT, anchor="w").pack(side="left", fill="x", expand=True)


class DashboardPanel(ctk.CTkFrame):
    def __init__(self, parent, user_data: dict, **kwargs):
        super().__init__(parent, fg_color=BG, **kwargs)
        self._user  = user_data
        self._stats = {
            "actions_today":  0,
            "active_threads": 0,
            "tokens_loaded":  0,
            "proxies_live":   0,
            "hits":           0,
            "errors":         0,
        }
        self._log_queue = log.get_log_queue()
        self._build_ui()
        self._subscribe()
        self._schedule_polls()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # ── Top: status banner ─────────────────────────────────────────────────
        banner_row = ctk.CTkFrame(self, fg_color=CARD, corner_radius=0, height=36)
        banner_row.pack(fill="x")
        banner_row.pack_propagate(False)
        self._banner_dot = ctk.CTkLabel(banner_row, text="●", font=FONT_L,
                                         text_color=OK)
        self._banner_dot.pack(side="left", padx=(14, 4))
        self._banner_label = ctk.CTkLabel(banner_row, text="ZENITH STATUS — OPERATIONAL",
                                           font=("JetBrains Mono", 11, "bold"),
                                           text_color=OK)
        self._banner_label.pack(side="left")
        ctk.CTkLabel(banner_row, text=f"Logged in as  {self._user.get('username', '?')}",
                     font=FONT_S, text_color=SUB).pack(side="right", padx=14)

        # ── Main scroll area ───────────────────────────────────────────────────
        scroll = ctk.CTkScrollableFrame(self, fg_color=BG,
                                         scrollbar_button_color=BORDER)
        scroll.pack(fill="both", expand=True, padx=16, pady=8)

        # Stat cards grid
        SectionHeader(scroll, "LIVE METRICS").pack(fill="x", pady=(4, 8))
        grid = ctk.CTkFrame(scroll, fg_color="transparent")
        grid.pack(fill="x")
        grid.columnconfigure((0, 1, 2), weight=1)

        defs = [
            ("Total Actions Today", "actions_today",  "0",  ACCENT),
            ("Active Threads",      "active_threads", "0",  OK),
            ("Tokens Loaded",       "tokens_loaded",  "0",  "#7B2FFF"),
            ("Proxies Live",        "proxies_live",   "0",  ACCENT),
            ("Successful Hits",     "hits",           "0",  OK),
            ("Failures / Errors",   "errors",         "0",  DANGER),
        ]
        self._stat_cards: dict[str, StatCard] = {}
        for idx, (title, key, val, color) in enumerate(defs):
            row, col = divmod(idx, 3)
            card = StatCard(grid, title, val, color=color)
            card.grid(row=row, column=col, padx=6, pady=6, sticky="ew")
            self._stat_cards[key] = card

        # CPU / RAM sparklines
        SectionHeader(scroll, "SYSTEM USAGE").pack(fill="x", pady=(12, 6))
        graphs_row = ctk.CTkFrame(scroll, fg_color=CARD, corner_radius=8)
        graphs_row.pack(fill="x", pady=(0, 6))

        for label, color, attr in [
            ("CPU %", ACCENT, "_cpu_graph"), ("RAM %", "#7B2FFF", "_ram_graph")
        ]:
            col_frame = ctk.CTkFrame(graphs_row, fg_color="transparent")
            col_frame.pack(side="left", fill="both", expand=True, padx=12, pady=10)
            ctk.CTkLabel(col_frame, text=label, font=FONT_S,
                         text_color=color).pack(anchor="w")
            graph = MiniGraph(col_frame, color=color, width=220)
            graph.pack(fill="x", pady=(2, 0))
            setattr(self, attr, graph)

        # Quick launch
        SectionHeader(scroll, "QUICK LAUNCH").pack(fill="x", pady=(12, 6))
        ql_row = ctk.CTkFrame(scroll, fg_color="transparent")
        ql_row.pack(fill="x")
        self._quick_btns: list[ctk.CTkButton] = []
        for lbl, route in [
            ("Discord Nuker",    "discord.account_nuker"),
            ("YT Comment Spam",  "youtube.comment_spammer"),
            ("TikTok Live Spam", "tiktok.live_spammer"),
        ]:
            btn = GhostButton(ql_row, text=lbl, height=36, width=180)
            btn.pack(side="left", padx=6)
            self._quick_btns.append(btn)

        # Live action feed
        SectionHeader(scroll, "LIVE ACTION FEED").pack(fill="x", pady=(12, 6))
        feed_frame = ctk.CTkFrame(scroll, fg_color=CARD, corner_radius=8)
        feed_frame.pack(fill="x")
        self._feed = ctk.CTkScrollableFrame(feed_frame, fg_color="transparent",
                                             height=180,
                                             scrollbar_button_color=BORDER)
        self._feed.pack(fill="both", expand=True, padx=4, pady=4)
        self._feed_rows: deque = deque(maxlen=200)

    # ── Event bus subscriptions ────────────────────────────────────────────────

    def _subscribe(self) -> None:
        bus.subscribe(bus.STAT_UPDATE,    self._on_stat_update)
        bus.subscribe(bus.THREAD_STARTED, self._on_thread_change)
        bus.subscribe(bus.THREAD_STOPPED, self._on_thread_change)

    def _on_stat_update(self, data: dict) -> None:
        key = data.get("key")
        val = data.get("value", 0)
        if key in self._stats:
            self._stats[key] = val

    def _on_thread_change(self, data: dict) -> None:
        from core.thread_manager import manager
        count = manager.get_active_count()
        self._stats["active_threads"] = count

    # ── Polling ────────────────────────────────────────────────────────────────

    def _schedule_polls(self) -> None:
        self._poll_stats()
        self._drain_log_queue()

    def _poll_stats(self) -> None:
        try:
            from core.database import count_live_proxies, count_valid_tokens
            from core.thread_manager import manager

            self._stats["proxies_live"]   = count_live_proxies()
            self._stats["tokens_loaded"]  = count_valid_tokens()
            self._stats["active_threads"] = manager.get_active_count()

            for key, card in self._stat_cards.items():
                card.set_value(str(self._stats.get(key, 0)))

            cpu = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory().percent
            self._cpu_graph.push(cpu)
            self._ram_graph.push(ram)

            # Update status banner
            err = self._stats.get("errors", 0)
            proxies = self._stats.get("proxies_live", 0)
            if err > 20 or proxies == 0:
                color, label = DANGER, "CRITICAL"
            elif err > 5:
                color, label = WARN, "DEGRADED"
            else:
                color, label = OK, "OPERATIONAL"
            self._banner_dot.configure(text_color=color)
            self._banner_label.configure(
                text=f"ZENITH STATUS — {label}", text_color=color)
        except Exception:
            pass
        self.after(_POLL_MS, self._poll_stats)

    def _drain_log_queue(self) -> None:
        drained = 0
        while drained < 20:
            try:
                entry = self._log_queue.get_nowait()
                self._add_feed_row(entry)
                drained += 1
            except queue.Empty:
                break
        self.after(_LOG_POLL_MS, self._drain_log_queue)

    def _add_feed_row(self, entry: dict) -> None:
        row = LiveLogRow(self._feed, entry)
        row.pack(fill="x", pady=1)
        self._feed_rows.append(row)
        if len(self._feed_rows) > 200:
            old = self._feed_rows[0]
            old.destroy()
