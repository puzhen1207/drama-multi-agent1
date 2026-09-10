"""受控三模式评测：单次生成、增加 RAG、增加审核重写。"""
from __future__ import annotations

import json
import math
import statistics
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List

from pydantic import BaseModel, Field

from .agents.audit_agent import run_audit
from .agents.parser_agent import _rule_based_parse
from .agents.polish_agent import run_copywriting, run_organize, run_qa, run_rewrite
from .agents.retriever_agent import run_retrieve
from .config import settings
from .telemetry import record_node, record_retrieval, start_run_telemetry
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


def applicable_modes(case: EvaluationCase, requested: Iterable[str]) -> List[str]:
    """RAG 对审核分类没有实验意义；审核样本只验证完整审核链。"""
    modes = [mode for mode in requested if mode in VALID_MODES]
    if case.task_type == "audit":
        return ["full_workflow"] if "full_workflow" in modes else []
    return modes


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


def _timed_node(
    name: str,
    func: Callable[[Dict[str, Any]], Dict[str, Any]],
    state: Dict[str, Any],
) -> Dict[str, Any]:
    t0 = time.time()
    try:
        result = func(state)
        record_node(name, (time.time() - t0) * 1000)
        return result
    except Exception as exc:
        record_node(name, (time.time() - t0) * 1000, f"{type(exc).__name__}: {exc}")
        raise


def _base_state(case: EvaluationCase) -> Dict[str, Any]:
    # 三组共享同一个确定性解析结果，避免把解析差异误算成 RAG 收益。
    parsed = _rule_based_parse(case.prompt)
    parsed.task_type = case.task_type
    parsed.needs_retrieval = case.task_type != "audit"
    return {
        "raw_input": case.prompt,
        "user_id": "evaluation_isolated",
        "parsed_task": parsed,
        "retrieved_materials": [],
        "draft_content": "",
        "audit_result": None,
        "iteration_count": 0,
        "session_context": "",
        "user_profile_text": "",
        "degrade_mode": False,
    }


def _generate_once(state: Dict[str, Any]) -> Dict[str, Any]:
    task_type = state["parsed_task"].task_type
    generators = {
        "content_organize": run_organize,
        "qa": run_qa,
        "copywriting": run_copywriting,
    }
    return _timed_node(
        f"{task_type}_generation", generators.get(task_type, run_copywriting), state
    )


def _run_generation_case(
    case: EvaluationCase,
    *,
    use_rag: bool,
    use_audit_loop: bool,
    strict_llm: bool,
) -> Dict[str, Any]:
    telemetry = start_run_telemetry(strict_llm=strict_llm)
    t0 = time.time()
    state = _base_state(case)
    if use_rag:
        state.update(_timed_node("retrieve", run_retrieve, state))
        record_retrieval(len(state.get("retrieved_materials") or []))
    state.update(_generate_once(state))

    audit_result = None
    if use_audit_loop:
        max_iterations = int(settings.audit_max_iteration or 3)
        while int(state.get("iteration_count", 0)) < max_iterations:
            state.update(_timed_node("audit", run_audit, state))
            audit_result = state.get("audit_result")
            if audit_result is not None and bool(getattr(audit_result, "passed", False)):
                break
            if int(state.get("iteration_count", 0)) >= max_iterations:
                break
            state.update(_timed_node("rewrite", run_rewrite, state))

    elapsed_ms = (time.time() - t0) * 1000
    content = str(state.get("draft_content") or "")
    passed = bool(getattr(audit_result, "passed", False)) if audit_result is not None else bool(content)
    return {
        "content": content,
        "pipeline_success": passed,
        "iterations": int(state.get("iteration_count", 0)),
        "degrade_mode": bool(state.get("degrade_mode", False)),
        "metrics": telemetry.snapshot(elapsed_ms),
        "audit_score": getattr(audit_result, "score", None),
        "audit_passed": getattr(audit_result, "passed", None),
    }


def _run_audit_case(case: EvaluationCase, strict_llm: bool) -> Dict[str, Any]:
    telemetry = start_run_telemetry(strict_llm=strict_llm)
    t0 = time.time()
    state = _base_state(case)
    state["draft_content"] = case.prompt
    state.update(_timed_node("audit", run_audit, state))
    audit_result = state.get("audit_result")
    elapsed_ms = (time.time() - t0) * 1000
    return {
        "content": case.prompt,
        "pipeline_success": audit_result is not None,
        "iterations": int(state.get("iteration_count", 0)),
        "degrade_mode": bool(getattr(audit_result, "degrade_mode", False)),
        "metrics": telemetry.snapshot(elapsed_ms),
        "audit_score": getattr(audit_result, "score", None),
        "audit_passed": getattr(audit_result, "passed", None),
    }


