"""运行三模式评测并输出逐条 JSONL 与汇总 JSON。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import SecretStr

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from drama_agent.evaluation import VALID_MODES, evaluate_case, load_cases, save_results  # noqa: E402
from drama_agent.config import settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="短剧多智能体三模式可复现评测")
    parser.add_argument("--dataset", type=Path, default=ROOT / "evals" / "dataset.jsonl")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "evals" / "results")
    parser.add_argument("--modes", nargs="+", choices=VALID_MODES, default=list(VALID_MODES))
    parser.add_argument("--limit", type=int, default=None, help="仅运行前 N 个样本，便于快速验证")
    parser.add_argument("--offline", action="store_true", help="禁用真实 LLM 和 embedding，仅验证评测管线")
    args = parser.parse_args()

    if args.offline:
        settings.llm_api_key = SecretStr("")
        settings.embedding_provider = "hash"

    cases = load_cases(args.dataset)
    if args.limit is not None:
        cases = cases[: max(0, args.limit)]
    results = []
    total = len(cases) * len(args.modes)
    for index, case in enumerate(cases, 1):
        for mode in args.modes:
            print(f"[{len(results) + 1}/{total}] {case.id} / {mode}", flush=True)
            try:
                results.append(evaluate_case(case, mode))
            except Exception as exc:
                results.append({
                    "case_id": case.id,
                    "task_type": case.task_type,
                    "mode": mode,
                    "prompt": case.prompt,
                    "success": False,
                    "error": f"{type(exc).__name__}: {exc}",
                })
    paths = save_results(results, args.output_dir)
    print(json.dumps({key: str(value) for key, value in paths.items()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
