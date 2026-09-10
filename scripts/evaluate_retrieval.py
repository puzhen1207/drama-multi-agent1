"""独立评测公共知识库检索，不调用 LLM。"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from drama_agent.evaluation import load_cases  # noqa: E402
from drama_agent.config import settings  # noqa: E402
from drama_agent.tools.vector_retriever import get_vector_store  # noqa: E402


def evaluate(dataset: Path, top_k: int) -> Dict[str, Any]:
    cases = [case for case in load_cases(dataset) if case.expected_source_titles]
    store = get_vector_store()
    rows: List[Dict[str, Any]] = []
    for case in cases:
        materials = store.search(case.prompt, top_k_parent=top_k)
        titles = [item.title for item in materials]
        expected = case.expected_source_titles
        hits = sum(title in titles for title in expected)
        rows.append({
            "case_id": case.id,
            "task_type": case.task_type,
            "expected_source_titles": expected,
            "retrieved_titles": titles,
            "source_recall": round(hits / len(expected), 3),
            "top1_hit": bool(titles and titles[0] in expected),
        })
    count = len(rows)
    knowledge_digest = hashlib.sha256()
    for file_path in sorted(
        item for item in settings.absolute_knowledge_path.rglob("*") if item.is_file()
    ):
        knowledge_digest.update(
            file_path.relative_to(settings.absolute_knowledge_path).as_posix().encode("utf-8")
        )
        knowledge_digest.update(file_path.read_bytes())
    return {
        "dataset": str(dataset.resolve()),
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "knowledge_sha256": knowledge_digest.hexdigest(),
        "embedding_model": settings.embedding_model,
        "top_k": top_k,
        "evaluated_cases": count,
        "top1_accuracy": round(sum(row["top1_hit"] for row in rows) / count, 3) if count else None,
        "source_recall_at_k": round(sum(row["source_recall"] for row in rows) / count, 3) if count else None,
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="评测 RAG 来源召回率（不调用 LLM）")
    parser.add_argument("--dataset", type=Path, default=ROOT / "evals" / "dataset.jsonl")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evals" / "results" / "retrieval-evaluation.json",
    )
    args = parser.parse_args()
    if args.top_k < 1:
        parser.error("--top-k 必须至少为 1")
    report = evaluate(args.dataset, args.top_k)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
