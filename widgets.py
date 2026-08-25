"""
ZENITH — ui/components/widgets.py
Reusable CustomTkinter widgets matching the ZENITH aesthetic.
Stat cards, progress bars, toggle switches, data tables, log rows,
badge labels, confirmation modals. Import these everywhere — no duplication.
"""

from __future__ import annotations
import customtkinter as ctk
from tkinter import ttk
import tkinter as tk
from typing import Callable

# ── Palette ───────────────────────────────────────────────────────────────────
BG     = "#0A0A0F"
CARD   = "#13131A"
ACCENT = "#00F5FF"
DANGER = "#FF2244"
TEXT   = "#E8E8F0"
SUB    = "#6B6B80"
OK     = "#00FF88"
WARN   = "#FFB800"
BORDER = "#1E1E2E"
FONT   = ("JetBrains Mono", 12)
FONT_S = ("JetBrains Mono", 10)
FONT_L = ("JetBrains Mono", 15, "bold")
FONT_H = ("JetBrains Mono", 18, "bold")


# ── Stat Card ─────────────────────────────────────────────────────────────────

class StatCard(ctk.CTkFrame):
    """Single metric card with a label, large value, and optional sub-label."""

    def __init__(self, parent, title: str, value: str = "0",
                 color: str = ACCENT, sub: str = "", **kwargs):
        super().__init__(parent, fg_color=CARD, corner_radius=8,
                         border_width=1, border_color=BORDER, **kwargs)
        self._color = color

        ctk.CTkLabel(self, text=title.upper(), font=FONT_S,
                     text_color=SUB).pack(anchor="w", padx=14, pady=(12, 0))
        self._val_label = ctk.CTkLabel(self, text=value, font=FONT_H, text_color=color)
        self._val_label.pack(anchor="w", padx=14, pady=(2, 0))
        if sub:
            ctk.CTkLabel(self, text=sub, font=FONT_S, text_color=SUB
                         ).pack(anchor="w", padx=14, pady=(0, 10))
        else:
            ctk.CTkLabel(self, text="", font=FONT_S).pack(pady=(0, 6))

    def set_value(self, value: str, color: str | None = None) -> None:
        self._val_label.configure(text=value,
                                  text_color=color or self._color)


# ── Section Header ────────────────────────────────────────────────────────────

class SectionHeader(ctk.CTkFrame):
    def __init__(self, parent, text: str, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)
        ctk.CTkLabel(self, text=text, font=FONT_L,
                     text_color=ACCENT).pack(side="left", padx=4)
        sep = ctk.CTkFrame(self, height=1, fg_color=BORDER)
        sep.pack(side="left", fill="x", expand=True, padx=(8, 0), pady=12)


# ── Badged Label ──────────────────────────────────────────────────────────────

class Badge(ctk.CTkLabel):
    COLORS = {
        "owner":   ("#7B2FFF", TEXT),
        "admin":   (ACCENT,    BG),
        "user":    (BORDER,    TEXT),
        "success": (OK,        BG),
        "warning": (WARN,      BG),
        "error":   (DANGER,    TEXT),
        "info":    (ACCENT,    BG),
        "live":    (OK,        BG),
        "dead":    (DANGER,    TEXT),
        "slow":    (WARN,      BG),
    }

    def __init__(self, parent, tag: str, **kwargs):
        bg, fg = self.COLORS.get(tag.lower(), (BORDER, TEXT))
        super().__init__(parent, text=f"  {tag.upper()}  ", font=FONT_S,
                         fg_color=bg, text_color=fg, corner_radius=4, **kwargs)


# ── Log Row ───────────────────────────────────────────────────────────────────

LEVEL_COLORS = {
    "DEBUG":    SUB,
    "INFO":     ACCENT,
    "SUCCESS":  OK,
    "WARNING":  WARN,
    "ERROR":    DANGER,
    "CRITICAL": DANGER,
}

MODULE_COLORS = {
    "DISCORD":  "#5865F2",
    "YOUTUBE":  "#FF0000",
    "TIKTOK":   "#69C9D0",
    "SYSTEM":   SUB,
}

def level_color(level: str) -> str:
    return LEVEL_COLORS.get(level.upper(), TEXT)

