"""
ZENITH — modules/youtube/comment_spammer.py
Post comments on YouTube videos using rotating OAuth tokens or Selenium
with cookie injection. Handles API quota rotation, comment variation,
reply-to support, and daily quota tracking.
"""

from __future__ import annotations
import re
import time
import random
import threading
from typing import Callable

import requests

import core.logger as log
from core.event_bus import bus
from core.database import log_action
from utils.proxy_rotator import ProxyRotator

MODULE   = "YOUTUBE.COMMENT_SPAM"
YT_BASE  = "https://www.googleapis.com/youtube/v3"

# Synonym bank for light variation to dodge exact-match filters
_SYNS = {
    "great": ["awesome", "amazing", "fantastic", "brilliant"],
    "love":  ["adore", "enjoy", "appreciate", "like"],
    "good":  ["solid", "excellent", "nice", "quality"],
    "video": ["content", "vid", "upload", "clip"],
}


def _vary_message(message: str) -> str:
    """Lightweight synonym swap to produce unique comment strings."""
    for word, alts in _SYNS.items():
        if word in message.lower():
            message = re.sub(
                re.escape(word), random.choice(alts), message, count=1, flags=re.I)
    return message


def _extract_video_id(url_or_id: str) -> str:
    m = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url_or_id)
    return m.group(1) if m else url_or_id.strip()


# ── API-based comment post ────────────────────────────────────────────────────

def _post_comment_api(
    oauth_token:  str,
    video_id:     str,
    text:         str,
    reply_to:     str | None = None,
    proxy:        dict | None = None,
) -> tuple[bool, str]:
    headers = {
        "Authorization": f"Bearer {oauth_token}",
        "Content-Type":  "application/json",
    }
    if reply_to:
        # reply to existing comment thread
        url     = f"{YT_BASE}/comments?part=snippet"
        payload = {
            "snippet": {
                "parentId":  reply_to,
                "textOriginal": text,
            }
        }
    else:
        url     = f"{YT_BASE}/commentThreads?part=snippet"
        payload = {
            "snippet": {
                "videoId": video_id,
                "topLevelComment": {
                    "snippet": {"textOriginal": text}
                },
            }
        }
    try:
        r = requests.post(url, headers=headers, json=payload,
                          proxies=proxy, timeout=20)
        if r.status_code in (200, 201):
            cid = (r.json().get("snippet", {})
                   .get("topLevelComment", {})
                   .get("id") or r.json().get("id", ""))
            return True, cid
        if r.status_code == 403:
            err = r.json().get("error", {}).get("message", "quota")
            return False, f"403:{err}"
        return False, f"HTTP {r.status_code}"
    except Exception as exc:
        return False, str(exc)


def _get_top_comment_id(oauth_token: str, video_id: str,
                         proxy: dict | None) -> str | None:
    url = (f"{YT_BASE}/commentThreads?part=snippet&videoId={video_id}"
           f"&order=relevance&maxResults=1")
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {oauth_token}"},
                         proxies=proxy, timeout=15)
        if r.status_code == 200:
            items = r.json().get("items", [])
            if items:
                return items[0]["snippet"]["topLevelComment"]["id"]
    except Exception:
        pass
    return None


# ── Per-token quota tracker ───────────────────────────────────────────────────

class _QuotaTracker:
    """
    Tracks per-OAuth-token daily comment quota.
    YouTube Data API v3 quota unit cost for comment insert = 50 units.
    Default daily quota = 10 000 units → ~200 comments per token.
    """
    COST_PER_COMMENT = 50
    DAILY_QUOTA      = 10_000

    def __init__(self):
        self._used: dict[str, int] = {}

    def can_use(self, token_id: str) -> bool:
        return self._used.get(token_id, 0) < self.DAILY_QUOTA

    def consume(self, token_id: str) -> None:
        self._used[token_id] = self._used.get(token_id, 0) + self.COST_PER_COMMENT

    def reset(self, token_id: str) -> None:
        self._used.pop(token_id, None)


_tracker = _QuotaTracker()


# ── Main worker ───────────────────────────────────────────────────────────────

def spam_comments(
    oauth_tokens:    list[str],
    video_urls:      list[str],
    messages:        list[str],
    count_per_video: int,
    delay_ms:        int,
    proxy_rotator:   ProxyRotator | None,
    reply_random:    bool,
    like_own:        bool,
    result_callback: Callable[[dict], None],
    stop_event:      threading.Event,
) -> None:
    token_index  = 0
    total_sent   = 0

    for url in video_urls:
        if stop_event.is_set():
            break
        vid_id = _extract_video_id(url)
        log.info(MODULE, f"Targeting video: {vid_id}")
        sent_this_video = 0

        for _ in range(count_per_video):
            if stop_event.is_set():
                break

            # rotate tokens by quota
            exhausted_count = 0
            while exhausted_count < len(oauth_tokens):
                tok = oauth_tokens[token_index % len(oauth_tokens)]
                tok_id = tok[:16]
                if _tracker.can_use(tok_id):
                    break
                token_index   += 1
                exhausted_count += 1
            else:
                log.error(MODULE, "All tokens exhausted (quota). Stopping.")
                break

            tok   = oauth_tokens[token_index % len(oauth_tokens)]
            tok_id = tok[:16]
            proxy = proxy_rotator.next() if proxy_rotator else None
            msg   = _vary_message(random.choice(messages))

            reply_id = None
            if reply_random:
                reply_id = _get_top_comment_id(tok, vid_id, proxy)

            ok, meta = _post_comment_api(tok, vid_id, msg, reply_id, proxy)

            res = {
                "account": tok_id + "...",
                "video":   vid_id,
                "comment": msg[:60],
                "status":  "success" if ok else "failed",
                "meta":    meta,
            }
            result_callback(res)

            if ok:
                _tracker.consume(tok_id)
                sent_this_video += 1
                total_sent      += 1
                bus.publish(bus.STAT_UPDATE, {"key": "hits",
                                               "value": total_sent})
                log.success(MODULE, f"Comment posted [{vid_id}]: {msg[:40]}...")
            else:
                if meta.startswith("403:"):
                    token_index += 1   # rotate away from quota-exhausted token
                log.warning(MODULE, f"Comment failed [{vid_id}]: {meta}")
                bus.publish(bus.STAT_UPDATE, {"key": "errors",
                                               "value": total_sent})

            time.sleep(delay_ms / 1000)
            if delay_ms < 500:
                time.sleep(random.uniform(0, 0.3))

        log.info(MODULE, f"Video {vid_id}: {sent_this_video} comment(s) posted.")

    log.success(MODULE, f"Run complete — {total_sent} total comment(s).")
    log_action(MODULE, "comment_spam",
               f"{len(video_urls)} video(s)", "complete")
