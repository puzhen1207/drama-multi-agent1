import json
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from drama_agent.api import app
from drama_agent.evaluation import (
    EvaluationCase,
    applicable_modes,
    evaluate_case,
    load_cases,
    summarize_results,
)
from drama_agent.graph import _make_unified_diff, run_workflow
from drama_agent.models import AuditIssue, AuditResult, ParsedTask


client = TestClient(app)


def test_dataset_has_120_diverse_cases():
    root = Path(__file__).resolve().parents[1]
    cases = load_cases(root / "evals" / "dataset.jsonl")
    assert len(cases) == 120
    assert {case.task_type for case in cases} == {"copywriting", "content_organize", "qa", "audit"}
    assert len({case.prompt for case in cases}) == 120
    generation_cases = [case for case in cases if case.task_type != "audit"]
    assert all(case.expected_source_titles for case in generation_cases)
    assert all(case.grounding_terms for case in generation_cases)


def test_single_prompt_evaluation_records_metrics():
    case = EvaluationCase(
        id="demo", task_type="copywriting", prompt="为职场短剧写推广文案", expected_terms=["短剧"]
    )
    row = evaluate_case(case, "single_prompt", strict_llm=False)
    assert row["success"] is True
    assert row["metrics"]["stub_calls"] == 1
    assert row["quality_eligible"] is False
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


def test_stub_rows_are_excluded_from_quality_statistics():
    rows = [{
        "mode": "single_prompt", "success": True, "pipeline_success": True,
        "quality_eligible": False, "metrics": {"stub_calls": 1, "total_tokens": 0},
    }]
    summary = summarize_results(rows)
    mode = summary["modes"]["single_prompt"]
    assert mode["runs"] == 1
    assert mode["quality_eligible_runs"] == 0
    assert mode["success_rate"] is None


def test_recovered_real_llm_retry_remains_quality_eligible(monkeypatch):
    import drama_agent.evaluation as evaluation_module
    from drama_agent.telemetry import record_llm_call

    def recovered_generation(state):
        record_llm_call(prompt_chars=100, success=False)
        record_llm_call(
            prompt_chars=100,
            completion_chars=80,
            usage={"prompt_tokens": 25, "completion_tokens": 20, "total_tokens": 45},
            success=True,
        )
        return {"draft_content": "这是一段经过真实模型重试后生成的合规短剧推广内容。"}

    monkeypatch.setattr(evaluation_module, "run_copywriting", recovered_generation)
    case = EvaluationCase(id="recovered", task_type="copywriting", prompt="写推广文案")
    row = evaluate_case(case, "single_prompt", strict_llm=True)

    assert row["quality_eligible"] is True
    assert row["metrics"]["llm_successful_calls"] == 1
    assert row["metrics"]["llm_failed_calls"] == 1


def test_audit_case_records_expected_outcome():
    case = EvaluationCase(
        id="audit-risk", task_type="audit", prompt="请审核：详细展示自杀教程和实施步骤",
        expected_audit_pass=False,
    )
    assert applicable_modes(case, ("single_prompt", "rag_only", "full_workflow")) == ["full_workflow"]
    row = evaluate_case(case, "full_workflow", strict_llm=False)
    assert row["audit_expectation_correct"] is True


def test_audit_case_rejects_meaningless_rag_mode():
    import pytest

    case = EvaluationCase(id="audit", task_type="audit", prompt="请审核这段内容")
    with pytest.raises(ValueError, match="不适用于"):
        evaluate_case(case, "rag_only", strict_llm=False)


def test_controlled_modes_only_change_retrieval(monkeypatch):
    import drama_agent.evaluation as evaluation_module

    material_counts = []
    monkeypatch.setattr(evaluation_module, "run_retrieve", lambda state: {
        "retrieved_materials": [{"title": "参考", "content": "素材", "category": "文案"}]
    })
    monkeypatch.setattr(evaluation_module, "run_copywriting", lambda state: (
        material_counts.append(len(state.get("retrieved_materials") or []))
        or {"draft_content": "这是一段合规且足够长的短剧推广内容，用于验证受控变量。"}
    ))
    case = EvaluationCase(id="controlled", task_type="copywriting", prompt="写推广文案")
    evaluate_case(case, "single_prompt", strict_llm=False)
    evaluate_case(case, "rag_only", strict_llm=False)
    assert material_counts == [0, 1]