def module_color(module: str) -> str:
    base = module.split(".")[0].upper()
    return MODULE_COLORS.get(base, SUB)


# ── Confirm Modal ─────────────────────────────────────────────────────────────

class ConfirmModal(ctk.CTkToplevel):
    """
    Blocking modal that requires the user to type a confirmation phrase.
    Usage:
        m = ConfirmModal(parent, phrase="NUKE", on_confirm=callback)
    """

    def __init__(self, parent, phrase: str, title: str = "Confirm Action",
                 message: str = "", on_confirm: Callable | None = None):
        super().__init__(parent)
        self.title(title)
        self.geometry("420x260")
        self.configure(fg_color=BG)
        self.resizable(False, False)
        self.grab_set()

        self._phrase     = phrase.upper()
        self._on_confirm = on_confirm

        ctk.CTkLabel(self, text=title, font=FONT_L,
                     text_color=DANGER).pack(pady=(24, 4))
        msg = message or f"Type  {phrase}  to confirm this action."
        ctk.CTkLabel(self, text=msg, font=FONT_S,
                     text_color=SUB, wraplength=360).pack(pady=(0, 16))

        self._entry = ctk.CTkEntry(self, placeholder_text=phrase,
                                   font=FONT, width=280, justify="center")
        self._entry.pack(pady=(0, 16))

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack()
        ctk.CTkButton(row, text="CANCEL", font=FONT_S, fg_color=BORDER,
                      hover_color=CARD, width=120,
                      command=self.destroy).pack(side="left", padx=8)
        ctk.CTkButton(row, text="CONFIRM", font=FONT_S, fg_color=DANGER,
                      hover_color="#CC001A", width=120,
                      command=self._check).pack(side="left", padx=8)

    def _check(self) -> None:
        if self._entry.get().upper() == self._phrase:
            self.destroy()
            if self._on_confirm:
                self._on_confirm()
        else:
            self._entry.configure(border_color=DANGER)
            self._entry.delete(0, "end")


# ── Data Table (ttk.Treeview wrapped) ────────────────────────────────────────

class ZenithTable(ctk.CTkFrame):
    """
    Sortable, resizable data table with ZENITH dark styling applied via ttk.Style.
    Columns: list of (col_id, display_name, width)
    """

    def __init__(self, parent, columns: list[tuple[str, str, int]], **kwargs):
        super().__init__(parent, fg_color=CARD, corner_radius=8, **kwargs)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Zenith.Treeview",
                        background=CARD, foreground=TEXT,
                        fieldbackground=CARD, rowheight=28,
                        font=("JetBrains Mono", 10))
        style.configure("Zenith.Treeview.Heading",
                        background=BG, foreground=ACCENT,
                        font=("JetBrains Mono", 10, "bold"),
                        relief="flat")
        style.map("Zenith.Treeview",
                  background=[("selected", "#1E1E3A")],
                  foreground=[("selected", ACCENT)])

        col_ids = [c[0] for c in columns]
        self._tree = ttk.Treeview(self, columns=col_ids, show="headings",
                                  style="Zenith.Treeview", selectmode="browse")

        for col_id, display, width in columns:
            self._tree.heading(col_id, text=display,
                               command=lambda c=col_id: self._sort(c, False))
            self._tree.column(col_id, width=width, minwidth=40)

        sb = ctk.CTkScrollbar(self, command=self._tree.yview)
        self._tree.configure(yscrollcommand=sb.set)

        self._tree.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        sb.pack(side="right", fill="y", padx=(0, 4), pady=8)

        self._sort_reverse: dict[str, bool] = {}

    def insert(self, values: tuple, tag: str = "") -> str:
        iid = self._tree.insert("", "end", values=values, tags=(tag,))
        self._tree.tag_configure("error",   foreground=DANGER)
        self._tree.tag_configure("success", foreground=OK)
        self._tree.tag_configure("warning", foreground=WARN)
        return iid

    def clear(self) -> None:
        self._tree.delete(*self._tree.get_children())

    def _sort(self, col: str, reverse: bool) -> None:
        data = [(self._tree.set(k, col), k) for k in self._tree.get_children()]
        try:
            data.sort(key=lambda x: float(x[0].replace(",", "")), reverse=reverse)
        except ValueError:
            data.sort(key=lambda x: x[0].lower(), reverse=reverse)
        for idx, (_, k) in enumerate(data):
            self._tree.move(k, "", idx)
        toggle = not self._sort_reverse.get(col, False)
        self._sort_reverse[col] = toggle
        self._tree.heading(col, command=lambda: self._sort(col, toggle))

    @property
    def tree(self) -> ttk.Treeview:
        return self._tree


