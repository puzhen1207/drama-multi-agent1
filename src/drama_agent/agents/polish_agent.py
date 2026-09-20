"""内容润色 Agent —— 基于素材 + 草稿 + 审核反馈 + 会话上下文，生成/重写内容。"""
from __future__ import annotations

from typing import Any, Dict, List

from ..exceptions import LLMServiceError
from ..llm import chat, llm_available
from ..logging_setup import get_logger
from ..models import RetrievedMaterial
from ..telemetry import record_stub_call, strict_llm_required
from ..tools.text_processor import tool_normalize_text
from .prompts import (
    COPYWRITING_SYSTEM_PROMPT,
    ORGANIZE_SYSTEM_PROMPT,
    POLISH_SYSTEM_PROMPT,
    QA_SYSTEM_PROMPT,
    SCRIPT_SYSTEM_PROMPT,
    build_task_user_prompt,
)

logger = get_logger("polish_agent")

_MARKETING_SCAFFOLD = ("【投放标题】", "【推广标题】", "【标题备选】", "【核心卖点】", "【推广文案】")


def _has_marketing_scaffold(content: str) -> bool:
    return any(marker in (content or "") for marker in _MARKETING_SCAFFOLD)


def run_copywriting(state: Dict[str, Any]) -> Dict[str, Any]:
    return _run_task_generation(state, "copywriting")


def run_script(state: Dict[str, Any]) -> Dict[str, Any]:
    return _run_task_generation(state, "script_generation")


def run_organize(state: Dict[str, Any]) -> Dict[str, Any]:
    return _run_task_generation(state, "content_organize")


def run_qa(state: Dict[str, Any]) -> Dict[str, Any]:
    return _run_task_generation(state, "qa")


def run_rewrite(state: Dict[str, Any]) -> Dict[str, Any]:
    parsed = state.get("parsed_task")
    task_type = getattr(parsed, "task_type", "copywriting") if parsed is not None else "copywriting"
    return _run_task_generation(state, task_type)


# 兼容原有导入和外部调用。
run_polish = run_rewrite


def _run_task_generation(state: Dict[str, Any], forced_task_type: str) -> Dict[str, Any]:
    parsed = state.get("parsed_task")
    materials: List[Any] = state.get("retrieved_materials") or []
    draft = state.get("draft_content") or ""
    audit = state.get("audit_result")
    session_context = state.get("session_context") or ""
    user_profile_text = state.get("user_profile_text") or ""

    if parsed is not None:
        task_type = forced_task_type
        topic = getattr(parsed, "topic", "")
        style = getattr(parsed, "style", "爽文")
        target_length = int(getattr(parsed, "target_length", 500))
        requirements = getattr(parsed, "requirements", "")
    else:
        task_type = "copywriting"
        topic = state.get("raw_input", "")[:40]
        style = "爽文"
        target_length = 500
        requirements = state.get("raw_input", "")

    # 组装素材文本
    materials_text = _format_materials(materials) if isinstance(materials, list) else ""

    # 审核反馈
    audit_text = ""
    if audit is not None:
        passed = getattr(audit, "passed", True)
        issues = getattr(audit, "issues", [])
        if not passed and issues:
            lines: List[str] = []
            for issue in issues:
                if isinstance(issue, dict):
                    level = issue.get("level", "")
                    cat = issue.get("category", "")
                    pos = issue.get("position", "")
                    sugg = issue.get("suggestion", "")
                    lines.append(f"- 【{level}】{cat}：{pos} -> {sugg}")
                else:
                    try:
                        lines.append(f"- 【{getattr(issue, 'level', '')}】"
                                     f"{getattr(issue, 'category', '')}："
                                     f"{getattr(issue, 'position', '')} -> "
                                     f"{getattr(issue, 'suggestion', '')}")
                    except Exception:
                        pass
            if getattr(audit, "summary", ""):
                lines.append(f"- 整体结论：{audit.summary}")
            audit_text = "\n".join(lines)

    user_prompt = build_task_user_prompt(
        task_type=task_type,
        topic=topic,
        style=style,
        target_length=target_length,
        requirements=requirements,
        materials=materials_text,
        draft=draft,
        audit_feedback=audit_text,
        session_context=session_context,
        user_profile_text=user_profile_text,
    )

    n_materials = len(materials) if isinstance(materials, list) else 0
    audit_flag = "有" if audit_text else "无"
    ctx_flag = "有" if session_context else "无"
    logger.info(
        f"[Polish] iter={state.get('iteration_count', 0)}, materials={n_materials}, "
        f"audit_feedback={audit_flag}, has_context={ctx_flag}"
    )

    content: str
    system_prompts = {
        "script_generation": SCRIPT_SYSTEM_PROMPT,
        "copywriting": COPYWRITING_SYSTEM_PROMPT,
        "content_organize": ORGANIZE_SYSTEM_PROMPT,
        "qa": QA_SYSTEM_PROMPT,
    }
    system_prompt = system_prompts.get(task_type, POLISH_SYSTEM_PROMPT)
    if llm_available():
        content = chat(user_prompt=user_prompt, system_prompt=system_prompt)
        if task_type == "script_generation" and _has_marketing_scaffold(content):
            logger.warning("[Polish] 剧本正文误用了营销结构，执行一次格式纠正")
            correction_prompt = (
                f"{user_prompt}\n\n"
                "【格式纠正】上一次回答错误地使用了投放标题、核心卖点或推广文案结构。"
                "请完全重写，只保留指定章节的剧本正文，以场景、动作和人物对白推进。"
                "不要解释错误，也不要输出任何营销栏目。\n\n"
                f"【上一次不合格输出】\n{content[:2400]}"
            )
            content = chat(user_prompt=correction_prompt, system_prompt=system_prompt)
    else:
        if strict_llm_required():
            raise LLMServiceError("严格评测模式禁止内容生成降级为 Stub")
        content = _stub_polish(task_type, topic, style, target_length, materials_text)
        record_stub_call(len(user_prompt) + len(system_prompt), len(content))

    content = tool_normalize_text(content)

    # 兜底：如果 LLM 意外返回短内容，补一个 stub
    if not content or len(content.strip()) < 50:
        if strict_llm_required():
            raise LLMServiceError("严格评测模式下模型输出过短，拒绝使用 Stub 补全")
        logger.warning("[Polish] 生成内容过短，补本地模板兜底")
        content = _stub_polish(task_type, topic, style, target_length, materials_text)
        record_stub_call(len(user_prompt) + len(system_prompt), len(content))

    logger.info(f"[Polish] 生成内容 {len(content)} 字")
    return {"draft_content": content}


