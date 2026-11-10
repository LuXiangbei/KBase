from __future__ import annotations

import sqlite3
from typing import Iterator

from fastapi import Depends, Request

from .config import Settings, get_settings
from .db import db_session
from .security import User, user_from_headers


def get_app_settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    settings: Settings = getattr(request.app.state, "settings", None) or get_settings()
    with db_session(settings.db_path) as conn:
        yield conn


def get_user(request: Request) -> User:
    return user_from_headers(request.headers)


SettingsDep = Depends(get_app_settings)
ConnDep = Depends(get_conn)
UserDep = Depends(get_user)
