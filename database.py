"""
ZENITH — core/database.py
SQLAlchemy ORM models + engine bootstrap. All tables, relationships, and
utility accessors live here. Imported by every module that needs persistence.
"""

import os
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean,
    Float, DateTime, Text, ForeignKey, Enum as SAEnum
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session
from sqlalchemy.pool import StaticPool

Base = declarative_base()

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "zenith.db")
ENGINE = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
    echo=False,
)
SessionLocal = sessionmaker(bind=ENGINE, autocommit=False, autoflush=False)


# ── Models ────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id          = Column(Integer, primary_key=True, index=True)
    username    = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(128), nullable=False)
    role        = Column(String(16), default="user")          # "owner" | "admin" | "user"
    email       = Column(String(128), nullable=True)
    created_at  = Column(DateTime, default=datetime.utcnow)
    last_login  = Column(DateTime, nullable=True)
    banned      = Column(Boolean, default=False)
    fail_count  = Column(Integer, default=0)
    session_token = Column(String(256), nullable=True)

    logs    = relationship("Log", back_populates="user", cascade="all, delete-orphan")
    actions = relationship("ActionHistory", back_populates="user", cascade="all, delete-orphan")
    settings = relationship("Setting", back_populates="user", cascade="all, delete-orphan")
    tokens  = relationship("Token", back_populates="owner", cascade="all, delete-orphan")


class Token(Base):
    __tablename__ = "tokens"

    id              = Column(Integer, primary_key=True)
    token           = Column(Text, nullable=False)
    platform        = Column(String(32), default="discord")
    email           = Column(String(128), nullable=True)
    phone_verified  = Column(Boolean, default=False)
    email_verified  = Column(Boolean, default=False)
    status          = Column(String(16), default="unknown")   # valid | invalid | unknown
    created_at      = Column(DateTime, default=datetime.utcnow)
    last_used       = Column(DateTime, nullable=True)
    owner_user_id   = Column(Integer, ForeignKey("users.id"), nullable=True)

    owner = relationship("User", back_populates="tokens")


class Proxy(Base):
    __tablename__ = "proxies"

    id           = Column(Integer, primary_key=True)
    address      = Column(String(256), nullable=False)
    protocol     = Column(String(16), default="http")   # http | socks4 | socks5
    username     = Column(String(128), nullable=True)
    password     = Column(String(128), nullable=True)
    status       = Column(String(16), default="unknown") # live | dead | slow | unknown
    latency_ms   = Column(Float, nullable=True)
    last_checked = Column(DateTime, nullable=True)
    added_at     = Column(DateTime, default=datetime.utcnow)


class Log(Base):
    __tablename__ = "logs"

    id        = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    level     = Column(String(16), default="INFO")   # DEBUG|INFO|SUCCESS|WARNING|ERROR|CRITICAL
    module    = Column(String(64), default="SYSTEM")
    message   = Column(Text, nullable=False)
    thread_id = Column(String(64), nullable=True)
    user_id   = Column(Integer, ForeignKey("users.id"), nullable=True)

    user = relationship("User", back_populates="logs")


class Setting(Base):
    __tablename__ = "settings"

    id      = Column(Integer, primary_key=True)
    key     = Column(String(128), nullable=False)
    value   = Column(Text, nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    user = relationship("User", back_populates="settings")


class ActionHistory(Base):
    __tablename__ = "action_history"

    id          = Column(Integer, primary_key=True)
    user_id     = Column(Integer, ForeignKey("users.id"), nullable=True)
    module      = Column(String(64), nullable=False)
    action_type = Column(String(128), nullable=False)
    target      = Column(Text, nullable=True)
    result      = Column(String(32), default="pending")  # success | failure | pending
    timestamp   = Column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("User", back_populates="actions")


# ── DB Helpers ─────────────────────────────────────────────────────────────────

def init_db() -> None:
    """Create all tables if they do not exist."""
    Base.metadata.create_all(bind=ENGINE)


def get_session() -> Session:
    """Return a new DB session. Caller is responsible for close()."""
    return SessionLocal()


def has_any_user() -> bool:
    """Return True if at least one user exists in the database."""
    with SessionLocal() as session:
        return session.query(User).count() > 0


def get_user_by_username(username: str) -> User | None:
    with SessionLocal() as session:
        return session.query(User).filter_by(username=username).first()


def get_owner() -> User | None:
    with SessionLocal() as session:
        return session.query(User).filter_by(role="owner").first()


def count_live_proxies() -> int:
    with SessionLocal() as session:
        return session.query(Proxy).filter_by(status="live").count()


def count_valid_tokens(platform: str = "discord") -> int:
    with SessionLocal() as session:
        return session.query(Token).filter_by(status="valid", platform=platform).count()


def get_setting(key: str, user_id: int | None = None, default: str = "") -> str:
    with SessionLocal() as session:
        q = session.query(Setting).filter_by(key=key, user_id=user_id).first()
        return q.value if q else default


def upsert_setting(key: str, value: str, user_id: int | None = None) -> None:
    with SessionLocal() as session:
        q = session.query(Setting).filter_by(key=key, user_id=user_id).first()
        if q:
            q.value = value
        else:
            session.add(Setting(key=key, value=value, user_id=user_id))
        session.commit()


def log_action(module: str, action_type: str, target: str = "",
               result: str = "success", user_id: int | None = None) -> None:
    with SessionLocal() as session:
        session.add(ActionHistory(
            user_id=user_id,
            module=module,
            action_type=action_type,
            target=target,
            result=result,
        ))
        session.commit()
