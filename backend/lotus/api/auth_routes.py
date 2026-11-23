"""Sign-in, session and team-management endpoints."""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from ..auth import COOKIE, User, admin_only, current_user, set_session_cookie

auth = APIRouter(prefix="/api/auth")
team = APIRouter(prefix="/api/users", dependencies=[Depends(admin_only)])


def U(request: Request):
    return request.app.state.users


class LoginIn(BaseModel):
    email: str
    password: str


@auth.post("/login")
def login(request: Request, response: Response, body: LoginIn):
    users, limiter = U(request), request.app.state.login_limiter
    email = body.email.strip().lower()
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0]
    limiter.check(email, ip)
    user = users.authenticate(email, body.password)
    if not user:
        limiter.fail(email, ip)
        raise HTTPException(401, "Email or password is incorrect")
    limiter.clear(email)
    set_session_cookie(request, response, user, users._raw(email).get("session_ver", 0))
    return user


@auth.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@auth.get("/me")
def me(user: User = Depends(current_user)):
    return user


class PasswordIn(BaseModel):
    current_password: str
    new_password: str


@auth.post("/password")
def change_password(request: Request, response: Response, body: PasswordIn, user: User = Depends(current_user)):
    users = U(request)
    if not users.authenticate(user.email, body.current_password):
        raise HTTPException(400, "Current password is incorrect")
    if body.new_password == body.current_password:
        raise HTTPException(400, "Choose a different password")
    ver = users._raw(user.email).get("session_ver", 0) + 1  # revoke other sessions
    updated = users.update(user.email, password=body.new_password, must_change_password=False, session_ver=ver)
    set_session_cookie(request, response, updated, ver)
    return updated


# ------------------------------------------------------------------ team (admin only)
@team.get("")
def list_users(request: Request):
    return U(request).all()


class NewUser(BaseModel):
    email: str
    name: str
    role: Literal["admin", "staff"] = "staff"
    temporary_password: str


@team.post("")
def create_user(request: Request, body: NewUser):
    return U(request).create(body.email, body.name, body.role, body.temporary_password, must_change=True)


class UserPatch(BaseModel):
    role: Optional[Literal["admin", "staff"]] = None
    active: Optional[bool] = None
    temporary_password: Optional[str] = None


@team.patch("/{email}")
def update_user(request: Request, email: str, body: UserPatch, admin: User = Depends(admin_only)):
    users = U(request)
    target = users.get(email)
    if not target:
        raise HTTPException(404, "User not found")
    demoting = (body.role == "staff" or body.active is False) and target.role == "admin" and target.active
    if demoting and users.active_admins() <= 1:
        raise HTTPException(409, "Keep at least one active admin")
    if target.email == admin.email and body.active is False:
        raise HTTPException(409, "You can't deactivate your own account")
    changes: dict = {"role": body.role, "active": body.active}
    raw = users._raw(email)
    if body.temporary_password is not None:
        changes.update(password=body.temporary_password, must_change_password=True)
    if body.temporary_password is not None or body.active is False:
        changes["session_ver"] = raw.get("session_ver", 0) + 1  # sign the user out everywhere
    return users.update(email, **changes)