def evaluate_case(
    case: EvaluationCase,
    mode: str,
    *,
    strict_llm: bool = True,
    repetition: int = 1,
) -> Dict[str, Any]:
    if mode not in VALID_MODES:
        raise ValueError(f"未知评测模式 {mode!r}；可选值：{', '.join(VALID_MODES)}")
    if mode not in applicable_modes(case, VALID_MODES):
        raise ValueError(f"{case.task_type} 样本不适用于 {mode} 模式")

    if case.task_type == "audit":
        result = _run_audit_case(case, strict_llm)
    else:
        result = _run_generation_case(
            case,
            use_rag=mode in {"rag_only", "full_workflow"},
            use_audit_loop=mode == "full_workflow",
            strict_llm=strict_llm,
        )

    content = result.pop("content")
    metrics = result.get("metrics") or {}
    successful_llm_calls = int(
        metrics.get(
            "llm_successful_calls",
            int(metrics.get("llm_calls", 0)) - int(metrics.get("llm_failed_calls", 0)),
        )
    )
    if strict_llm and (metrics.get("stub_calls", 0) or successful_llm_calls <= 0):
        raise RuntimeError("评测完整性失败：未获得真实 LLM 成功结果或检测到 Stub")

    compliance = _compliance_snapshot(content)
    # 真实请求在网络抖动或空响应后重试成功，仍属于可评测样本；失败次数单独作为
    # 稳定性指标报告。只有 Stub 或最终没有任何真实成功调用才污染质量统计。
    quality_eligible = not metrics.get("stub_calls", 0) and successful_llm_calls > 0
    predicted_audit_pass = result.get("audit_passed")
    normalized_success = (
        bool(result.get("pipeline_success"))
        if case.task_type == "audit"
        else bool(content.strip()) and bool(compliance["rule_passed"])
    )
    return {
        "case_id": case.id,
        "task_type": case.task_type,
        "mode": mode,
        "repetition": repetition,
        "prompt": case.prompt,
        "content": content,
        "content_chars": len(content),
        "success": normalized_success,
        "quality_eligible": quality_eligible,
        "term_coverage": _term_coverage(content, case.expected_terms),
        "compliance": compliance,
        "expected_audit_pass": case.expected_audit_pass,
        "audit_expectation_correct": (
            bool(predicted_audit_pass) == case.expected_audit_pass
            if case.task_type == "audit" and case.expected_audit_pass is not None else None
        ),
        **result,
    }


def summarize_results(
    results: List[Dict[str, Any]], metadata: Dict[str, Any] | None = None
) -> Dict[str, Any]:
    def is_quality_eligible(row: Dict[str, Any]) -> bool:
        explicit = row.get("quality_eligible")
        if explicit is not None:
            return bool(explicit)
        metrics = row.get("metrics") or {}
        if metrics.get("stub_calls", 0):
            return False
        if "llm_calls" not in metrics and "llm_successful_calls" not in metrics:
            # 兼容第一优先级修复前产生的历史结果文件。
            return not bool(metrics.get("llm_failed_calls", 0))
        successful = int(
            metrics.get(
                "llm_successful_calls",
                int(metrics.get("llm_calls", 0)) - int(metrics.get("llm_failed_calls", 0)),
            )
        )
        return successful > 0

    summary: Dict[str, Any] = {
        "total_runs": len(results),
        "error_runs": sum(bool(row.get("error")) for row in results),
        "total_tokens": sum(int((row.get("metrics") or {}).get("total_tokens", 0)) for row in results),
        "modes": {},
    }
    if metadata:
        summary["run_metadata"] = metadata
    for mode in VALID_MODES:
        rows = [row for row in results if row.get("mode") == mode]
        valid = [
            row for row in rows
            if not row.get("error") and is_quality_eligible(row)
        ]
        if not rows:
            continue
        latencies = [float((row.get("metrics") or {}).get("elapsed_ms", 0.0)) for row in valid]
        tokens = [int((row.get("metrics") or {}).get("total_tokens", 0)) for row in valid]
        coverages = [float(row["term_coverage"]) for row in valid if row.get("term_coverage") is not None]
        audit_rows = [row for row in valid if row.get("audit_expectation_correct") is not None]
        summary["modes"][mode] = {
            "runs": len(rows),
            "valid_runs": len(valid),
            "quality_eligible_runs": len(valid),
            "error_runs": len(rows) - len(valid),
            "success_rate": round(sum(bool(row.get("success")) for row in valid) / len(valid), 3) if valid else None,
            "pipeline_success_rate": round(
                sum(bool(row.get("pipeline_success")) for row in valid) / len(valid), 3
            ) if valid else None,
            "rule_compliance_rate": round(
                sum(bool((row.get("compliance") or {}).get("rule_passed")) for row in valid) / len(valid), 3
            ) if valid else None,
            "mean_latency_ms": round(statistics.fmean(latencies), 1) if latencies else None,
            "p95_latency_ms": round(sorted(latencies)[max(0, math.ceil(len(latencies) * 0.95) - 1)], 1) if latencies else None,
            "mean_tokens": round(statistics.fmean(tokens), 1) if tokens else None,
            "mean_iterations": round(statistics.fmean(float(row.get("iterations", 0)) for row in valid), 2) if valid else None,
            "mean_term_coverage": round(statistics.fmean(coverages), 3) if coverages else None,
            "audit_expectation_accuracy": round(
                sum(bool(row["audit_expectation_correct"]) for row in audit_rows) / len(audit_rows), 3
            ) if audit_rows else None,
            "failed_llm_calls": sum(int((row.get("metrics") or {}).get("llm_failed_calls", 0)) for row in rows),
            "stub_runs": sum(bool((row.get("metrics") or {}).get("stub_calls", 0)) for row in rows),
        }
    return summary


def save_results(
    results: List[Dict[str, Any]],
    output_dir: Path,
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Path]:
    """兼容一次性保存；正式 CLI 使用逐条 checkpoint。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    result_path = output_dir / f"evaluation-{stamp}.jsonl"
    summary_path = output_dir / f"evaluation-{stamp}-summary.json"
    result_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in results) + "\n",
        encoding="utf-8",
    )
    summary_path.write_text(
        json.dumps(summarize_results(results, metadata), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"results": result_path, "summary": summary_path}
