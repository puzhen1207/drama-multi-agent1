"""工作流集成测试（Stub 模式，无需 LLM API Key）。"""
from __future__ import annotations

from drama_agent.graph import run_workflow, run_workflow_with_events
from drama_agent.models import FinalResponse
from drama_agent.models import AuditResult
from drama_agent.models import ParsedTask


def test_run_workflow_returns_content():
    resp = run_workflow("写一段关于重生80年代当首富的剧情，300字", user_id="pytest")
    assert isinstance(resp, FinalResponse)
    assert resp.content
    assert len(resp.content) > 50


def test_run_workflow_with_events():
    events: list = []
    resp = run_workflow_with_events(
        "帮我整理霸总追妻短剧大纲",
        user_id="pytest",
        event_callback=lambda ev: events.append(ev),
    )
    assert resp.content
    types = {e.get("type") for e in events}
    assert "node_start" in types or "workflow_start" in types
    assert any(e.get("type") == "node_done" for e in events)


def test_retrieval_event_exposes_provenance_without_content(monkeypatch):
    import drama_agent.graph as graph_module

    def fake_retrieve(_state):
        return {"retrieved_materials": [{
            "material_id": "M_origin01",
            "title": "个人记忆 · 测试素材",
            "content": "不应被发送到进度事件中的素材正文",
            "category": "个人记忆",
            "score": 0.88,
            "source": "user_memory",
            "source_path": "用户个人素材 / provenance_user",
            "owner_user_id": "provenance_user",
        }]}

    monkeypatch.setattr(graph_module, "run_retrieve", fake_retrieve)
    events = []
    response = graph_module.run_workflow_with_events(
        "写一段都市短剧文案",
        user_id="provenance_user",
        event_callback=events.append,
    )
    retrieve_done = next(
        event for event in events
        if event.get("type") == "node_done" and event.get("node") == "retrieve_node"
    )
    assert retrieve_done["references"][0]["owner_user_id"] == "provenance_user"
    assert retrieve_done["references"][0]["source"] == "user_memory"
    assert "content" not in retrieve_done["references"][0]
    assert response.reference_sources[0].material_id == "M_origin01"


def test_session_persistence():
    resp1 = run_workflow("第一集：开场冲突", user_id="session_test")
    sid = resp1.session_id
    assert sid

    resp2 = run_workflow(
        "继续写第二集",
        user_id="session_test",
        session_id=sid,
    )
    assert resp2.session_id == sid
    assert resp2.has_context is True


def test_audit_task_reviews_original_without_rewrite():
    raw = "请审核以下内容是否违规：剧情包含赌博情节。"
    resp = run_workflow(raw, user_id="audit_user")
    assert resp.task_type == "audit"
    assert resp.content == raw
    assert resp.success is True
    assert resp.audit_result is not None
    assert resp.audit_result.passed is False
    assert resp.iteration_count == 1


def test_generation_failure_is_not_reported_as_success(monkeypatch):
    import drama_agent.graph as graph_module

    def always_reject(state):
        iteration = int(state.get("iteration_count", 0))
        return {
            "audit_result": AuditResult(passed=False, score=0.2, summary="reject"),
            "iteration_count": iteration + 1,
        }

    monkeypatch.setattr(graph_module, "run_audit", always_reject)
    resp = graph_module.run_workflow("写一段都市短剧推广文案", user_id="reject_user")
    assert resp.success is False
    assert resp.error == "生成内容在最大重写次数内未通过审核"


def test_fast_mode_stops_after_first_audit(monkeypatch):
    import drama_agent.graph as graph_module

    calls = {"audit": 0, "rewrite": 0}

    def always_reject(state):
        calls["audit"] += 1
        return {
            "audit_result": AuditResult(passed=False, score=0.2, summary="reject"),
            "iteration_count": int(state.get("iteration_count", 0)) + 1,
        }

    def count_rewrite(state):
        calls["rewrite"] += 1
        return {"draft_content": state.get("draft_content", "") + " rewrite"}

    monkeypatch.setattr(graph_module, "run_audit", always_reject)
    monkeypatch.setattr(graph_module, "run_rewrite", count_rewrite)
    response = graph_module.run_workflow(
        "写一段都市短剧推广文案",
        user_id="fast_user",
        run_mode="fast",
    )
    assert response.success is False
    assert calls == {"audit": 1, "rewrite": 0}


def test_qa_uses_dedicated_generation_node():
    events = []
    run_workflow_with_events(
        "短剧文案应该如何设置开场钩子？",
        user_id="qa_user",
        event_callback=events.append,
    )
    completed_nodes = {e.get("node") for e in events if e.get("type") == "node_done"}
    assert "qa_node" in completed_nodes
    assert "copywriting_node" not in completed_nodes


def test_script_chapter_uses_dedicated_generation_node():
    events = []
    response = run_workflow_with_events(
        "写一个主角为博兴是傻子的短剧内容第一章",
        user_id="script_user",
        run_mode="fast",
        event_callback=events.append,
    )
    completed_nodes = {e.get("node") for e in events if e.get("type") == "node_done"}
    assert response.task_type == "script_generation"
    assert "script_node" in completed_nodes
    assert "copywriting_node" not in completed_nodes
    assert "【推广标题】" not in response.content


def test_script_generation_corrects_marketing_scaffold(monkeypatch):
    import drama_agent.agents.polish_agent as polish_module

    responses = iter([
        "【投放标题】全城都笑他是傻子\n【核心卖点】身份反转\n【推广文案】这是一段错误的营销文案结构，不能作为剧本正文。",
        "【第一章】\n【场景：博家前院·日】\n众人围住博兴。\n【管家】：傻子也配进祠堂？\n博兴低头笑着，手中却悄悄攥紧了钥匙。",
    ])
    calls = []

    def fake_chat(**kwargs):
        calls.append(kwargs)
        return next(responses)

    monkeypatch.setattr(polish_module, "llm_available", lambda: True)
    monkeypatch.setattr(polish_module, "chat", fake_chat)
    result = polish_module.run_script({
        "parsed_task": ParsedTask(
            task_type="script_generation",
            topic="博兴短剧第一章",
            requirements="写一个主角为博兴是傻子的短剧内容第一章",
        ),
        "retrieved_materials": [],
    })
    assert len(calls) == 2
    assert "【第一章】" in result["draft_content"]
    assert "【投放标题】" not in result["draft_content"]
