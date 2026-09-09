"""工作流集成测试（Stub 模式，无需 LLM API Key）。"""
from __future__ import annotations

from drama_agent.graph import run_workflow, run_workflow_with_events
from drama_agent.models import FinalResponse
from drama_agent.models import AuditResult


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
