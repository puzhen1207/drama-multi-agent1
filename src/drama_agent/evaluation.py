"""可复现的三模式离线评测：单提示词、仅 RAG、完整工作流。"""
from __future__ import annotations

import json
import statistics
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List

from pydantic import BaseModel, Field

from .agents.parser_agent import _rule_based_parse
from .agents.polish_agent import run_copywriting, run_organize, run_qa
from .agents.retriever_agent import run_retrieve
from .graph import run_workflow
from .llm import chat
from .telemetry import record_retrieval, start_run_telemetry
from .tools.compliance_engine import tool_sensitive_check


VALID_MODES = ("single_prompt", "rag_only", "full_workflow")


class EvaluationCase(BaseModel):
    id: str
    task_type: str
    prompt: str
    expected_terms: List[str] = Field(default_factory=list)
    expected_audit_pass: bool | None = None
    notes: str = ""


def load_cases(path: Path) -> List[EvaluationCase]:
    cases: List[EvaluationCase] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            cases.append(EvaluationCase.model_validate_json(line))
        except Exception as exc:
            raise ValueError(f"{path}:{line_no} 不是合法评测样本: {exc}") from exc
    return cases


def _compliance_snapshot(content: str) -> Dict[str, Any]:
    result = tool_sensitive_check(content)
    forbidden = result.get("forbidden") or []
    warning = result.get("warning") or []
    return {
        "rule_passed": not forbidden and not warning,
        "forbidden_count": len(forbidden),
        "warning_count": len(warning),
    }


def _term_coverage(content: str, terms: Iterable[str]) -> float | None:
    expected = [term for term in terms if term]
    if not expected:
        return None
    return round(sum(1 for term in expected if term in content) / len(expected), 3)


def _run_single_prompt(case: EvaluationCase) -> Dict[str, Any]:
    telemetry = start_run_telemetry()
    t0 = time.time()
    content = chat(
        user_prompt=case.prompt,
        system_prompt=(
            "你是短剧内容助手。直接完成用户任务，输出可用的中文结果；"
            "兼顾剧情吸引力、人物一致性、可拍摄性和内容合规。"
        ),
    )
    elapsed_ms = (time.time() - t0) * 1000
    return {
        "content": content,
        "success": bool(content.strip()),
        "iterations": 0,
        "degrade_mode": False,
        "metrics": telemetry.snapshot(elapsed_ms),
    }


def _run_rag_only(case: EvaluationCase) -> Dict[str, Any]:
    telemetry = start_run_telemetry()
    t0 = time.time()
    parsed = _rule_based_parse(case.prompt)
    state: Dict[str, Any] = {
        "raw_input": case.prompt,
        "parsed_task": parsed,
        "retrieved_materials": [],
        "draft_content": "",
        "audit_result": None,
        "iteration_count": 0,
        "session_context": "",
        "user_profile_text": "",
    }
    if parsed.task_type == "audit":
        content = case.prompt
    else:
        retrieved = run_retrieve(state)
        state.update(retrieved)
        record_retrieval(len(state.get("retrieved_materials") or []))
        generators = {
            "content_organize": run_organize,
            "qa": run_qa,
            "copywriting": run_copywriting,
        }
        state.update(generators.get(parsed.task_type, run_copywriting)(state))
        content = str(state.get("draft_content") or "")
    elapsed_ms = (time.time() - t0) * 1000
    return {
        "content": content,
        "success": bool(content.strip()),
        "iterations": 0,
        "degrade_mode": False,
        "metrics": telemetry.snapshot(elapsed_ms),
    }


def _run_full_workflow(case: EvaluationCase) -> Dict[str, Any]:
    response = run_workflow(case.prompt, user_id="evaluation")
    return {
        "content": response.content,
        "success": response.success,
        "iterations": response.iteration_count,
        "degrade_mode": response.degrade_mode,
        "metrics": response.metrics.model_dump() if response.metrics else {},
        "audit_score": response.audit_result.score if response.audit_result else None,
        "audit_passed": response.audit_result.passed if response.audit_result else None,
        "revision_count": len(response.revisions),
    }


def evaluate_case(case: EvaluationCase, mode: str) -> Dict[str, Any]:
    if mode not in VALID_MODES:
        raise ValueError(f"未知评测模式 {mode!r}；可选值：{', '.join(VALID_MODES)}")
    runners = {
        "single_prompt": _run_single_prompt,
        "rag_only": _run_rag_only,
        "full_workflow": _run_full_workflow,
    }
    result = runners[mode](case)
    content = result.pop("content")
    compliance = _compliance_snapshot(content)
    predicted_audit_pass = result.get("audit_passed")
    if predicted_audit_pass is None and case.expected_audit_pass is not None:
        predicted_audit_pass = compliance["rule_passed"]
    return {
        "case_id": case.id,
        "task_type": case.task_type,
        "mode": mode,
        "prompt": case.prompt,
        "content": content,
        "content_chars": len(content),
        "term_coverage": _term_coverage(content, case.expected_terms),
        "compliance": compliance,
        "expected_audit_pass": case.expected_audit_pass,
        "audit_expectation_correct": (
            bool(predicted_audit_pass) == case.expected_audit_pass
            if case.expected_audit_pass is not None else None
        ),
        **result,
    }


def summarize_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {"total_runs": len(results), "modes": {}}
    for mode in VALID_MODES:
        rows = [row for row in results if row.get("mode") == mode]
        if not rows:
            continue
        latencies = [float((row.get("metrics") or {}).get("elapsed_ms", 0.0)) for row in rows]
        tokens = [int((row.get("metrics") or {}).get("total_tokens", 0)) for row in rows]
        coverages = [float(row["term_coverage"]) for row in rows if row.get("term_coverage") is not None]
        audit_rows = [row for row in rows if row.get("audit_expectation_correct") is not None]
        summary["modes"][mode] = {
            "runs": len(rows),
            "success_rate": round(sum(bool(row.get("success")) for row in rows) / len(rows), 3),
            "rule_compliance_rate": round(
                sum(bool((row.get("compliance") or {}).get("rule_passed")) for row in rows) / len(rows), 3
            ),
            "mean_latency_ms": round(statistics.fmean(latencies), 1),
            "p95_latency_ms": round(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)], 1),
            "mean_tokens": round(statistics.fmean(tokens), 1),
            "mean_iterations": round(statistics.fmean(float(row.get("iterations", 0)) for row in rows), 2),
            "mean_term_coverage": round(statistics.fmean(coverages), 3) if coverages else None,
            "audit_expectation_accuracy": round(
                sum(bool(row["audit_expectation_correct"]) for row in audit_rows) / len(audit_rows), 3
            ) if audit_rows else None,
            "failed_llm_calls": sum(int((row.get("metrics") or {}).get("llm_failed_calls", 0)) for row in rows),
        }
    return summary


def save_results(results: List[Dict[str, Any]], output_dir: Path) -> Dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    result_path = output_dir / f"evaluation-{stamp}.jsonl"
    summary_path = output_dir / f"evaluation-{stamp}-summary.json"
    result_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in results) + "\n",
        encoding="utf-8",
    )
    summary_path.write_text(
        json.dumps(summarize_results(results), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"results": result_path, "summary": summary_path}
