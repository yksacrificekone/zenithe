"""
ZENITH — core/auth.py
Authentication layer. Bcrypt password hashing, session token generation,
login / signup / owner bootstrap, account lockout after 5 failed attempts.
"""

from __future__ import annotations
import os
import secrets
import hashlib
from datetime import datetime

import bcrypt
from cryptography.fernet import Fernet

from core.database import (
    SessionLocal, User, has_any_user, get_user_by_username,
    init_db
)
import core.logger as log

# ── Encryption key for session tokens in config ───────────────────────────────
_KEY_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".zenith.key")

def _load_or_create_fernet() -> Fernet:
    if os.path.exists(_KEY_PATH):
        with open(_KEY_PATH, "rb") as fh:
            key = fh.read().strip()
    else:
        key = Fernet.generate_key()
        with open(_KEY_PATH, "wb") as fh:
            fh.write(key)
        os.chmod(_KEY_PATH, 0o600)
    return Fernet(key)

_fernet = _load_or_create_fernet()


# ── Password helpers ──────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


# ── Session tokens ────────────────────────────────────────────────────────────

def generate_session_token(user_id: int, username: str) -> str:
    raw = f"{user_id}:{username}:{secrets.token_hex(32)}"
    return _fernet.encrypt(raw.encode()).decode()


def decode_session_token(token: str) -> dict | None:
    try:
        raw = _fernet.decrypt(token.encode()).decode()
        user_id_s, username, _ = raw.split(":", 2)
        return {"user_id": int(user_id_s), "username": username}
    except Exception:
        return None


# ── Bootstrap ─────────────────────────────────────────────────────────────────

def needs_owner_setup() -> bool:
    init_db()
    return not has_any_user()


def create_owner(username: str, password: str, email: str = "") -> User:
    with SessionLocal() as session:
        if session.query(User).filter_by(role="owner").first():
            raise RuntimeError("Owner account already exists.")
        owner = User(
            username=username,
            password_hash=hash_password(password),
            role="owner",
            email=email or None,
            created_at=datetime.utcnow(),
        )
        session.add(owner)
        session.commit()
        session.refresh(owner)
        log.success("AUTH", f"Owner account '{username}' created.")
        return owner


def create_user(username: str, password: str, email: str = "",
                role: str = "user") -> tuple[bool, str]:
    with SessionLocal() as session:
        if session.query(User).filter_by(username=username).first():
            return False, "Username already taken."
        if len(username) < 3:
            return False, "Username must be at least 3 characters."
        if len(password) < 8:
            return False, "Password must be at least 8 characters."
        user = User(
            username=username,
            password_hash=hash_password(password),
            role=role,
            email=email or None,
        )
        session.add(user)
        session.commit()
        log.success("AUTH", f"User '{username}' registered.")
        return True, "Account created successfully."


# ── Login ─────────────────────────────────────────────────────────────────────

MAX_FAIL = 5

def login(username: str, password: str, remember_me: bool = False) -> tuple[bool, str, dict | None]:
    """
    Returns (success, message, user_data_dict | None).
    user_data_dict keys: id, username, role, session_token
    """
    with SessionLocal() as session:
        user: User | None = session.query(User).filter_by(username=username).first()

        if not user:
            log.warning("AUTH", f"Login attempt for unknown user '{username}'.")
            return False, "Invalid credentials.", None

        if user.banned:
            return False, "Account banned. Contact administrator.", None

        if user.fail_count >= MAX_FAIL:
            return False, f"Account locked after {MAX_FAIL} failed attempts. Contact owner.", None

        if not verify_password(password, user.password_hash):
            user.fail_count += 1
            session.commit()
            remaining = MAX_FAIL - user.fail_count
            log.warning("AUTH", f"Failed login for '{username}'. {remaining} attempt(s) remaining.")
            if user.fail_count >= MAX_FAIL:
                return False, "Too many failed attempts — account locked.", None
            return False, f"Invalid credentials. {remaining} attempt(s) remaining.", None

        # success
        user.fail_count = 0
        user.last_login = datetime.utcnow()
        token = generate_session_token(user.id, user.username) if remember_me else None
        if token:
            user.session_token = token
        session.commit()

        data = {
            "id":            user.id,
            "username":      user.username,
            "role":          user.role,
            "session_token": token,
        }
        log.success("AUTH", f"User '{username}' logged in.")
        return True, "Login successful.", data


def login_with_session_token(token: str) -> tuple[bool, dict | None]:
    decoded = decode_session_token(token)
    if not decoded:
        return False, None
    with SessionLocal() as session:
        user = session.query(User).filter_by(
            id=decoded["user_id"], username=decoded["username"],
            session_token=token
        ).first()
        if not user or user.banned:
            return False, None
        user.last_login = datetime.utcnow()
        session.commit()
        return True, {"id": user.id, "username": user.username, "role": user.role}


# ── Admin actions (owner only) ────────────────────────────────────────────────

def unlock_account(target_username: str) -> bool:
    with SessionLocal() as session:
        user = session.query(User).filter_by(username=target_username).first()
        if not user:
            return False
        user.fail_count = 0
        user.banned = False
        session.commit()
        log.info("AUTH", f"Account '{target_username}' unlocked.")
        return True


def ban_account(target_username: str) -> bool:
    with SessionLocal() as session:
        user = session.query(User).filter_by(username=target_username).first()
        if not user or user.role == "owner":
            return False
        user.banned = True
        session.commit()
        log.warning("AUTH", f"Account '{target_username}' banned.")
        return True


def delete_account(target_username: str) -> bool:
    with SessionLocal() as session:
        user = session.query(User).filter_by(username=target_username).first()
        if not user or user.role == "owner":
            return False
        session.delete(user)
        session.commit()
        log.warning("AUTH", f"Account '{target_username}' deleted.")
        return True


def get_all_users() -> list[dict]:
    with SessionLocal() as session:
        return [
            {
                "id":         u.id,
                "username":   u.username,
                "role":       u.role,
                "banned":     u.banned,
                "fail_count": u.fail_count,
                "last_login": u.last_login,
                "created_at": u.created_at,
            }
            for u in session.query(User).all()
        ]
