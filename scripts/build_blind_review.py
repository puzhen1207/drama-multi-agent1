"""从多模式评测结果生成匿名人工盲评包和单独的解盲密钥。"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any, Dict, List


RATING_FIELDS = [
    "hook", "pacing", "character_consistency", "shootability", "compliance",
]


def _load_latest(path: Path) -> List[Dict[str, Any]]:
    latest: Dict[tuple, Dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        key = (row.get("case_id"), int(row.get("repetition", 1)), row.get("mode"))
        latest[key] = row
    return list(latest.values())


def build_packets(rows: List[Dict[str, Any]], seed: int = 20260910) -> tuple[List[dict], List[dict]]:
    grouped: Dict[tuple, List[Dict[str, Any]]] = {}
    for row in rows:
        if row.get("error") or not row.get("quality_eligible") or not row.get("content"):
            continue
        key = (row.get("case_id"), int(row.get("repetition", 1)))
        grouped.setdefault(key, []).append(row)

    rng = random.Random(seed)
    packets: List[dict] = []
    answer_key: List[dict] = []
    for (case_id, repetition), candidates in sorted(grouped.items()):
        if len(candidates) < 2:
            continue
        shuffled = list(candidates)
        rng.shuffle(shuffled)
        labels = [chr(ord("A") + index) for index in range(len(shuffled))]
        packets.append({
            "review_id": f"{case_id}-r{repetition}",
            "case_id": case_id,
            "repetition": repetition,
            "task_type": shuffled[0].get("task_type"),
            "prompt": shuffled[0].get("prompt"),
            "candidates": [
                {
                    "label": label,
                    "content": row["content"],
                    "scores": {field: None for field in RATING_FIELDS},
                    "notes": "",
                }
                for label, row in zip(labels, shuffled)
            ],
            "preferred_label": None,
        })
        answer_key.append({
            "review_id": f"{case_id}-r{repetition}",
            "mapping": {label: row.get("mode") for label, row in zip(labels, shuffled)},
        })
    return packets, answer_key


def main() -> int:
    parser = argparse.ArgumentParser(description="生成隐藏模式名称的人工盲评包")
    parser.add_argument("results", type=Path, help="三模式评测 JSONL")
    parser.add_argument("--output", type=Path, default=Path("evals/results/blind-review.jsonl"))
    parser.add_argument("--key-output", type=Path, default=Path("evals/results/blind-review-key.json"))
    parser.add_argument("--seed", type=int, default=20260910)
    args = parser.parse_args()
    packets, answer_key = build_packets(_load_latest(args.results), args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.key_output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in packets) + ("\n" if packets else ""),
        encoding="utf-8",
    )
    args.key_output.write_text(
        json.dumps({"seed": args.seed, "items": answer_key}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"review_items": len(packets), "packet": str(args.output), "key": str(args.key_output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
