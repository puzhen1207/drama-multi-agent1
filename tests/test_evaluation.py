from pathlib import Path

from fastapi.testclient import TestClient

from drama_agent.api import app
from drama_agent.evaluation import EvaluationCase, evaluate_case, load_cases, summarize_results
from drama_agent.graph import _make_unified_diff, run_workflow
from drama_agent.models import AuditIssue, AuditResult, ParsedTask


client = TestClient(app)


def test_dataset_has_120_diverse_cases():
    root = Path(__file__).resolve().parents[1]
    cases = load_cases(root / "evals" / "dataset.jsonl")
    assert len(cases) == 120
    assert {case.task_type for case in cases} == {"copywriting", "content_organize", "qa", "audit"}


def test_single_prompt_evaluation_records_metrics():
    case = EvaluationCase(
        id="demo", task_type="copywriting", prompt="为职场短剧写推广文案", expected_terms=["短剧"]
    )
    row = evaluate_case(case, "single_prompt")
    assert row["success"] is True
    assert row["metrics"]["stub_calls"] == 1
    assert row["metrics"]["elapsed_ms"] >= 0
    assert "compliance" in row


def test_summary_compares_modes():
    rows = [
        {"mode": "single_prompt", "success": True, "term_coverage": 0.5, "iterations": 0,
         "compliance": {"rule_passed": True}, "metrics": {"elapsed_ms": 10, "total_tokens": 5}},
        {"mode": "full_workflow", "success": True, "term_coverage": 1.0, "iterations": 1,
         "compliance": {"rule_passed": True}, "metrics": {"elapsed_ms": 20, "total_tokens": 8}},
    ]
    summary = summarize_results(rows)
    assert summary["modes"]["single_prompt"]["mean_latency_ms"] == 10
    assert summary["modes"]["full_workflow"]["mean_iterations"] == 1


def test_audit_case_records_expected_outcome():
    case = EvaluationCase(
        id="audit-risk", task_type="audit", prompt="请审核：详细展示自杀教程和实施步骤",
        expected_audit_pass=False,
    )
    row = evaluate_case(case, "rag_only")
    assert row["audit_expectation_correct"] is True


def test_workflow_returns_runtime_evidence():
    response = run_workflow("写一版都市短剧推广文案")
    assert response.metrics is not None
    assert response.metrics.elapsed_ms >= 0
    assert "parse_node" in response.metrics.node_durations_ms
    assert response.metrics.stub_calls >= 1


def test_unified_diff_contains_before_and_after():
    diff = _make_unified_diff("第一幕：冲突", "第一幕：强冲突")
    assert "-第一幕：冲突" in diff
    assert "+第一幕：强冲突" in diff


def test_workflow_captures_real_revision_before_and_after(monkeypatch):
    import drama_agent.graph as graph_module

    audits = iter([
        AuditResult(passed=False, score=0.4, issues=[
            AuditIssue(level="warning", category="节奏", suggestion="加强开场")
        ]),
        AuditResult(passed=True, score=0.9),
    ])
    monkeypatch.setattr(graph_module, "run_parse", lambda state: {"parsed_task": ParsedTask(
        task_type="copywriting", topic="测试", requirements="测试", needs_retrieval=False
    )})
    monkeypatch.setattr(graph_module, "run_copywriting", lambda state: {"draft_content": "第一幕：普通开场"})
    monkeypatch.setattr(graph_module, "run_rewrite", lambda state: {"draft_content": "第一幕：强冲突开场"})
    monkeypatch.setattr(graph_module, "run_audit", lambda state: {
        "audit_result": next(audits), "iteration_count": int(state.get("iteration_count", 0)) + 1
    })

    response = graph_module.run_workflow("写一段测试文案")
    assert response.success is True
    assert len(response.revisions) == 1
    revision = response.revisions[0]
    assert revision.before_content == "第一幕：普通开场"
    assert revision.after_content == "第一幕：强冲突开场"
    assert revision.before_score == 0.4
    assert revision.after_score == 0.9
    assert "加强开场" in revision.issues[0]


def test_human_review_api_and_summary():
    payload = {
        "user_id": "reviewer",
        "session_id": "S_demo",
        "case_id": "case-1",
        "mode": "full_workflow",
        "hook": 5,
        "pacing": 4,
        "character_consistency": 3,
        "shootability": 4,
        "compliance": 5,
        "notes": "整体可用",
    }
    saved = client.post("/v1/evaluations/human", json=payload)
    assert saved.status_code == 200
    assert saved.json()["review"]["average"] == 4.2
    summary = client.get("/v1/evaluations/summary?user_id=reviewer")
    assert summary.status_code == 200
    assert summary.json()["total"] == 1
    assert summary.json()["averages"]["hook"] == 5


def test_human_review_rejects_out_of_range_score():
    payload = {
        "user_id": "reviewer", "hook": 6, "pacing": 3,
        "character_consistency": 3, "shootability": 3, "compliance": 3,
    }
    assert client.post("/v1/evaluations/human", json=payload).status_code == 422
