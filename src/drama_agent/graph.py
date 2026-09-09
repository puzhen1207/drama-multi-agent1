"""LangGraph 工作流调度。

使用 LangGraph StateGraph 编排：parse → (retrieve?) → typed generation ↔ audit。
通过 Annotated overwrite reducer 保证 draft_content / audit_result 等字段为覆盖语义，
避免不同版本 state merge 导致内容丢失。
"""
from __future__ import annotations

import difflib
import time
from typing import Any, Callable, Dict, List, Literal, Optional

from langgraph.graph import END, START, StateGraph
from typing_extensions import Annotated, TypedDict

from .agents.audit_agent import run_audit
from .agents.parser_agent import run_parse
from .agents.polish_agent import (
    run_copywriting,
    run_organize,
    run_qa,
    run_rewrite,
)
from .agents.retriever_agent import run_retrieve
from .config import settings
from .exceptions import DramaAgentError
from .logging_setup import get_logger
from .memory import get_session_manager
from .models import (
    AuditResult,
    FinalResponse,
    ParsedTask,
    ReflectionEntry,
    RevisionRecord,
    WorkflowMetrics,
)
from .telemetry import record_node, record_retrieval, start_run_telemetry

logger = get_logger("graph")


# ============= LangGraph State =============


def _overwrite(_old: Any, new: Any) -> Any:
    """Reducer：节点返回值覆盖旧值（非 append/merge）。"""
    return new


class WorkflowGraphState(TypedDict, total=False):
    """LangGraph 共享 state。"""

    raw_input: str
    user_id: str
    session_id: Optional[str]
    session_context: str
    user_profile_text: str
    parsed_task: Annotated[Optional[ParsedTask], _overwrite]
    retrieved_materials: Annotated[List[Any], _overwrite]
    draft_content: Annotated[str, _overwrite]
    audit_result: Annotated[Optional[AuditResult], _overwrite]
    iteration_count: Annotated[int, _overwrite]
    max_iteration: int
    degrade_mode: Annotated[bool, _overwrite]
    error_info: Annotated[str, _overwrite]
    need_more_retrieval: bool
    node_failed: Annotated[str, _overwrite]
    revision_history: Annotated[List[Dict[str, Any]], _overwrite]


# ============= 事件发射（前端可视化用）=============


def _emit(ctx: Dict[str, Any], event: dict) -> None:
    cb = ctx.get("event_callback")
    if cb is not None:
        try:
            cb(event)
        except Exception:
            pass


# ============= 节点包装：异常捕获 + 事件发射 =============


def _safe_node(
    ctx: Dict[str, Any],
    func: Callable[..., Optional[Dict[str, Any]]],
    name: str,
    state: Dict[str, Any],
) -> Dict[str, Any]:
    """把一个 Agent 函数包装为 LangGraph 节点；异常时写入 degrade_mode，不中断工作流。"""
    _emit(ctx, {"type": "node_start", "node": name, "ts": time.time()})
    t0 = time.time()
    try:
        result = func(state) or {}
        dt_ms = (time.time() - t0) * 1000
        record_node(name, dt_ms)
        if name == "retrieve_node":
            record_retrieval(len(result.get("retrieved_materials") or []))
        logger.info(f"node[{name}] ok 耗时={dt_ms:.0f}ms")
        _emit(ctx, {
            "type": "node_done",
            "node": name,
            "ts": time.time(),
            "duration_ms": round(dt_ms, 1),
            "summary": _node_output_summary(name, result),
        })
        return result
    except DramaAgentError as e:
        dt_ms = (time.time() - t0) * 1000
        record_node(name, dt_ms, str(e))
        logger.warning(f"node[{name}] DramaAgentError: {e}")
        _emit(ctx, {"type": "node_error", "node": name, "error": str(e)})
        return {"degrade_mode": True, "error_info": str(e), "node_failed": name}
    except Exception as e:
        dt_ms = (time.time() - t0) * 1000
        record_node(name, dt_ms, f"{type(e).__name__}: {e}")
        logger.exception(f"node[{name}] 未预期异常: {e}")
        _emit(ctx, {"type": "node_error", "node": name, "error": f"{type(e).__name__}: {e}"})
        return {
            "degrade_mode": True,
            "error_info": f"{type(e).__name__}: {e}",
            "node_failed": name,
        }


