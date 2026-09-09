"""人工评测记录的轻量 JSONL 存储。"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from .config import settings


RATING_FIELDS = (
    "hook",
    "pacing",
    "character_consistency",
    "shootability",
    "compliance",
)


class HumanReview(BaseModel):
    user_id: str = "guest"
    session_id: str = ""
    case_id: str = "interactive"
    mode: str = "full_workflow"
    hook: int = Field(..., ge=1, le=5)
    pacing: int = Field(..., ge=1, le=5)
    character_consistency: int = Field(..., ge=1, le=5)
    shootability: int = Field(..., ge=1, le=5)
    compliance: int = Field(..., ge=1, le=5)
    notes: str = Field(default="", max_length=2000)


class HumanReviewStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or settings.absolute_evaluation_path
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "human_reviews.jsonl"
        self._lock = threading.RLock()

    def append(self, review: HumanReview) -> Dict[str, Any]:
        item = {
            "review_id": f"R_{uuid.uuid4().hex[:12]}",
            "created_at": time.time(),
            **review.model_dump(),
        }
        item["average"] = round(
            sum(float(item[field]) for field in RATING_FIELDS) / len(RATING_FIELDS), 2
        )
        line = json.dumps(item, ensure_ascii=False)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        return item

    def list(self, user_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        with self._lock:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        items: List[Dict[str, Any]] = []
        for line in reversed(lines):
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("user_id") == user_id:
                items.append(item)
            if len(items) >= limit:
                break
        return items

    def summary(self, user_id: str) -> Dict[str, Any]:
        items = self.list(user_id, limit=10_000)
        if not items:
            return {"total": 0, "averages": {field: None for field in RATING_FIELDS}, "by_mode": {}}
        averages = {
            field: round(sum(float(item[field]) for item in items) / len(items), 2)
            for field in RATING_FIELDS
        }
        by_mode: Dict[str, Dict[str, Any]] = {}
        for item in items:
            bucket = by_mode.setdefault(item.get("mode", "unknown"), {"total": 0, "score_sum": 0.0})
            bucket["total"] += 1
            bucket["score_sum"] += float(item.get("average", 0.0))
        for bucket in by_mode.values():
            bucket["average"] = round(bucket.pop("score_sum") / bucket["total"], 2)
        return {"total": len(items), "averages": averages, "by_mode": by_mode}


_store: HumanReviewStore | None = None


def get_human_review_store() -> HumanReviewStore:
    global _store
    if _store is None:
        _store = HumanReviewStore()
    return _store
