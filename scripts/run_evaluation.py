"""运行可恢复的受控三模式评测，并逐条写入结果。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from pydantic import SecretStr

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from drama_agent.config import settings  # noqa: E402
from drama_agent.evaluation import (  # noqa: E402
    VALID_MODES,
    applicable_modes,
    evaluate_case,
    load_cases,
    summarize_results,
)
from drama_agent.llm import llm_available  # noqa: E402
from drama_agent.telemetry import current_telemetry  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return "unknown"


def _atomic_json(path: Path, value: Dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"断点文件 {path}:{line_no} 损坏，请先修复该行") from exc
    return rows


def _append_result(path: Path, row: Dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _result_key(row: Dict[str, Any]) -> tuple:
    return row.get("case_id"), row.get("mode"), int(row.get("repetition", 1))


def _latest_results(rows: List[Dict[str, Any]]) -> Dict[tuple, Dict[str, Any]]:
    """同一 case/mode/repetition 重试时，以最后一次尝试作为质量结果。"""
    latest: Dict[tuple, Dict[str, Any]] = {}
    for row in rows:
        latest[_result_key(row)] = row
    return latest


def _paths(output_dir: Path, resume: Path | None) -> Dict[str, Path]:
    if resume:
        result_path = resume.resolve()
        name = result_path.name
        stem = name[:-6] if name.endswith(".jsonl") else result_path.stem
    else:
        stem = f"evaluation-{time.strftime('%Y%m%d-%H%M%S')}"
        result_path = output_dir / f"{stem}.jsonl"
    return {
        "results": result_path,
        "summary": result_path.parent / f"{stem}-summary.json",
        "manifest": result_path.parent / f"{stem}-manifest.json",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="短剧多智能体受控、可恢复评测")
    parser.add_argument("--dataset", type=Path, default=ROOT / "evals" / "dataset.jsonl")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "evals" / "results")
    parser.add_argument("--modes", nargs="+", choices=VALID_MODES, default=list(VALID_MODES))
    parser.add_argument("--limit", type=int, default=None, help="仅运行前 N 个样本")
    parser.add_argument("--repetitions", type=int, default=1, help="每组重复次数")
    parser.add_argument("--temperature", type=float, default=0.0, help="评测温度，默认 0")
    parser.add_argument("--max-output-tokens", type=int, default=1200, help="单次最大输出 Token")
    parser.add_argument("--max-total-tokens", type=int, default=1_000_000, help="累计 Token 预算")
    parser.add_argument("--resume", type=Path, default=None, help="从已有 JSONL 断点继续")
    parser.add_argument("--retry-errors", action="store_true", help="恢复时重新执行失败样本")
    parser.add_argument("--offline", action="store_true", help="仅验证管线，允许 Stub，不得用于质量结论")
    parser.add_argument("--allow-stub", action="store_true", help="允许 Stub；结果会明确标为非正式运行")
    args = parser.parse_args()

    if args.repetitions < 1:
        parser.error("--repetitions 必须至少为 1")
    if args.max_output_tokens < 1 or args.max_total_tokens < 1:
        parser.error("Token 上限必须是正整数")

    strict_llm = not (args.offline or args.allow_stub)
    settings.llm_temperature = args.temperature
    settings.llm_max_tokens = args.max_output_tokens
    # 评测只允许公共知识库，避免个人记忆污染实验。
    settings.enable_user_memory = False
    if args.offline:
        settings.llm_api_key = SecretStr("")
        settings.embedding_provider = "hash"
    if strict_llm and not llm_available():
        parser.error("严格评测需要有效 LLM 配置；若只验证管线，请显式使用 --offline")

    dataset = args.dataset.resolve()
    cases = load_cases(dataset)
    if args.limit is not None:
        cases = cases[: max(0, args.limit)]
    plan = [
        (case, mode, repetition)
        for repetition in range(1, args.repetitions + 1)
        for case in cases
        for mode in applicable_modes(case, args.modes)
    ]
    plan_sha256 = hashlib.sha256(json.dumps(
        [(case.id, mode, repetition) for case, mode, repetition in plan],
        ensure_ascii=False,
    ).encode("utf-8")).hexdigest()

    paths = _paths(args.output_dir.resolve(), args.resume)
    paths["results"].parent.mkdir(parents=True, exist_ok=True)
    previous = _load_jsonl(paths["results"])
    latest = _latest_results(previous)
    completed = {
        key for key, row in latest.items()
        if not (args.retry_errors and row.get("error"))
    }
    metadata: Dict[str, Any] = {
        "started_at": time.time(),
        "dataset": str(dataset),
        "dataset_sha256": _sha256(dataset),
        "git_commit": _git_commit(),
        "llm_base_url": settings.llm_base_url,
        "llm_model": settings.llm_model,
        "temperature": settings.llm_temperature,
        "max_output_tokens": settings.llm_max_tokens,
        "max_total_tokens": args.max_total_tokens,
        "modes": list(args.modes),
        "repetitions": args.repetitions,
        "strict_llm": strict_llm,
        "offline": bool(args.offline),
        "user_memory_enabled": False,
        "planned_runs": len(plan),
        "plan_sha256": plan_sha256,
    }

    if args.resume and paths["manifest"].exists():
        old = json.loads(paths["manifest"].read_text(encoding="utf-8"))
        checks = (
            "dataset_sha256", "plan_sha256", "llm_model", "temperature",
            "max_output_tokens", "modes", "repetitions",
        )
        mismatches = [name for name in checks if old.get(name) != metadata.get(name)]
        if mismatches:
            parser.error("断点配置不一致：" + ", ".join(mismatches))
        metadata["started_at"] = old.get("started_at", metadata["started_at"])
        metadata["resumed_at"] = time.time()
    _atomic_json(paths["manifest"], metadata)

    results_by_key = dict(latest)
    used_tokens = sum(int((row.get("metrics") or {}).get("total_tokens", 0)) for row in previous)
    attempt_count = len(previous)
    pending = [item for item in plan if (item[0].id, item[1], item[2]) not in completed]
    stop_reason = None
    for index, (case, mode, repetition) in enumerate(pending, 1):
        if used_tokens >= args.max_total_tokens:
            stop_reason = f"token_budget_reached:{used_tokens}"
            break
        print(
            f"[{index}/{len(pending)}] {case.id} / {mode} / repeat={repetition} / tokens={used_tokens}",
            flush=True,
        )
        try:
            row = evaluate_case(
                case, mode, strict_llm=strict_llm, repetition=repetition
            )
        except Exception as exc:
            telemetry = current_telemetry()
            row = {
                "case_id": case.id,
                "task_type": case.task_type,
                "mode": mode,
                "repetition": repetition,
                "prompt": case.prompt,
                "success": False,
                "pipeline_success": False,
                "quality_eligible": False,
                "error": f"{type(exc).__name__}: {exc}",
                "metrics": telemetry.snapshot() if telemetry else {},
            }
        _append_result(paths["results"], row)
        attempt_count += 1
        results_by_key[_result_key(row)] = row
        used_tokens += int((row.get("metrics") or {}).get("total_tokens", 0))
        current_results = list(results_by_key.values())
        checkpoint = summarize_results(current_results, metadata)
        checkpoint["status"] = "running"
        checkpoint["attempts"] = attempt_count
        checkpoint["total_tokens_consumed"] = used_tokens
        checkpoint["completed_runs"] = len(current_results)
        checkpoint["remaining_runs"] = max(0, len(plan) - len(current_results))
        _atomic_json(paths["summary"], checkpoint)

    current_results = list(results_by_key.values())
    summary = summarize_results(current_results, metadata)
    has_errors = any(row.get("error") for row in current_results)
    summary["status"] = (
        "budget_stopped" if stop_reason
        else "complete_with_errors" if has_errors
        else "complete"
    )
    summary["stop_reason"] = stop_reason
    summary["attempts"] = attempt_count
    summary["total_tokens_consumed"] = used_tokens
    summary["completed_runs"] = len(current_results)
    summary["remaining_runs"] = max(0, len(plan) - len(current_results))
    _atomic_json(paths["summary"], summary)
    print(json.dumps({key: str(value) for key, value in paths.items()}, ensure_ascii=False, indent=2))
    print(json.dumps({"status": summary["status"], "total_tokens": used_tokens}, ensure_ascii=False))
    return 2 if stop_reason else 1 if has_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