def _node_output_summary(name: str, result: Dict[str, Any]) -> str:
    try:
        if name == "parse_node":
            pt = result.get("parsed_task")
            if pt is not None:
                task_type = getattr(pt, "task_type", "?") if not isinstance(pt, dict) else pt.get("task_type", "?")
                topic = getattr(pt, "topic", "") if not isinstance(pt, dict) else pt.get("topic", "")
                return f"类型={task_type}, 主题={str(topic)[:30]}"
        elif name == "retrieve_node":
            mats = result.get("retrieved_materials") or []
            return f"召回 {len(mats)} 条素材"
        elif name in {"copywriting_node", "organize_node", "qa_node", "rewrite_node"}:
            content = result.get("draft_content", "")
            return f"生成内容 {len(str(content))} 字"
        elif name == "audit_node":
            a = result.get("audit_result")
            if a is not None:
                score = getattr(a, "score", 0) if not isinstance(a, dict) else a.get("score", 0)
                issues = getattr(a, "issues", []) if not isinstance(a, dict) else a.get("issues", [])
                passed = getattr(a, "passed", False) if not isinstance(a, dict) else a.get("passed", False)
                return f"passed={passed}, score={score}, issues={len(issues)}"
    except Exception:
        pass
    return "节点完成"


def _audit_score(audit: Any) -> float:
    if audit is None:
        return 0.0
    value = getattr(audit, "score", 0.0) if not isinstance(audit, dict) else audit.get("score", 0.0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _issue_summaries(audit: Any) -> List[str]:
    issues = getattr(audit, "issues", []) if not isinstance(audit, dict) else audit.get("issues", [])
    result: List[str] = []
    for issue in issues or []:
        if isinstance(issue, dict):
            level = issue.get("level", "")
            category = issue.get("category", "")
            suggestion = issue.get("suggestion", "")
        else:
            level = getattr(issue, "level", "")
            category = getattr(issue, "category", "")
            suggestion = getattr(issue, "suggestion", "")
        result.append(f"[{level}] {category}: {suggestion}".strip()[:240])
    return result


def _make_unified_diff(before: str, after: str) -> str:
    lines = list(difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile="修改前",
        tofile="修改后",
        lineterm="",
    ))
    return "\n".join(lines[:160])[:12000]


# ============= 路由决策 =============


def _generation_node(state: WorkflowGraphState) -> str:
    parsed = state.get("parsed_task")
    task_type = (
        getattr(parsed, "task_type", "copywriting")
        if not isinstance(parsed, dict)
        else parsed.get("task_type", "copywriting")
    )
    return {
        "content_organize": "organize_node",
        "qa": "qa_node",
        "copywriting": "copywriting_node",
    }.get(task_type, "copywriting_node")


def _route_after_parse(state: WorkflowGraphState) -> str:
    parsed = state.get("parsed_task")
    if parsed is None:
        return "copywriting_node"
    task_type = (
        getattr(parsed, "task_type", "copywriting")
        if not isinstance(parsed, dict)
        else parsed.get("task_type", "copywriting")
    )
    if task_type == "audit":
        return "audit_input_node"
    needs_retrieval = (
        getattr(parsed, "needs_retrieval", True)
        if not isinstance(parsed, dict)
        else parsed.get("needs_retrieval", True)
    )
    degrade = state.get("degrade_mode", False)
    if needs_retrieval and not degrade:
        return "retrieve_node"
    return _generation_node(state)


def _route_after_retrieve(state: WorkflowGraphState) -> str:
    return _generation_node(state)


def _route_after_audit(state: WorkflowGraphState) -> Literal["rewrite_node", "__end__"]:
    parsed = state.get("parsed_task")
    task_type = (
        getattr(parsed, "task_type", "")
        if not isinstance(parsed, dict)
        else parsed.get("task_type", "")
    )
    # 审核任务的目标是报告风险，不应改写用户提交的原文。
    if task_type == "audit":
        return END
    audit = state.get("audit_result")
    passed = False
    if audit is not None:
        passed = (
            getattr(audit, "passed", False)
            if not isinstance(audit, dict)
            else audit.get("passed", False)
        )
    max_iter = int(state.get("max_iteration") or 3)
    iteration_count = int(state.get("iteration_count") or 0)
    if passed or iteration_count >= max_iter:
        return END
    logger.info(f"[Graph] 第 {iteration_count} 轮审核未通过，继续重写")
    return "rewrite_node"


# ============= LangGraph 构建 =============


