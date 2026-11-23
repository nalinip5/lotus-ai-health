"""Authentication and role-based access for the console.

Roles
  admin  — everything staff can do, plus managing team accounts
  staff  — work cases, resolve exceptions, prepare / approve / write off appeals

Every /api route except /api/health and /api/auth/login requires a signed-in
user; there is no anonymous access to cases, letters or appeal approvals.

Passwords: PBKDF2-HMAC-SHA256 (600k iterations, per-user salt).
Sessions:  HMAC-signed, HttpOnly, SameSite=Lax cookie (Secure on HTTPS), 12 h.
Bootstrap: the first admin comes from LOTUS_ADMIN_EMAIL / LOTUS_ADMIN_PASSWORD.
           If no password is configured a random one is generated and logged once.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from collections import defaultdict, deque
from typing import Literal, Optional

from fastapi import Depends, HTTPException, Request, Response
from pydantic import BaseModel

from .domain.models import utcnow
from .store.docstore import DocStore

log = logging.getLogger("lotus.auth")

Role = Literal["admin", "staff"]
COOKIE = "lotus_session"
SESSION_TTL_S = 12 * 3600
PBKDF2_ITER = 600_000
MIN_PASSWORD = 10


# ------------------------------------------------------------------ passwords
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITER)
    return f"pbkdf2_sha256${PBKDF2_ITER}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, it, salt, dk = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(it))
        return hmac.compare_digest(calc, base64.b64decode(dk))
    except Exception:
        return False


def check_password_policy(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise HTTPException(400, f"Password must be at least {MIN_PASSWORD} characters")


# ------------------------------------------------------------------ users
class User(BaseModel):
    id: str
    email: str
    name: str
    role: Role
    active: bool = True
    must_change_password: bool = False
    created_at: str = ""
    last_login: Optional[str] = None


class UserStore:
    COLL = "users"

    def __init__(self, store: DocStore):
        self.store = store

    def _raw(self, email: str) -> Optional[dict]:
        return self.store.get(self.COLL, email.strip().lower())

    def get(self, email: str) -> Optional[User]:
        raw = self._raw(email)
        return User(**{k: v for k, v in raw.items() if k != "password_hash"}) if raw else None

    def all(self) -> list[User]:
        return sorted((User(**{k: v for k, v in r.items() if k != "password_hash"})
                       for r in self.store.find(self.COLL, limit=10_000)), key=lambda u: (u.role, u.name))

    def create(self, email: str, name: str, role: Role, password: str, must_change: bool = True) -> User:
        email = email.strip().lower()
        if "@" not in email:
            raise HTTPException(400, "Enter a valid email")
        check_password_policy(password)
        body = {"id": email, "email": email, "name": name.strip() or email, "role": role, "active": True,
                "must_change_password": must_change, "created_at": utcnow(), "last_login": None,
                "password_hash": hash_password(password)}
        if not self.store.insert_if_absent(self.COLL, email, body):
            raise HTTPException(409, "A user with that email already exists")
        return self.get(email)  # type: ignore[return-value]

    def update(self, email: str, **changes) -> User:
        raw = self._raw(email)
        if not raw:
            raise HTTPException(404, "User not found")
        if "password" in changes:
            pw = changes.pop("password")
            check_password_policy(pw)
            raw["password_hash"] = hash_password(pw)
        raw.update({k: v for k, v in changes.items() if v is not None})
        self.store.put(self.COLL, raw["id"], raw)
        return self.get(email)  # type: ignore[return-value]

    def authenticate(self, email: str, password: str) -> Optional[User]:
        raw = self._raw(email)
        if not raw or not raw.get("active", True):
            # Still spend the hashing time so response timing doesn't reveal which emails exist.
            verify_password(password, hash_password("timing-equaliser"))
            return None
        if not verify_password(password, raw["password_hash"]):
            return None
        return self.update(email, last_login=utcnow())

    def active_admins(self) -> int:
        return sum(1 for u in self.all() if u.role == "admin" and u.active)

    def bootstrap(self) -> None:
        if self.store.count(self.COLL):
            return
        email = os.environ.get("LOTUS_ADMIN_EMAIL", "admin@lotusbpmhealth.com")
        password = os.environ.get("LOTUS_ADMIN_PASSWORD", "")
        generated = not password
        if generated:
            password = secrets.token_urlsafe(12)
        self.create(email, os.environ.get("LOTUS_ADMIN_NAME", "Lotus Admin"), "admin", password,
                    must_change=generated)
        if generated:
            log.warning("Created first admin %s with generated password: %s  (change it after signing in)",
                        email, password)
        else:
            log.info("Created first admin %s from LOTUS_ADMIN_PASSWORD", email)


# ------------------------------------------------------------------ sessions
def _secret() -> bytes:
    s = os.environ.get("LOTUS_SESSION_SECRET")
    if not s:
        global _EPHEMERAL
        s = _EPHEMERAL
    return s.encode()


_EPHEMERAL = secrets.token_urlsafe(32)  # dev fallback: sessions end when the process restarts


def sign_session(email: str, ver: int) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"e": email, "x": int(time.time()) + SESSION_TTL_S,
                                                    "v": ver}).encode()).decode()
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def read_session(token: str) -> Optional[dict]:
    try:
        payload, sig = token.rsplit(".", 1)
        if not hmac.compare_digest(sig, hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload))
        return data if data["x"] > time.time() else None
    except Exception:
        return None


def set_session_cookie(request: Request, response: Response, user: User, ver: int) -> None:
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(COOKIE, sign_session(user.email, ver), max_age=SESSION_TTL_S, httponly=True,
                        samesite="lax", secure=secure, path="/")


# ------------------------------------------------------------------ rate limit
class LoginLimiter:
    """At most 5 failed attempts per email and 20 per client IP in 15 minutes."""

    def __init__(self) -> None:
        self.fails: dict[str, deque] = defaultdict(deque)

    def _recent(self, key: str) -> deque:
        q = self.fails[key]
        while q and q[0] < time.time() - 900:
            q.popleft()
        return q

    def check(self, email: str, ip: str) -> None:
        if len(self._recent("e:" + email)) >= 5 or len(self._recent("i:" + ip)) >= 20:
            raise HTTPException(429, "Too many failed sign-in attempts. Try again in 15 minutes.")

    def fail(self, email: str, ip: str) -> None:
        self.fails["e:" + email].append(time.time())
        self.fails["i:" + ip].append(time.time())

    def clear(self, email: str) -> None:
        self.fails.pop("e:" + email, None)


# ------------------------------------------------------------------ dependencies
def current_user(request: Request) -> User:
    users: UserStore = request.app.state.users
    data = read_session(request.cookies.get(COOKIE, ""))
    if not data:
        raise HTTPException(401, "Sign in required")
    raw = users._raw(data["e"])
    # Session version: bumped on password change / deactivation to revoke existing sessions.
    if not raw or not raw.get("active", True) or raw.get("session_ver", 0) != data.get("v", 0):
        raise HTTPException(401, "Session expired — sign in again")
    user = users.get(data["e"])
    assert user
    request.state.user = user
    return user


def require_roles(*roles: Role):
    def dep(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, "You don't have permission for this action")
        return user
    return dep


staff_or_admin = require_roles("admin", "staff")
admin_only = require_roles("admin")


def actor(user: User) -> str:
    return f"{user.name} ({user.role})"
