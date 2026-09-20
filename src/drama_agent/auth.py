"""Persistent local account and session authentication.

Passwords are stored as salted scrypt hashes. Browser sessions use opaque random
tokens; only their SHA-256 digests are persisted on disk.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .config import settings
from .identifiers import validate_identifier


class AuthError(ValueError):
    """A user-facing authentication error."""


class AuthStore:
    def __init__(self, path: Optional[Path] = None):
        self.path = path or settings.absolute_auth_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._data = self._load()

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "users": {}, "sessions": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"账号数据无法读取：{self.path}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("账号数据格式无效")
        payload.setdefault("version", 1)
        payload.setdefault("users", {})
        payload.setdefault("sessions", {})
        return payload

    def _save(self) -> None:
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    @staticmethod
    def _password_hash(password: str, salt: bytes) -> bytes:
        return hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=2**14,
            r=8,
            p=1,
            dklen=32,
        )

    @staticmethod
    def _validate_password(password: str) -> str:
        if len(password) < 8:
            raise AuthError("密码至少需要 8 个字符")
        if len(password) > 128:
            raise AuthError("密码不能超过 128 个字符")
        return password

    @staticmethod
    def _token_digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def register(self, username: str, password: str) -> Dict[str, Any]:
        try:
            user_id = validate_identifier(username, "账号")
        except ValueError as exc:
            raise AuthError(str(exc)) from exc
        self._validate_password(password)
        with self._lock:
            if user_id in self._data["users"]:
                raise AuthError("账号已存在，请直接登录")
            salt = secrets.token_bytes(16)
            password_hash = self._password_hash(password, salt)
            now = int(time.time())
            self._data["users"][user_id] = {
                "user_id": user_id,
                "salt": base64.urlsafe_b64encode(salt).decode("ascii"),
                "password_hash": base64.urlsafe_b64encode(password_hash).decode("ascii"),
                "created_at": now,
            }
            self._save()
            return self._create_session_locked(user_id)

    def login(self, username: str, password: str) -> Dict[str, Any]:
        try:
            user_id = validate_identifier(username, "账号")
        except ValueError as exc:
            raise AuthError("账号或密码错误") from exc
        with self._lock:
            record = self._data["users"].get(user_id)
            if not isinstance(record, dict):
                raise AuthError("账号或密码错误")
            try:
                salt = base64.urlsafe_b64decode(record["salt"])
                expected = base64.urlsafe_b64decode(record["password_hash"])
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError("账号数据已损坏") from exc
            supplied = self._password_hash(password, salt)
            if not hmac.compare_digest(supplied, expected):
                raise AuthError("账号或密码错误")
            return self._create_session_locked(user_id)

    def _create_session_locked(self, user_id: str) -> Dict[str, Any]:
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        expires_at = now + int(settings.auth_session_days * 86400)
        self._data["sessions"][self._token_digest(token)] = {
            "user_id": user_id,
            "created_at": now,
            "expires_at": expires_at,
        }
        self._prune_sessions_locked(now)
        self._save()
        return {"token": token, "user_id": user_id, "expires_at": expires_at}

    def authenticate(self, token: str) -> Optional[str]:
        if not token:
            return None
        digest = self._token_digest(token)
        with self._lock:
            record = self._data["sessions"].get(digest)
            if not isinstance(record, dict):
                return None
            now = int(time.time())
            if int(record.get("expires_at", 0)) <= now:
                self._data["sessions"].pop(digest, None)
                self._save()
                return None
            user_id = str(record.get("user_id", ""))
            if user_id not in self._data["users"]:
                return None
            return user_id

    def logout(self, token: str) -> None:
        if not token:
            return
        with self._lock:
            if self._data["sessions"].pop(self._token_digest(token), None) is not None:
                self._save()

    def _prune_sessions_locked(self, now: Optional[int] = None) -> None:
        current = now or int(time.time())
        expired = [
            digest
            for digest, record in self._data["sessions"].items()
            if not isinstance(record, dict) or int(record.get("expires_at", 0)) <= current
        ]
        for digest in expired:
            self._data["sessions"].pop(digest, None)


_auth_store: Optional[AuthStore] = None


def get_auth_store() -> AuthStore:
    global _auth_store
    if _auth_store is None:
        _auth_store = AuthStore()
    return _auth_store
