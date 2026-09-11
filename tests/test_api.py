"""FastAPI 路由测试。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from drama_agent.api import app, _format_sse
from drama_agent.config import settings
from pydantic import SecretStr


client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "ok"
    assert payload["llm"]["mode"] in {"real_with_stub_fallback", "stub"}
    assert isinstance(payload["llm"]["configured"], bool)


def test_root_returns_html():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    html = resp.text
    assert 'id="llm-badge"' in html
    assert 'role="tablist"' in html
    assert 'aria-modal="true"' in html
    assert "AbortController" in html


def test_list_tools():
    resp = client.get("/v1/tools")
    assert resp.status_code == 200
    tools = resp.json()["tools"]
    assert "sensitive_check" in tools
    assert "retrieve_materials" in tools


def test_generate_sync():
    resp = client.post("/v1/generate", json={
        "raw_input": "写一段短剧开头",
        "user_id": "test",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["data"]["content"]


def test_sse_encoding():
    b = _format_sse("final", {"content": "你好，世界", "score": 0.95})
    assert b.startswith(b"event: final\n")
    assert "你好".encode("utf-8") in b
    assert b"\\u4f60" not in b


def test_rejects_unsafe_session_id():
    resp = client.post("/v1/generate", json={
        "raw_input": "写一段短剧开头",
        "user_id": "test",
        "session_id": "../../outside",
    })
    assert resp.status_code == 422


def test_session_owner_is_enforced():
    generated = client.post("/v1/generate", json={
        "raw_input": "写一段短剧开头",
        "user_id": "owner_a",
    }).json()
    sid = generated["data"]["session_id"]
    resp = client.get(f"/v1/sessions/{sid}?user_id=owner_b")
    assert resp.status_code == 403


def test_get_missing_session_does_not_create_it():
    resp = client.get("/v1/sessions/S_missing123?user_id=reader")
    assert resp.status_code == 404
    sessions = client.get("/v1/sessions?user_id=reader").json()["sessions"]
    assert all(item["session_id"] != "S_missing123" for item in sessions)


def test_bearer_token_binds_user_identity(monkeypatch):
    monkeypatch.setattr(
        settings,
        "api_user_tokens_json",
        SecretStr('{"alice":"alice-token-123456789","bob":"bob-token-12345678901"}'),
    )
    denied = client.post(
        "/v1/generate",
        headers={"Authorization": "Bearer bob-token-12345678901"},
        json={"raw_input": "写一段短剧开头", "user_id": "alice"},
    )
    assert denied.status_code == 403

    allowed = client.post(
        "/v1/generate",
        headers={"Authorization": "Bearer alice-token-123456789"},
        json={"raw_input": "写一段短剧开头", "user_id": "alice"},
    )
    assert allowed.status_code == 200
