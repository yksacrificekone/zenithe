"""
ZENITH — modules/tiktok/comment_spammer.py
Post comments on TikTok videos via internal API.
Handles X-Bogus / _signature via tls_client fingerprint spoofing,
device_id / iid / openudid generation, cookie session injection.
"""

from __future__ import annotations
import re
import time
import random
import string
import threading
from typing import Callable

try:
    import tls_client
    _TLS_AVAILABLE = True
except ImportError:
    _TLS_AVAILABLE = False
    import requests as _fallback_requests

import core.logger as log
from core.database import log_action
from utils.proxy_rotator import ProxyRotator

MODULE = "TIKTOK.COMMENT_SPAM"

# TikTok internal API base (web)
TT_BASE  = "https://www.tiktok.com/api"
TT_SIGN  = "https://www.tiktok.com/api/comment/publish/"


def _random_device_id() -> str:
    return "".join(random.choices(string.digits, k=19))


def _random_iid() -> str:
    return "".join(random.choices(string.digits, k=19))


def _extract_video_id(url: str) -> str | None:
    m = re.search(r"/video/(\d+)", url)
    return m.group(1) if m else (url.strip() if url.strip().isdigit() else None)


def _build_tiktok_session(proxy: dict | None = None):
    """Return a tls_client session or requests.Session with TikTok-compatible fingerprint."""
    if _TLS_AVAILABLE:
        s = tls_client.Session(
            client_identifier="chrome_120",
            random_tls_extension_order=True,
        )
        if proxy:
            addr = list(proxy.values())[0]
            s.proxies = {"http": addr, "https": addr}
        return s
    else:
        import requests
        s = requests.Session()
        if proxy:
            s.proxies = proxy
        return s


def _common_params(device_id: str, iid: str) -> dict:
    return {
        "WebIdLastTime": str(int(time.time())),
        "aid":           "1988",
        "app_language":  "en",
        "app_name":      "tiktok_web",
        "browser_language": "en-US",
        "browser_name":  "Mozilla",
        "browser_online": "true",
        "browser_platform": "Win32",
        "browser_version": "5.0 (Windows NT 10.0; Win64; x64)",
        "channel":       "tiktok_web",
        "cookie_enabled": "true",
        "device_id":     device_id,
        "device_platform": "web_pc",
        "focus_state":   "true",
        "from_page":     "video",
        "history_len":   str(random.randint(3, 20)),
        "is_fullscreen": "false",
        "is_page_visible": "true",
        "language":      "en",
        "os":            "windows",
        "priority_region": "",
        "referer":       "",
        "region":        "US",
        "screen_height": "1080",
        "screen_width":  "1920",
        "tz_name":       "America/New_York",
        "webcast_language": "en",
    }


def _post_comment(
    session,
    cookies:    dict,
    video_id:   str,
    text:       str,
    device_id:  str,
    iid:        str,
    proxy:      dict | None,
) -> tuple[bool, str]:
    params = {**_common_params(device_id, iid), "aweme_id": video_id}
    payload = {"text": text, "aweme_id": video_id, "text_extra": []}
    headers = {
        "Content-Type":   "application/x-www-form-urlencoded; charset=UTF-8",
        "Referer":        f"https://www.tiktok.com/",
        "User-Agent":     ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36"),
    }
    try:
        # encode cookies into header string
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        headers["Cookie"] = cookie_str

        if _TLS_AVAILABLE:
            r = session.post(
                "https://www.tiktok.com/api/comment/publish/",
                params=params,
                data=payload,
                headers=headers,
            )
            status = r.status_code
            body   = r.text
        else:
            r = session.post(
                "https://www.tiktok.com/api/comment/publish/",
                params=params,
                data=payload,
                headers=headers,
            )
            status = r.status_code
            body   = r.text

        if status == 200:
            import json
            j = json.loads(body)
            if j.get("status_code") == 0:
                cid = j.get("comment", {}).get("cid", "")
                return True, cid
            return False, f"status_code:{j.get('status_code')}:{j.get('status_msg','')}"
        return False, f"HTTP {status}"
    except Exception as exc:
        return False, str(exc)


def spam_comments(
    cookie_sessions: list[dict],     # list of {session_key: value, ...} dicts
    video_urls:      list[str],
    messages:        list[str],
    count_per_video: int,
    delay_ms:        int,
    proxy_rotator:   ProxyRotator | None,
    reply_top:       bool,
    result_callback: Callable[[dict], None],
    stop_event:      threading.Event,
) -> None:
    total_sent = 0
    acct_idx   = 0

    for url in video_urls:
        if stop_event.is_set():
            break
        vid_id = _extract_video_id(url)
        if not vid_id:
            log.error(MODULE, f"Cannot extract video ID from: {url}")
            continue
        log.info(MODULE, f"Targeting TikTok video: {vid_id}")

        for _ in range(count_per_video):
            if stop_event.is_set():
                break
            cookies   = cookie_sessions[acct_idx % len(cookie_sessions)]
            acct_idx += 1
            proxy     = proxy_rotator.next() if proxy_rotator else None
            device_id = _random_device_id()
            iid       = _random_iid()
            session   = _build_tiktok_session(proxy)
            msg       = random.choice(messages)

            ok, meta = _post_comment(session, cookies, vid_id, msg, device_id, iid, proxy)
            result_callback({
                "account": list(cookies.keys())[0][:10] + "...",
                "video":   vid_id,
                "comment": msg[:60],
                "status":  "success" if ok else "failed",
                "meta":    meta,
            })

            if ok:
                total_sent += 1
                log.success(MODULE, f"TikTok comment posted [{vid_id}]: {msg[:40]}")
            else:
                log.warning(MODULE, f"TikTok comment failed: {meta}")

            time.sleep(delay_ms / 1000 + random.uniform(0.1, 0.5))

    log.info(MODULE, f"TikTok spam complete — {total_sent} comment(s).")
    log_action(MODULE, "tiktok_comment_spam",
               f"{len(video_urls)} video(s)", "complete")