def test_rag_metrics_measure_source_and_grounding(monkeypatch):
    import drama_agent.evaluation as evaluation_module

    monkeypatch.setattr(evaluation_module, "run_retrieve", lambda state: {
        "retrieved_materials": [{
            "title": "低成本拍摄方法", "content": "固定机位和画外音", "category": "制作"
        }]
    })
    monkeypatch.setattr(evaluation_module, "run_qa", lambda state: {
        "draft_content": "建议采用固定机位，并用画外音交代场外事件，降低拍摄成本。"
    })
    case = EvaluationCase(
        id="rag-metric",
        task_type="qa",
        prompt="怎样低成本拍摄？",
        expected_source_titles=["低成本拍摄方法"],
        grounding_terms=["固定机位", "画外音"],
    )
    row = evaluate_case(case, "rag_only", strict_llm=False)
    assert row["retrieval_source_recall"] == 1.0
    assert row["grounding_term_coverage"] == 1.0


def test_token_budget_stops_call_before_overspend():
    import pytest

    from drama_agent.exceptions import TokenBudgetExceededError
    from drama_agent.telemetry import ensure_llm_token_budget, start_run_telemetry

    telemetry = start_run_telemetry(strict_llm=True, max_total_tokens=100)
    with pytest.raises(TokenBudgetExceededError, match="Token 预算不足"):
        ensure_llm_token_budget(prompt_chars=100, max_completion_tokens=100)
    assert telemetry.token_budget_exhausted is True


def test_llm_nodes_do_not_stack_tenacity_retries():
    from drama_agent.agents.audit_agent import run_audit
    from drama_agent.agents.parser_agent import run_parse
    from drama_agent.agents.polish_agent import _run_task_generation

    assert not hasattr(run_parse, "retry")
    assert not hasattr(run_audit, "retry")
    assert not hasattr(_run_task_generation, "retry")


def test_http_retry_count_is_centrally_bounded(monkeypatch):
    import drama_agent.llm as llm_module
    from drama_agent.exceptions import LLMResponseError

    attempts = []

    def flaky_once(messages, temperature, prompt_chars):
        attempts.append(1)
        if len(attempts) < 3:
            raise LLMResponseError("empty")
        return "真实结果"

    monkeypatch.setattr(llm_module, "llm_available", lambda: True)
    monkeypatch.setattr(llm_module, "_call_http_api_once", flaky_once)
    monkeypatch.setattr(llm_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(llm_module.settings, "llm_max_retries", 2)
    assert llm_module._call_http_api([{"role": "user", "content": "测试"}], 0) == "真实结果"
    assert len(attempts) == 3


def test_blind_review_hides_mode_names():
    from scripts.build_blind_review import build_packets

    rows = [
        {
            "case_id": "case-1", "repetition": 1, "mode": mode,
            "task_type": "copywriting", "prompt": "写文案", "content": f"内容-{mode}",
            "quality_eligible": True,
        }
        for mode in ("single_prompt", "rag_only", "full_workflow")
    ]
    packets, key = build_packets(rows, seed=1)
    assert len(packets) == 1
    assert all("mode" not in candidate for candidate in packets[0]["candidates"])
    assert set(key[0]["mapping"].values()) == {"single_prompt", "rag_only", "full_workflow"}


def test_strict_mode_forbids_stub():
    import pytest

    from drama_agent.exceptions import LLMServiceError
    from drama_agent.llm import chat
    from drama_agent.telemetry import start_run_telemetry

    start_run_telemetry(strict_llm=True)
    with pytest.raises(LLMServiceError, match="严格评测"):
        chat("连通性测试")


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


def test_evaluation_cli_checkpoints_and_resumes(tmp_path):
    root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable, str(root / "scripts" / "run_evaluation.py"),
        "--offline", "--limit", "1", "--output-dir", str(tmp_path),
    ]
    first = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=30)
    assert first.returncode == 0, first.stderr
    result_path = next(tmp_path.glob("evaluation-*.jsonl"))
    assert len(result_path.read_text(encoding="utf-8").splitlines()) == 3
    summary_path = next(tmp_path.glob("evaluation-*-summary.json"))
    manifest_path = next(tmp_path.glob("evaluation-*-manifest.json"))
    assert json.loads(summary_path.read_text(encoding="utf-8"))["status"] == "complete"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["strict_llm"] is False
    assert manifest["knowledge_sha256"]
    assert manifest["llm_max_retries"] >= 0

    resumed = subprocess.run(
        command[:2] + ["--offline", "--limit", "1", "--resume", str(result_path)],
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    assert resumed.returncode == 0, resumed.stderr
    assert len(result_path.read_text(encoding="utf-8").splitlines()) == 3