def _build_workflow_graph(ctx: Dict[str, Any]):
    """构建并编译 LangGraph 工作流（节点闭包捕获 ctx 以发射 SSE 事件）。"""

    def parse_node(state: WorkflowGraphState) -> Dict[str, Any]:
        return _safe_node(ctx, run_parse, "parse_node", dict(state))

    def retrieve_node(state: WorkflowGraphState) -> Dict[str, Any]:
        return _safe_node(ctx, run_retrieve, "retrieve_node", dict(state))

    def copywriting_node(state: WorkflowGraphState) -> Dict[str, Any]:
        return _safe_node(ctx, run_copywriting, "copywriting_node", dict(state))

    def organize_node(state: WorkflowGraphState) -> Dict[str, Any]:
        return _safe_node(ctx, run_organize, "organize_node", dict(state))

    def qa_node(state: WorkflowGraphState) -> Dict[str, Any]:
        return _safe_node(ctx, run_qa, "qa_node", dict(state))

    def audit_input_node(state: WorkflowGraphState) -> Dict[str, Any]:
        return {"draft_content": state.get("raw_input", "")}

    def rewrite_node(state: WorkflowGraphState) -> Dict[str, Any]:
        before = state.get("draft_content", "")
        before_audit = state.get("audit_result")
        result = _safe_node(ctx, run_rewrite, "rewrite_node", dict(state))
        after = str(result.get("draft_content", before))
        history = list(state.get("revision_history") or [])
        history.append(RevisionRecord(
            iteration=int(state.get("iteration_count") or 0),
            before_content=before[:6000],
            after_content=after[:6000],
            before_score=_audit_score(before_audit),
            issues=_issue_summaries(before_audit),
            unified_diff=_make_unified_diff(before, after),
        ).model_dump())
        result["revision_history"] = history
        return result

    def audit_node(state: WorkflowGraphState) -> Dict[str, Any]:
        result = _safe_node(ctx, run_audit, "audit_node", dict(state))
        history = list(state.get("revision_history") or [])
        if history and history[-1].get("after_score") is None:
            history[-1] = {**history[-1], "after_score": _audit_score(result.get("audit_result"))}
            result["revision_history"] = history
        return result

    builder = StateGraph(WorkflowGraphState)
    builder.add_node("parse_node", parse_node)
    builder.add_node("retrieve_node", retrieve_node)
    builder.add_node("copywriting_node", copywriting_node)
    builder.add_node("organize_node", organize_node)
    builder.add_node("qa_node", qa_node)
    builder.add_node("audit_input_node", audit_input_node)
    builder.add_node("rewrite_node", rewrite_node)
    builder.add_node("audit_node", audit_node)

    builder.add_edge(START, "parse_node")
    builder.add_conditional_edges("parse_node", _route_after_parse)
    builder.add_conditional_edges("retrieve_node", _route_after_retrieve)
    builder.add_edge("copywriting_node", "audit_node")
    builder.add_edge("organize_node", "audit_node")
    builder.add_edge("qa_node", "audit_node")
    builder.add_edge("audit_input_node", "audit_node")
    builder.add_edge("rewrite_node", "audit_node")
    builder.add_conditional_edges("audit_node", _route_after_audit)

    return builder.compile()


def _run_langgraph(ctx: Dict[str, Any], state: WorkflowGraphState) -> WorkflowGraphState:
    """执行 LangGraph 工作流。"""
    logger.info("[Graph] 使用 LangGraph StateGraph 调度")
    graph = _build_workflow_graph(ctx)
    return graph.invoke(state)


# ============= 对外主入口 =============


def run_workflow(raw_input: str, user_id: str = "guest",
                 session_id: Optional[str] = None) -> FinalResponse:
    """阻塞式：运行完整工作流，返回 FinalResponse。"""
    return run_workflow_with_events(raw_input, user_id, None, session_id)


