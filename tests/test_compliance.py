"""合规规则引擎单元测试。"""
from __future__ import annotations

from drama_agent.tools.compliance_engine import tool_sensitive_check
from drama_agent.models import AuditIssue, AuditResult


def test_clean_text_passes():
    result = tool_sensitive_check("这是一段正常的短剧文案，讲述都市爱情故事。")
    assert result["passed_rule"] is True
    assert len(result["forbidden"]) == 0


def test_forbidden_keyword_detected():
    result = tool_sensitive_check("这段内容涉及色情低俗描写。")
    assert result["passed_rule"] is False
    assert any(h["level"] == "forbidden" for h in result["forbidden"])


def test_phone_number_detected():
    result = tool_sensitive_check("请联系我：13800138000")
    assert result["passed_rule"] is False


def test_semantic_forbidden_issue_cannot_pass(monkeypatch):
    import drama_agent.agents.audit_agent as audit_module

    monkeypatch.setattr(audit_module, "llm_available", lambda: True)
    monkeypatch.setattr(
        audit_module,
        "chat_structured",
        lambda **_: AuditResult(
            passed=True,
            score=1.0,
            issues=[AuditIssue(level="forbidden", category="语义风险")],
        ),
    )
    result = audit_module.run_audit({
        "draft_content": "这是一段长度足够且规则层没有直接命中的普通测试文本。",
        "iteration_count": 0,
        "degrade_mode": False,
    })["audit_result"]
    assert result.passed is False
