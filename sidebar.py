"""
ZENITH — ui/sidebar.py
Left navigation panel. Hierarchical module tree with collapsible categories,
active state highlighting, owner-only admin panel entry. Calls a callback
with a route string when the user selects an entry.
"""

from __future__ import annotations
import customtkinter as ctk
from typing import Callable

BG     = "#0A0A0F"
CARD   = "#13131A"
ACCENT = "#00F5FF"
DANGER = "#FF2244"
TEXT   = "#E8E8F0"
SUB    = "#6B6B80"
BORDER = "#1E1E2E"
FONT   = ("JetBrains Mono", 11)
FONT_S = ("JetBrains Mono", 10)
FONT_C = ("JetBrains Mono", 12, "bold")

# (display_label, route_key, icon_char, children?)
NAV_TREE = [
    ("⬡  DASHBOARD",     "dashboard",          None,  []),
    ("◈  DISCORD",       "discord",             None,  [
        ("  Account Nuker",    "discord.account_nuker"),
        ("  Server Nuker",     "discord.server_nuker"),
        ("  Token Joiner",     "discord.token_joiner"),
    ]),
    ("▷  YOUTUBE",       "youtube",             None,  [
        ("  Comment Spammer",  "youtube.comment_spammer"),
        ("  Live Spammer",     "youtube.live_spammer"),
        ("  Likes & Subs Bot", "youtube.likes_subs"),
    ]),
    ("♪  TIKTOK",        "tiktok",              None,  [
        ("  Comment Spammer",  "tiktok.comment_spammer"),
        ("  Live Spammer",     "tiktok.live_spammer"),
        ("  Follows & Likes",  "tiktok.follows_likes"),
    ]),
    ("⚙  TOOLS",         "tools",               None,  [
        ("  Proxy Manager",    "tools.proxy_manager"),
        ("  Token Manager",    "tools.token_manager"),
        ("  Captcha Config",   "tools.captcha_config"),
        ("  User-Agent Pool",  "tools.useragent_pool"),
    ]),
    ("≡  SETTINGS",      "settings",            None,  []),
    ("⊞  LOGS",          "logs",                None,  []),
]

ADMIN_ENTRY = ("☠  ADMIN PANEL", "admin_panel", None, [])


class SidebarButton(ctk.CTkButton):
    def __init__(self, parent, text: str, route: str,
                 on_nav: Callable[[str], None],
                 indent: bool = False, **kwargs):
        self._route  = route
        self._on_nav = on_nav
        super().__init__(
            parent,
            text=text,
            anchor="w",
            font=FONT_S if indent else FONT,
            fg_color="transparent",
            hover_color=CARD,
            text_color=SUB if indent else TEXT,
            height=34,
            corner_radius=6,
            command=self._handle,
            **kwargs,
        )
        if indent:
            self.configure(width=180, padx=(28, 8))

    def _handle(self) -> None:
        self._on_nav(self._route)

    def set_active(self, active: bool) -> None:
        if active:
            self.configure(fg_color=BORDER, text_color=ACCENT)
        else:
            self.configure(fg_color="transparent",
                           text_color=TEXT if not self._route.count(".") else SUB)


class Sidebar(ctk.CTkFrame):
    def __init__(self, parent, on_navigate: Callable[[str], None],
                 user_role: str = "user", **kwargs):
        super().__init__(parent, fg_color=CARD, corner_radius=0,
                         width=210, **kwargs)
        self.pack_propagate(False)

        self._on_navigate = on_navigate
        self._buttons:  dict[str, SidebarButton] = {}
        self._collapsed: dict[str, bool]         = {}
        self._current   = "dashboard"

        # ── Logo strip ─────────────────────────────────────────────────────────
        logo_frame = ctk.CTkFrame(self, fg_color=BG, corner_radius=0, height=52)
        logo_frame.pack(fill="x")
        logo_frame.pack_propagate(False)
        ctk.CTkLabel(logo_frame, text="Z  E  N  I  T  H", font=("JetBrains Mono", 14, "bold"),
                     text_color=ACCENT).place(relx=0.5, rely=0.5, anchor="center")

        # ── Separator ──────────────────────────────────────────────────────────
        ctk.CTkFrame(self, height=1, fg_color=BORDER).pack(fill="x")

        # ── Scrollable nav area ────────────────────────────────────────────────
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent",
                                         scrollbar_button_color=BORDER,
                                         scrollbar_button_hover_color=ACCENT)
        scroll.pack(fill="both", expand=True, padx=6, pady=6)

        tree = list(NAV_TREE)
        if user_role == "owner":
            tree.append(ADMIN_ENTRY)

        for entry in tree:
            label, route, _icon, children = entry
            if children:
                self._build_category(scroll, label, route, children)
            else:
                btn = SidebarButton(scroll, label, route, self._handle_nav)
                btn.pack(fill="x", pady=1)
                self._buttons[route] = btn

        # ── Bottom separator + version ─────────────────────────────────────────
        ctk.CTkFrame(self, height=1, fg_color=BORDER).pack(fill="x")
        ctk.CTkLabel(self, text="v1.0.0 · ZENITH", font=FONT_S,
                     text_color=SUB).pack(pady=6)

        self._set_active("dashboard")

    # ── Internal builders ─────────────────────────────────────────────────────

    def _build_category(self, parent, label: str, route: str,
                        children: list[tuple]) -> None:
        self._collapsed[route] = False
        container = ctk.CTkFrame(parent, fg_color="transparent")
        container.pack(fill="x", pady=1)

        header = ctk.CTkButton(
            container, text=label, anchor="w",
            font=FONT, fg_color="transparent", hover_color=CARD,
            text_color=TEXT, height=36, corner_radius=6,
            command=lambda r=route: self._toggle(r, child_frame),
        )
        header.pack(fill="x")

        child_frame = ctk.CTkFrame(container, fg_color="transparent")
        child_frame.pack(fill="x")

        for child_label, child_route in children:
            btn = SidebarButton(child_frame, child_label, child_route,
                                self._handle_nav, indent=True)
            btn.pack(fill="x", pady=1)
            self._buttons[child_route] = btn

    def _toggle(self, route: str, frame: ctk.CTkFrame) -> None:
        self._collapsed[route] = not self._collapsed[route]
        if self._collapsed[route]:
            frame.pack_forget()
        else:
            frame.pack(fill="x")

    def _handle_nav(self, route: str) -> None:
        self._set_active(route)
        self._on_navigate(route)

    def _set_active(self, route: str) -> None:
        for r, btn in self._buttons.items():
            btn.set_active(r == route)
        self._current = route

    def set_active_route(self, route: str) -> None:
        self._set_active(route)