# ── Animated Button ───────────────────────────────────────────────────────────

class DangerButton(ctk.CTkButton):
    def __init__(self, parent, **kwargs):
        kwargs.setdefault("fg_color", DANGER)
        kwargs.setdefault("hover_color", "#CC001A")
        kwargs.setdefault("font", FONT)
        super().__init__(parent, **kwargs)


class AccentButton(ctk.CTkButton):
    def __init__(self, parent, **kwargs):
        kwargs.setdefault("fg_color", ACCENT)
        kwargs.setdefault("hover_color", "#00C8D4")
        kwargs.setdefault("text_color", BG)
        kwargs.setdefault("font", FONT)
        super().__init__(parent, **kwargs)


class GhostButton(ctk.CTkButton):
    def __init__(self, parent, **kwargs):
        kwargs.setdefault("fg_color", BORDER)
        kwargs.setdefault("hover_color", "#2A2A3E")
        kwargs.setdefault("text_color", TEXT)
        kwargs.setdefault("font", FONT)
        super().__init__(parent, **kwargs)


# ── Progress Row ──────────────────────────────────────────────────────────────

class ProgressRow(ctk.CTkFrame):
    def __init__(self, parent, label: str, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)
        ctk.CTkLabel(self, text=label, font=FONT_S,
                     text_color=TEXT, width=160, anchor="w").pack(side="left")
        self._bar = ctk.CTkProgressBar(self, progress_color=ACCENT,
                                        fg_color=BORDER, height=8, width=240)
        self._bar.set(0)
        self._bar.pack(side="left", padx=8)
        self._pct = ctk.CTkLabel(self, text="0%", font=FONT_S,
                                  text_color=ACCENT, width=40)
        self._pct.pack(side="left")

    def set_progress(self, fraction: float) -> None:
        self._bar.set(max(0.0, min(1.0, fraction)))
        self._pct.configure(text=f"{int(fraction * 100)}%")


# ── Token Input Block ─────────────────────────────────────────────────────────

class TokenInputBlock(ctk.CTkFrame):
    """Multi-line token paste area + file loader + valid/invalid counter."""

    def __init__(self, parent, on_load_file: Callable | None = None, **kwargs):
        super().__init__(parent, fg_color=CARD, corner_radius=8, **kwargs)

        ctk.CTkLabel(self, text="TOKENS", font=FONT_S,
                     text_color=ACCENT).pack(anchor="w", padx=12, pady=(10, 2))
        self._text = ctk.CTkTextbox(self, height=120, font=FONT_S,
                                     fg_color=BG, text_color=TEXT,
                                     border_color=BORDER, border_width=1)
        self._text.pack(fill="x", padx=12, pady=(0, 6))

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(0, 10))

        GhostButton(row, text="LOAD FILE", width=110,
                    command=on_load_file or self._load_file).pack(side="left")
        self._counter = ctk.CTkLabel(row, text="0 tokens", font=FONT_S,
                                      text_color=SUB)
        self._counter.pack(side="left", padx=12)

    def get_tokens(self) -> list[str]:
        raw = self._text.get("1.0", "end").strip()
        tokens = [t.strip() for t in raw.splitlines() if t.strip()]
        self._counter.configure(text=f"{len(tokens)} token(s)")
        return tokens

    def _load_file(self) -> None:
        from tkinter import filedialog
        path = filedialog.askopenfilename(filetypes=[("Text files", "*.txt"), ("All", "*.*")])
        if path:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                content = fh.read()
            self._text.delete("1.0", "end")
            self._text.insert("1.0", content)
            count = len([l for l in content.splitlines() if l.strip()])
            self._counter.configure(text=f"{count} token(s) loaded")