def run_workflow_with_events(
    raw_input: str,
    user_id: str = "guest",
    event_callback: Optional[Callable[[dict], None]] = None,
    session_id: Optional[str] = None,
) -> FinalResponse:
    """流式：运行工作流，通过 event_callback 逐事件通知调用方（前端 SSE）。"""
    t0 = time.time()
    telemetry = start_run_telemetry()
    ctx: Dict[str, Any] = {"event_callback": event_callback}

    sm = get_session_manager()
    session = sm.before_workflow(session_id, user_id, raw_input)
    context_text = session.context_summary()
    profile_text = session.profile.summary_text()
    has_context = bool(context_text)
    logger.info(
        f"[Graph] 会话 {session.session_id} 启动：user_id={user_id}, "
        f"历史消息数={len(session.messages)}, 画像_preference={session.profile.preferred_style}"
    )

    _emit(ctx, {
        "type": "workflow_start",
        "ts": t0,
        "input": raw_input[:200],
        "session_id": session.session_id,
        "has_context": has_context,
    })

    state: WorkflowGraphState = {
        "raw_input": raw_input,
        "user_id": user_id,
        "session_id": session.session_id,
        "session_context": context_text,
        "user_profile_text": profile_text,
        "parsed_task": None,
        "retrieved_materials": [],
        "draft_content": "",
        "audit_result": None,
        "iteration_count": 0,
        "max_iteration": int(settings.audit_max_iteration or 3),
        "degrade_mode": False,
        "error_info": "",
        "need_more_retrieval": False,
        "node_failed": "",
        "revision_history": [],
    }

    try:
        state = _run_langgraph(ctx, state)
    except Exception as e:
        logger.exception(f"工作流异常：{e}")
        state["error_info"] = str(e)
        state["degrade_mode"] = True
        _emit(ctx, {"type": "workflow_error", "error": str(e)})

    dt_ms = (time.time() - t0) * 1000
    metrics_dict = telemetry.snapshot(dt_ms)
    _emit(ctx, {
        "type": "workflow_done",
        "ts": time.time(),
        "elapsed_ms": round(dt_ms, 1),
        "session_id": session.session_id,
        "metrics": metrics_dict,
    })

    has_error = bool(state.get("error_info"))
    parsed_task_obj: Optional[ParsedTask] = None
    pt = state.get("parsed_task")
    if isinstance(pt, ParsedTask):
        parsed_task_obj = pt
    content = state.get("draft_content") or ""
    audit_result = state.get("audit_result")

    task_type_value = (
        getattr(parsed_task_obj, "task_type", None)
        if parsed_task_obj is not None
        else None
    )
    if task_type_value == "audit":
        workflow_success = bool(audit_result is not None) and not has_error
    else:
        audit_passed = bool(
            getattr(audit_result, "passed", False)
            if audit_result is not None and not isinstance(audit_result, dict)
            else (audit_result or {}).get("passed", False)
        )
        workflow_success = bool(content) and audit_passed and not has_error

    final_error = state.get("error_info") or None
    if not workflow_success and not final_error and task_type_value != "audit":
        final_error = "生成内容在最大重写次数内未通过审核"

    try:
        reflection_entry: Optional[ReflectionEntry] = None
        iteration = int(state.get("iteration_count") or 0)
        revision_records = [
            RevisionRecord.model_validate(item)
            for item in (state.get("revision_history") or [])
        ]
        if revision_records:
            first_revision = revision_records[0]
            last_revision = revision_records[-1]
            reflection_entry = ReflectionEntry(
                session_id=session.session_id,
                original_content=first_revision.before_content[:300],
                revision_content=last_revision.after_content[:300],
                audit_score_before=first_revision.before_score,
                audit_score_after=last_revision.after_score or _audit_score(audit_result),
                issues_found=first_revision.issues,
                iteration=iteration,
            )

        sm.after_workflow(
            session=session,
            content=content,
            audit_result=audit_result,
            parsed_task=parsed_task_obj,
            reflection_entry=reflection_entry,
        )
        logger.info(f"[Memory] 会话已持久化：reflection={reflection_entry is not None}")
    except Exception as e:
        logger.warning(f"[Memory] 会话持久化失败：{e}")

    return FinalResponse(
        success=workflow_success,
        task_type=task_type_value,
        content=content,
        audit_result=audit_result,
        iteration_count=int(state.get("iteration_count") or 0),
        degrade_mode=bool(state.get("degrade_mode")),
        error=final_error,
        elapsed_ms=dt_ms,
        session_id=session.session_id,
        has_context=has_context,
        user_profile_summary=profile_text if profile_text else None,
        metrics=WorkflowMetrics.model_validate(metrics_dict),
        revisions=[
            RevisionRecord.model_validate(item)
            for item in (state.get("revision_history") or [])
        ],
    )


# ============= 辅助：列出已注册工具 =============


def list_tools() -> List[str]:
    from .tools import registry
    return registry.list_tools()
