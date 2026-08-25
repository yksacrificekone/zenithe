"""
ZENITH — modules/youtube/live_spammer.py
Flood an active YouTube live stream chat. Polls liveChatId from the
video/broadcast API, posts via liveChatMessages endpoint with rotating
OAuth tokens. Selenium fallback path if API quota is unavailable.
"""

from __future__ import annotations
import time
import random
import threading
from typing import Callable

import requests

import core.logger as log
from core.event_bus import bus
from core.database import log_action
from utils.proxy_rotator import ProxyRotator

MODULE  = "YOUTUBE.LIVE_SPAM"
YT_BASE = "https://www.googleapis.com/youtube/v3"


def _get_live_chat_id(oauth_token: str, video_id: str,
                       proxy: dict | None) -> str | None:
    url = f"{YT_BASE}/videos?part=liveStreamingDetails&id={video_id}"
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {oauth_token}"},
                         proxies=proxy, timeout=15)
        if r.status_code == 200:
            items = r.json().get("items", [])
            if items:
                return (items[0]
                        .get("liveStreamingDetails", {})
                        .get("activeLiveChatId"))
    except Exception as exc:
        log.warning(MODULE, f"live_chat_id fetch failed: {exc}")
    return None


def _post_chat_message(oauth_token: str, live_chat_id: str,
                        text: str, proxy: dict | None) -> tuple[bool, str]:
    url     = f"{YT_BASE}/liveChat/messages?part=snippet"
    payload = {
        "snippet": {
            "type":          "textMessageEvent",
            "liveChatId":    live_chat_id,
            "textMessageDetails": {"messageText": text},
        }
    }
    try:
        r = requests.post(url,
                          headers={"Authorization": f"Bearer {oauth_token}",
                                   "Content-Type":  "application/json"},
                          json=payload, proxies=proxy, timeout=15)
        return r.status_code in (200, 201), f"HTTP {r.status_code}"
    except Exception as exc:
        return False, str(exc)


def flood_live_chat(
    oauth_tokens:    list[str],
    video_url:       str,
    messages:        list[str],
    mode:            str,          # "sequential" | "random" | "rapid"
    msgs_per_minute: int,
    duration_mins:   float | None,
    emoji_flood:     bool,
    result_callback: Callable[[dict], None],
    stop_event:      threading.Event,
) -> None:
    vid_id = video_url.split("v=")[-1].split("&")[0].strip()
    delay  = 60.0 / max(1, msgs_per_minute)

    # resolve liveChatId using first token
    log.info(MODULE, f"Resolving liveChatId for {vid_id}...")
    live_chat_id = None
    for tok in oauth_tokens:
        live_chat_id = _get_live_chat_id(tok, vid_id, None)
        if live_chat_id:
            break

    if not live_chat_id:
        log.error(MODULE, "Could not resolve liveChatId — is the stream live?")
        return

    log.success(MODULE, f"liveChatId: {live_chat_id}")

    token_idx = 0
    msg_idx   = 0
    sent      = 0
    deadline  = (time.time() + duration_mins * 60) if duration_mins else None
    EMOJIS    = ["🔥", "💯", "⚡", "👑", "💥", "🚀", "😈", "🎯"]

    while not stop_event.is_set():
        if deadline and time.time() >= deadline:
            break

        tok   = oauth_tokens[token_idx % len(oauth_tokens)]
        proxy = None  # per-token proxy could be added via ProxyRotator here

        if mode == "sequential":
            msg = messages[msg_idx % len(messages)]
            msg_idx += 1
        elif mode == "rapid":
            msg = messages[0]
        else:
            msg = random.choice(messages)

        if emoji_flood:
            msg = msg + " " + "".join(random.choices(EMOJIS, k=random.randint(2, 5)))

        ok, meta = _post_chat_message(tok, live_chat_id, msg, proxy)
        result_callback({"sent": ok, "message": msg[:60], "meta": meta})

        if ok:
            sent += 1
            log.success(MODULE, f"[LIVE] {msg[:50]}")
        else:
            log.warning(MODULE, f"[LIVE] failed: {meta}")
            if "403" in meta:
                token_idx += 1  # quota rotate

        time.sleep(delay + random.uniform(0, 0.4))

    log.info(MODULE, f"Live spam ended — {sent} message(s) sent.")
    log_action(MODULE, "live_spam", vid_id, "complete")