def _format_materials(materials: List[Any]) -> str:
    items: List[str] = []
    for i, m in enumerate(materials):
        if isinstance(m, RetrievedMaterial):
            title, category, content = m.title, m.category, m.content
        elif isinstance(m, dict):
            title = m.get("title", "")
            category = m.get("category", "")
            content = m.get("content", "")
        else:
            continue
        snippet = content[:300]
        items.append(f"--- 素材 {i+1}（分类：{category}，标题：{title}）\n{snippet}\n")
    return "\n".join(items)


def _stub_polish(task_type: str, topic: str, style: str, length: int, materials: str) -> str:
    if task_type == "script_generation":
        return (
            f"《{topic}》\n\n"
            "【第一章】\n\n"
            "【场景：旧宅前院·白天】\n"
            "众人围在院中，主角站在人群之外。所有人都把他当成笑话，"
            "却没人注意到他已经看穿眼前的局。\n\n"
            "【主角】：你们说完了吗？\n"
            "院中笑声骤停。他抬起头，第一次没有露出平日的傻笑。\n\n"
            "门外忽然传来急促脚步声，一个陌生人推门而入，径直跪在他面前。\n"
            "【陌生人】：少爷，我们终于找到您了。\n\n"
            f"（{style}风格 · STUB 模式 · 目标约 {length} 字；配置 LLM_API_KEY 后生成完整正文。）"
        )
    if task_type == "copywriting":
        return (
            f"【推广标题】{topic}：命运反转就在这一刻\n\n"
            "她以为自己已经输掉一切，直到那份被隐藏多年的真相突然出现。\n"
            "旧关系重新洗牌，最不起眼的人站到了全场中央。\n"
            "下一秒，她做出的选择让所有人措手不及。\n\n"
            f"（{style}风格 · STUB 模式 · 目标约 {length} 字；配置 LLM_API_KEY 后生成完整版本。）"
        )
    if task_type == "qa":
        refs = materials[:500] if materials else "当前知识库没有命中可用素材。"
        return (
            f"【问题】{topic}\n\n"
            "【回答】当前处于 STUB 模式，无法进行可靠的语义问答。以下仅展示检索结果，"
            "正式使用前需要配置 LLM，并对规则类信息进行人工核验。\n\n"
            f"【检索参考】\n{refs}"
        )
    if task_type == "content_organize":
        return (
            f"【《{topic}》{style}方向大纲（STUB 模式）】\n\n"
            "第 1 集：建立人物目标与核心困境，结尾抛出第一次反转。\n"
            "第 2 集：冲突升级，关键关系发生变化，以更大的代价制造钩子。\n"
            "第 3 集：阶段高潮，揭示隐藏信息，并为后续剧情留下悬念。\n\n"
            f"（目标约 {length} 字；配置 LLM_API_KEY 后生成完整大纲。）"
        )
    body = (
        f"【《{topic}》{style}风格短剧（STUB 模式 · 未接入真实 LLM）】\n\n"
        f"第 1 幕：开场冲突。主角在一次意外事件中身陷绝境，强烈情绪钩子吸引读者。\n"
        f"第 2 幕：反转升级。关键配角登场，局势反复反转，节奏紧凑。\n"
        f"第 3 幕：高潮与钩子。冲突达到顶点，以悬念结尾，吸引读者看下一集。\n\n"
        f"【人设】\n"
        f"- 主角：外柔内刚，心思缜密，关键时刻爆发。\n"
        f"- 配角：强势霸道，控制欲强，对主角专一。\n\n"
        f"（字数目标 {length} 字，task={task_type}）\n\n"
        f"提示：请在项目根目录的 .env 中配置 LLM_API_KEY 后，可获得高质量生成。\n"
    )
    if materials:
        body += f"\n【参考素材摘要】\n基于素材：{materials[:200]}\n"
    return body
