"""任务解析 Agent —— 把用户原始输入解析为结构化任务（ParsedTask）。"""
from __future__ import annotations

import re
from typing import Any, Dict

from ..llm import chat_structured, llm_available
from ..logging_setup import get_logger
from ..models import ParsedTask
from ..telemetry import strict_llm_required
from .prompts import PARSER_FEW_SHOTS, PARSER_SYSTEM_PROMPT

logger = get_logger("parser_agent")

_MARKETING_MARKERS = ("推广", "营销", "投放", "文案", "标题", "海报", "卖点")
_SCRIPT_MARKERS = ("剧本正文", "短剧正文", "剧情正文", "短剧内容", "剧本内容", "故事正文")
_SCRIPT_SEQUENCE_RE = re.compile(r"第[一二三四五六七八九十百零〇两\d]+[章集幕场]")


def _looks_like_script_generation(text: str) -> bool:
    """识别明确的正文创作意图，避免与营销文案混淆。"""
    value = text or ""
    if any(marker in value for marker in _MARKETING_MARKERS):
        return False
    if any(marker in value for marker in _SCRIPT_MARKERS) or _SCRIPT_SEQUENCE_RE.search(value):
        return True
    has_creation_verb = any(marker in value for marker in ("写", "创作", "生成", "续写"))
    has_story_object = any(marker in value for marker in ("短剧", "剧本", "剧情", "故事"))
    return has_creation_verb and has_story_object


def _enforce_explicit_intent(raw_input: str, task: ParsedTask) -> ParsedTask:
    """明确的正文请求优先于模型猜测和历史偏好。"""
    if task.task_type in {"audit", "qa"}:
        return task
    if any(marker in raw_input for marker in ("整理", "大纲", "人设", "结构", "规划")):
        return task
    if not _looks_like_script_generation(raw_input):
        return task
    explanation = task.raw_explanation.strip()
    correction = "明确包含短剧正文或章/集创作要求，固定为剧本正文任务"
    return task.model_copy(update={
        "task_type": "script_generation",
        "needs_retrieval": True,
        "target_length": max(800, int(task.target_length or 0)),
        "raw_explanation": f"{explanation}；{correction}" if explanation else correction,
    })


def run_parse(state: Dict[str, Any]) -> Dict[str, Any]:
    """LangGraph 节点：解析用户原始输入 → 返回含 parsed_task 的 dict。"""
    raw_input = state.get("raw_input") or ""
    run_mode = str(state.get("run_mode") or "quality").lower()
    logger.info(f"[Parser] 解析原始输入（{len(raw_input)} 字）")

    # 快速模式把可确定的意图识别留给规则引擎，省去一次完整 LLM 往返。
    # 严格评测仍强制使用真实 LLM，避免规则结果混入模型能力评测。
    if run_mode == "fast" and not strict_llm_required():
        task = _rule_based_parse(raw_input, fallback=False)
        logger.info(
            f"[Parser] 解析完成（快速规则）：task_type={task.task_type}, topic={task.topic}"
        )
        return {"parsed_task": task}

    if llm_available():
        try:
            task = chat_structured(
                pydantic_cls=ParsedTask,
                user_prompt=f"请解析以下用户输入：\n{raw_input}",
                system_prompt=PARSER_SYSTEM_PROMPT,
                few_shots=PARSER_FEW_SHOTS,
            )
            task = _enforce_explicit_intent(raw_input, task)
            logger.info(f"[Parser] 解析完成（LLM）：task_type={task.task_type}, topic={task.topic}")
            return {"parsed_task": task}
        except Exception as e:
            if strict_llm_required():
                raise
            logger.warning(f"[Parser] LLM 解析失败：{e}，走规则解析")

    if strict_llm_required():
        raise RuntimeError("严格评测模式禁止任务解析降级为规则模式")

    task = _rule_based_parse(raw_input)
    logger.info(f"[Parser] 解析完成（规则）：task_type={task.task_type}, topic={task.topic}")
    return {"parsed_task": task}


def _rule_based_parse(text: str, *, fallback: bool = True) -> ParsedTask:
    text_lower = (text or "").lower()
    task_type = "copywriting"
    needs_retrieval = True
    style = "爽文"

    if any(k in text for k in ("整理", "大纲", "章节", "人设", "结构")):
        task_type = "content_organize"
    elif any(k in text for k in ("合规", "审核", "检查", "是否违规", "审查")):
        task_type = "audit"
        needs_retrieval = False
    elif any(k in text for k in ("答疑", "问答", "?", "？", "规则", "怎么", "如何")):
        task_type = "qa"
    elif any(k in text for k in ("推广", "文案", "标题", "海报", "营销")):
        task_type = "copywriting"
    elif _looks_like_script_generation(text):
        task_type = "script_generation"

    if any(k in text for k in ("虐", "哭", "悲剧")):
        style = "虐恋"
    elif any(k in text for k in ("悬疑", "推理", "破案", "密室")):
        style = "悬疑"
    elif any(k in text for k in ("甜", "宠", "恋爱", "浪漫")):
        style = "甜宠"
    elif any(k in text for k in ("都市", "职场")):
        style = "都市"
    elif any(k in text for k in ("古装", "穿越", "重生")):
        style = "古装"
    elif any(k in text for k in ("科幻", "未来", "外星")):
        style = "科幻"

    tokens = [t for t in re.sub(r"[，。,.!?！？\s]+", " ", text).split() if t]
    keywords = list({t for t in tokens if 1 < len(t) <= 8})[:8] or ["短剧"]

    target_length = 1000 if task_type == "script_generation" else 500
    m = re.search(r"(\d{2,5})\s*(?:字|词)", text)
    if m:
        try:
            target_length = int(m.group(1))
        except Exception:
            target_length = 500

    topic = text[:40] or "短剧"
    return ParsedTask(
        task_type=task_type,
        topic=topic,
        style=style,
        target_length=target_length,
        keywords=keywords,
        needs_retrieval=needs_retrieval,
        requirements=text,
        raw_explanation=(
            "[降级] 基于规则引擎的启发式解析"
            if fallback
            else "[快速模式] 基于规则引擎的启发式解析"
        ),
    )
