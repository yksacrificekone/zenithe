"""
ZENITH — modules/youtube/likes_subs_bot.py
Like videos and subscribe to channels using rotating OAuth tokens.
Bell notification toggle supported. Tracks per-account success.
"""

from __future__ import annotations
import re
import time
import random
import threading
from typing import Callable

import requests

import core.logger as log
from core.database import log_action

MODULE  = "YOUTUBE.LIKES_SUBS"
YT_BASE = "https://www.googleapis.com/youtube/v3"


def _extract_video_id(url: str) -> str:
    m = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else url.strip()


def _extract_channel_id(url: str, token: str) -> str | None:
    """Resolve a channel URL to a channel ID via the API."""
    # If it's already a UC... ID
    m = re.search(r"UC[A-Za-z0-9_-]{22}", url)
    if m:
        return m.group(0)
    # Try username/handle lookup
    handle = url.rstrip("/").split("/")[-1].lstrip("@")
    r = requests.get(
        f"{YT_BASE}/channels?part=id&forHandle={handle}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    if r.status_code == 200:
        items = r.json().get("items", [])
        if items:
            return items[0]["id"]
    return None


def _like_video(token: str, video_id: str,
                proxy: dict | None) -> tuple[bool, str]:
    url = f"{YT_BASE}/videos/rate"
    try:
        r = requests.post(url,
                          headers={"Authorization": f"Bearer {token}",
                                   "Content-Type": "application/json"},
                          json={"id": video_id, "rating": "like"},
                          proxies=proxy, timeout=15)
        # 204 = success, 400 = already liked, 403 = quota/perms
        return r.status_code in (200, 204), f"HTTP {r.status_code}"
    except Exception as exc:
        return False, str(exc)


def _subscribe(token: str, channel_id: str,
               proxy: dict | None) -> tuple[bool, str]:
    url = f"{YT_BASE}/subscriptions?part=snippet"
    payload = {"snippet": {"resourceId": {"kind": "youtube#channel",
                                           "channelId": channel_id}}}
    try:
        r = requests.post(url,
                          headers={"Authorization": f"Bearer {token}",
                                   "Content-Type": "application/json"},
                          json=payload, proxies=proxy, timeout=15)
        return r.status_code in (200, 201), f"HTTP {r.status_code}"
    except Exception as exc:
        return False, str(exc)


def run_likes_subs(
    oauth_tokens:     list[str],
    video_url:        str | None,
    channel_url:      str | None,
    do_like:          bool,
    do_subscribe:     bool,
    do_bell:          bool,
    count_limit:      int,
    delay_ms:         int,
    result_callback:  Callable[[dict], None],
    stop_event:       threading.Event,
) -> None:
    vid_id  = _extract_video_id(video_url) if video_url else None
    chan_id = None

    tokens_to_use = oauth_tokens[:count_limit]
    done = 0

    for tok in tokens_to_use:
        if stop_event.is_set():
            break

        record = {"account": tok[:14] + "...", "liked": False,
                  "subscribed": False, "status": "pending"}

        # Resolve channel ID per token (handle changes)
        if do_subscribe and channel_url and not chan_id:
            chan_id = _extract_channel_id(channel_url, tok)

        if do_like and vid_id:
            ok, meta = _like_video(tok, vid_id, None)
            record["liked"]  = ok
            if ok:
                log.success(MODULE, f"Liked {vid_id} with token {tok[:12]}...")
            else:
                log.warning(MODULE, f"Like failed: {meta}")

        if do_subscribe and chan_id:
            ok, meta = _subscribe(tok, chan_id, None)
            record["subscribed"] = ok
            if ok:
                log.success(MODULE, f"Subscribed to {chan_id}")
            else:
                log.warning(MODULE, f"Subscribe failed: {meta}")

        record["status"] = "success" if (record["liked"] or record["subscribed"]) else "failed"
        result_callback(record)
        done += 1
        time.sleep(delay_ms / 1000 + random.uniform(0, 0.5))

    log.info(MODULE, f"Done — {done} account(s) actioned.")
    log_action(MODULE, "likes_subs", f"vid:{vid_id} chan:{chan_id}", "complete")
