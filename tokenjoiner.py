"""
ZENITH — modules/discord/token_joiner.py
Section A: Mass-join a Discord server with a token list.
Section B: Generate fresh Discord accounts — register endpoint,
           hCaptcha solve, email verification via temp mail,
           optional SMS phone verification.
"""

from __future__ import annotations
import time
import json
import base64
import random
import string
import threading
from typing import Callable

import requests

import core.logger as log
from core.event_bus import bus
from core.database import SessionLocal, Token, log_action
from utils.proxy_rotator import ProxyRotator
from utils.captcha_solver import CaptchaSolver
from utils.useragent_pool import UAPool

MODULE_JOIN = "DISCORD.TOKEN_JOINER"
MODULE_GEN  = "DISCORD.TOKEN_GEN"
BASE        = "https://discord.com/api/v10"
DISCORD_REG = "https://discord.com/api/v10/auth/register"


def _ua() -> str:
    return UAPool.instance().random()


def _super_props(os_name: str = "Windows", browser: str = "Chrome") -> str:
    props = {
        "os":               os_name,
        "browser":          browser,
        "device":           "",
        "system_locale":    "en-US",
        "browser_version":  "120.0.0.0",
        "os_version":       "10",
        "referrer":         "",
        "referring_domain": "",
        "release_channel":  "stable",
        "client_build_number": 261947,
    }
    return base64.b64encode(json.dumps(props).encode()).decode()


def _headers(token: str | None = None) -> dict:
    h = {
        "Content-Type":       "application/json",
        "User-Agent":         _ua(),
        "X-Super-Properties": _super_props(),
        "X-Discord-Locale":   "en-US",
        "X-Debug-Options":    "bugReporterEnabled",
        "Origin":             "https://discord.com",
        "Referer":            "https://discord.com/",
    }
    if token:
        h["Authorization"] = token
    return h


def _req(method: str, url: str, proxy: dict | None = None,
         token: str | None = None, **kw) -> requests.Response | None:
    for attempt in range(3):
        try:
            r = requests.request(
                method, url,
                headers=_headers(token),
                proxies=proxy,
                timeout=20,
                **kw,
            )
            if r.status_code == 429:
                after = float(r.json().get("retry_after", 2.0))
                time.sleep(after + 0.2)
                continue
            return r
        except requests.RequestException as exc:
            time.sleep(2 ** attempt)
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION A — TOKEN JOINER
# ══════════════════════════════════════════════════════════════════════════════

def _resolve_invite(invite_code: str,
                    proxy: dict | None = None) -> str | None:
    r = _req("GET", f"{BASE}/invites/{invite_code}", proxy)
    return r.json().get("guild", {}).get("id") if r and r.status_code == 200 else None


def join_server(
    token:         str,
    invite_code:   str,
    proxy:         dict | None,
    stop_event:    threading.Event,
) -> dict:
    result = {"token": token[:10] + "...", "status": "pending",
              "member_id": None, "error": None}
    if stop_event.is_set():
        result["status"] = "stopped"
        return result

    r = _req("POST", f"{BASE}/invites/{invite_code}", proxy, token=token, json={})
    if not r:
        result.update({"status": "error", "error": "no_response"})
    elif r.status_code in (200, 201):
        data = r.json()
        result["status"]    = "joined"
        result["member_id"] = data.get("new_member", {}).get("user", {}).get("id")
        log.success(MODULE_JOIN, f"Token joined — member {result['member_id']}")
    elif r.status_code == 401:
        result.update({"status": "invalid_token", "error": "401"})
        log.error(MODULE_JOIN, f"Token invalid (401)")
    elif r.status_code == 403:
        result.update({"status": "banned_or_locked", "error": "403"})
    else:
        result.update({"status": f"error_{r.status_code}", "error": str(r.status_code)})

    log_action(MODULE_JOIN, "join_server", invite_code, result["status"])
    return result


def run_mass_join(
    tokens:          list[str],
    invite_code:     str,
    delay_ms:        int,
    proxy_rotator:   ProxyRotator | None,
    result_callback: Callable[[dict], None],
    stop_event:      threading.Event,
) -> None:
    code = invite_code.replace("https://discord.gg/", "").replace("discord.gg/", "").strip()
    for token in tokens:
        if stop_event.is_set():
            break
        proxy = proxy_rotator.next() if proxy_rotator else None
        res   = join_server(token.strip(), code, proxy, stop_event)
        result_callback(res)
        time.sleep(delay_ms / 1000)


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION B — ACCOUNT GENERATOR
# ══════════════════════════════════════════════════════════════════════════════

