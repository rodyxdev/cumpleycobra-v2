"""Usuarios y autenticación por token bearer.

- Cada usuario tiene un token aleatorio; en disco solo se guarda su sha256.
- El árbitro es el backend: se autentica con ARBITER_TOKEN (variable de entorno).
- Los roles son por tarea: cliente = quien la creó; freelancer = quien la aceptó.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import uuid
from typing import Annotated, Literal

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel, Field

from backend.config import get_settings

_lock = threading.Lock()
ARBITER_ID = "arbiter"


class User(BaseModel):
    user_id: str
    name: str
    stellar_address: str | None = None
    is_arbiter: bool = False


class _StoredUser(User):
    token_sha256: str


class NewUser(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    stellar_address: str | None = Field(default=None, pattern=r"^G[A-Z2-7]{55}$")


def _path():
    return get_settings().work_dir / "users.json"


def _load() -> dict[str, _StoredUser]:
    path = _path()
    if not path.exists():
        return {}
    return {k: _StoredUser(**v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


def _save(users: dict[str, _StoredUser]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({k: v.model_dump() for k, v in users.items()}, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_user(data: NewUser) -> tuple[User, str]:
    token = secrets.token_urlsafe(32)
    user = _StoredUser(user_id=uuid.uuid4().hex, name=data.name, stellar_address=data.stellar_address,
                       token_sha256=_digest(token))
    with _lock:
        users = _load()
        users[user.user_id] = user
        _save(users)
    return User(**user.model_dump(exclude={"token_sha256"})), token


def update_address(user_id: str, address: str) -> User:
    with _lock:
        users = _load()
        users[user_id] = users[user_id].model_copy(update={"stellar_address": address})
        _save(users)
        return User(**users[user_id].model_dump(exclude={"token_sha256"}))


def get_user(user_id: str) -> User | None:
    if user_id == ARBITER_ID:
        return User(user_id=ARBITER_ID, name="Cumple&Cobra (árbitro)", is_arbiter=True)
    stored = _load().get(user_id)
    return User(**stored.model_dump(exclude={"token_sha256"})) if stored else None


def user_from_token(token: str) -> User | None:
    arbiter = os.getenv("ARBITER_TOKEN")
    if arbiter and hmac.compare_digest(token, arbiter):
        return get_user(ARBITER_ID)
    digest = _digest(token)
    for user in _load().values():
        if hmac.compare_digest(user.token_sha256, digest):
            return User(**user.model_dump(exclude={"token_sha256"}))
    return None


def current_user(authorization: Annotated[str | None, Header()] = None) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing token (Authorization: Bearer ...)")
    user = user_from_token(authorization.removeprefix("Bearer ").strip())
    if user is None:
        raise HTTPException(401, "Invalid token")
    return user


CurrentUser = Annotated[User, Depends(current_user)]
Role = Literal["client", "freelancer", "arbiter", "visitor"]


def role_in_task(user: User, record) -> Role:
    if user.is_arbiter:
        return "arbiter"
    if user.user_id == record.client_id:
        return "client"
    if record.freelancer_id and user.user_id == record.freelancer_id:
        return "freelancer"
    return "visitor"
