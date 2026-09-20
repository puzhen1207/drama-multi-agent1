"""Registration, login, persistence, and API identity binding tests."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

import drama_agent.auth as auth_module
from drama_agent.api import app
from drama_agent.config import settings


client = TestClient(app)


def _register(username: str = "writer_one", password: str = "correct-horse-42"):
    response = client.post(
        "/v1/auth/register",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_registration_is_required_for_business_api(monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)
    response = client.post(
        "/v1/generate",
        json={"raw_input": "写一段短剧开头", "user_id": "guest"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "请先登录"


def test_register_login_and_identity_binding(monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)
    session = _register()
    token = session["token"]
    headers = {"Authorization": f"Bearer {token}"}

    me = client.get("/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["user_id"] == "writer_one"

    allowed = client.post(
        "/v1/generate",
        headers=headers,
        json={"raw_input": "写一段短剧开头", "user_id": "writer_one"},
    )
    assert allowed.status_code == 200

    denied = client.post(
        "/v1/generate",
        headers=headers,
        json={"raw_input": "写一段短剧开头", "user_id": "another_user"},
    )
    assert denied.status_code == 403


def test_password_is_hashed_and_login_survives_store_reload(monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)
    password = "never-store-me-88"
    _register("persistent_user", password)

    raw = settings.absolute_auth_path.read_text(encoding="utf-8")
    assert password not in raw
    saved = json.loads(raw)
    assert saved["users"]["persistent_user"]["password_hash"]
    assert saved["users"]["persistent_user"]["salt"]

    auth_module._auth_store = None
    login = client.post(
        "/v1/auth/login",
        json={"username": "persistent_user", "password": password},
    )
    assert login.status_code == 200
    assert login.json()["user_id"] == "persistent_user"


def test_logout_invalidates_session(monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)
    token = _register("logout_user", "logout-password-9")["token"]
    headers = {"Authorization": f"Bearer {token}"}

    assert client.post("/v1/auth/logout", headers=headers).status_code == 200
    assert client.get("/v1/auth/me", headers=headers).status_code == 401


def test_duplicate_account_and_wrong_password(monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)
    _register("duplicate_user", "original-password-9")

    duplicate = client.post(
        "/v1/auth/register",
        json={"username": "duplicate_user", "password": "another-password-9"},
    )
    assert duplicate.status_code == 409

    wrong = client.post(
        "/v1/auth/login",
        json={"username": "duplicate_user", "password": "wrong-password-99"},
    )
    assert wrong.status_code == 401
    assert wrong.json()["detail"] == "账号或密码错误"