class TempMailClient:
    """Minimal interface to mail.tm for ephemeral email accounts."""
    BASE = "https://api.mail.tm"

    def __init__(self):
        self._session = requests.Session()
        self._token   = None
        self._address = None

    def create(self) -> str | None:
        # Get available domain
        r = self._session.get(f"{self.BASE}/domains", timeout=10)
        if not r or r.status_code != 200:
            return None
        domain = r.json().get("hydra:member", [{}])[0].get("domain")
        if not domain:
            return None

        username   = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
        password   = "".join(random.choices(string.ascii_letters + string.digits, k=16))
        address    = f"{username}@{domain}"

        r2 = self._session.post(f"{self.BASE}/accounts", timeout=10,
                                json={"address": address, "password": password})
        if r2.status_code not in (200, 201):
            return None

        r3 = self._session.post(f"{self.BASE}/token", timeout=10,
                                json={"address": address, "password": password})
        if r3.status_code == 200:
            self._token   = r3.json().get("token")
            self._address = address
        return address

    def wait_for_link(self, sender_contains: str = "discord",
                      timeout: int = 120) -> str | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                r = self._session.get(
                    f"{self.BASE}/messages",
                    headers={"Authorization": f"Bearer {self._token}"},
                    timeout=10,
                )
                if r.status_code == 200:
                    for msg in r.json().get("hydra:member", []):
                        if sender_contains.lower() in msg.get("from", {}).get("address", "").lower():
                            mid = msg["id"]
                            rm = self._session.get(
                                f"{self.BASE}/messages/{mid}",
                                headers={"Authorization": f"Bearer {self._token}"},
                                timeout=10,
                            )
                            body = rm.json().get("html", [""])[0]
                            # extract first https link
                            import re
                            match = re.search(r'href="(https://[^"]+discord[^"]+)"', body)
                            if match:
                                return match.group(1)
            except Exception:
                pass
            time.sleep(5)
        return None


def _register_account(
    username:      str,
    email:         str,
    password:      str,
    captcha_token: str,
    proxy:         dict | None,
) -> tuple[bool, str | None, str | None]:
    """
    Returns (success, discord_token, error_message).
    """
    payload = {
        "username":              username,
        "email":                 email,
        "password":              password,
        "captcha_key":           captcha_token,
        "consent":               True,
        "date_of_birth":         "1998-05-15",
        "gift_code_sku_id":      None,
        "promotional_email_opt_in": False,
    }
    r = _req("POST", DISCORD_REG, proxy, json=payload)
    if not r:
        return False, None, "no_response"
    if r.status_code in (200, 201):
        data  = r.json()
        token = data.get("token")
        if token:
            return True, token, None
        return False, None, str(data)
    return False, None, f"HTTP {r.status_code}: {r.text[:200]}"


def generate_account(
    captcha_solver:  CaptchaSolver,
    proxy_rotator:   ProxyRotator,
    username:        str | None = None,
    use_phone:       bool = False,
    sms_api_key:     str = "",
    stop_event:      threading.Event = None,
) -> dict:
    result = {
        "email":          None,
        "username":       None,
        "token":          None,
        "email_verified": False,
        "phone_verified": False,
        "status":         "pending",
    }
    if stop_event and stop_event.is_set():
        result["status"] = "stopped"
        return result

    proxy = proxy_rotator.next()

    # temp mail
    mail = TempMailClient()
    email = mail.create()
    if not email:
        result["status"] = "error:tempmail"
        return result
    result["email"] = email

    uname = username or "".join(
        random.choices(string.ascii_lowercase + string.digits, k=10))
    result["username"] = uname
    password = "".join(random.choices(
        string.ascii_letters + string.digits + "!@#$", k=16))

    # hCaptcha solve
    log.info(MODULE_GEN, f"Solving hCaptcha for {uname}...")
    cap_token = captcha_solver.solve_hcaptcha(
        site_key="4c672d35-0701-42b2-88c3-78380b0db560",
        page_url="https://discord.com/register",
    )
    if not cap_token:
        result["status"] = "error:captcha"
        return result

    ok, token, err = _register_account(uname, email, password, cap_token, proxy)
    if not ok:
        result["status"] = f"error:register:{err}"
        log.error(MODULE_GEN, f"Registration failed: {err}")
        return result

    result["token"]  = token
    log.success(MODULE_GEN, f"Account created: {uname} | {email}")

    # email verify
    log.info(MODULE_GEN, f"Waiting for Discord verification email...")
    verify_link = mail.wait_for_link(timeout=90)
    if verify_link:
        rv = requests.get(verify_link, timeout=15)
        result["email_verified"] = rv.status_code == 200
        log.success(MODULE_GEN, f"Email verified: {result['email_verified']}")
    else:
        log.warning(MODULE_GEN, "Email verification timed out")

    result["status"] = "complete"

    # Save to DB
    with SessionLocal() as session:
        session.add(Token(
            token=token, platform="discord",
            email=email,
            email_verified=result["email_verified"],
            phone_verified=False,
            status="valid",
        ))
        session.commit()

    log_action(MODULE_GEN, "generate_account", uname, "success")
    bus.publish(bus.ACTION_RESULT, {
        "module": MODULE_GEN, "action": "generate",
        "target": uname, "result": "success"
    })
    return result


def run_generation(
    count:           int,
    captcha_solver:  CaptchaSolver,
    proxy_rotator:   ProxyRotator,
    username_prefix: str = "",
    result_callback: Callable[[dict], None] = None,
    stop_event:      threading.Event = None,
) -> None:
    stop_event = stop_event or threading.Event()
    for i in range(count):
        if stop_event.is_set():
            break
        uname = (f"{username_prefix}{i}" if username_prefix
                 else None)
        res = generate_account(captcha_solver, proxy_rotator,
                               username=uname, stop_event=stop_event)
        if result_callback:
            result_callback(res)
        time.sleep(random.uniform(2.0, 5.0))
