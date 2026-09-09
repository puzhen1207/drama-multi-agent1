"""FastAPI 服务封装：
- GET  /                      → 前端页面（frontend/index.html）
- GET  /health                → 健康检查
- GET  /v1/tools              → 已注册的 MCP 工具列表
- POST /v1/generate           → 同步生成（阻塞）
- POST /v1/stream             → SSE 流式生成（前端可视化用）
- POST /v1/async/generate     → 异步提交（返回 task_id）
- GET  /v1/async/{task_id}    → 查询异步任务状态/结果

记忆模块：
- GET  /v1/sessions            → 列出用户所有会话
- GET  /v1/sessions/{session_id} → 获取会话详情
- DELETE /v1/sessions/{session_id} → 删除会话
- POST /v1/sessions/{session_id}/writeback → 把高分内容回写知识库

个人记忆库：
- POST /v1/memory/save           → 保存 Q&A 到个人记忆库
- GET  /v1/memory                → 列出个人记忆
- GET  /v1/memory/{memory_id}    → 获取单条记忆（完整内容）
- PUT  /v1/memory/{memory_id}     → 修改记忆
- DELETE /v1/memory/{memory_id}  → 删除一条记忆

SSE 关键改进（相比 drama-multi-agent 原版）：
- 每个事件严格使用 "event: xxx\ndata: {...}\n\n" 格式；
- data 部分通过 json.dumps(..., ensure_ascii=False) 编码；
- 通过 StreamingResponse 以 UTF-8 流式输出，避免中文被 unicode-escape；
- 返回头显式设置 Cache-Control / X-Accel-Buffering: no，避免 Nginx 层缓存。
"""
from __future__ import annotations

import json
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from .config import PROJECT_ROOT, settings
from .graph import list_tools, run_workflow, run_workflow_with_events
from .identifiers import SAFE_IDENTIFIER_PATTERN, validate_identifier
from .logging_setup import setup_logging
from .memory import get_session_manager
from .models import FinalResponse
from .tools.embedding import embedding_status
from .tools.user_memory import get_user_memory_store
from .tools.vector_retriever import ensure_builtin_knowledge, get_vector_store

setup_logging()

import logging  # noqa: E402
logger = logging.getLogger("drama_agent.api")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    try:
        ensure_builtin_knowledge()
    except Exception as e:
        logger.warning(f"[API] 启动时构建知识库失败：{e}")
    app._start_time = time.time()  # type: ignore[attr-defined]
    yield


app = FastAPI(
    title="短剧多智能体内容生产系统",
    version="2.1.0",
    description="基于 LangGraph 的多 Agent 系统：解析 → 检索 → 润色 → 合规审核（支持会话记忆、用户画像、反思日志）",
    lifespan=_lifespan,
)


# ============= 请求 / 响应模型 =============


class GenerateRequest(BaseModel):
    raw_input: str = Field(..., min_length=1, max_length=10_000,
                           description="用户原始输入（最多 10k 字）")
    user_id: str = Field("guest", pattern=SAFE_IDENTIFIER_PATTERN, description="用户标识（可选）")
    session_id: Optional[str] = Field(default=None, pattern=SAFE_IDENTIFIER_PATTERN, description="会话 ID（不传则自动新建）")


class GenerateResponse(BaseModel):
    task_id: Optional[str] = None
    status: str = "ok"
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class WritebackRequest(BaseModel):
    user_id: str = Field("guest", pattern=SAFE_IDENTIFIER_PATTERN)
    title: str = Field(..., description="素材标题（建议使用剧名/场景名）")
    category: str = Field("剧本", description="素材分类：剧本/文案/人设/规则")
    content: Optional[str] = Field(default=None, description="正文（不传则从会话中读取 draft_content）")
    score_threshold: float = Field(0.85, ge=0.0, le=1.0, description="只有审核分 >= 阈值才允许回写")


class SaveMemoryRequest(BaseModel):
    user_id: str = Field("guest", pattern=SAFE_IDENTIFIER_PATTERN)
    question: str = Field(..., min_length=2, max_length=10_000)
    answer: str = Field(..., min_length=20, max_length=50_000)
    title: Optional[str] = Field(default=None, max_length=200)
    session_id: Optional[str] = Field(default=None, pattern=SAFE_IDENTIFIER_PATTERN)


class UpdateMemoryRequest(BaseModel):
    user_id: str = Field("guest", pattern=SAFE_IDENTIFIER_PATTERN)
    question: Optional[str] = Field(default=None, min_length=2, max_length=10_000)
    answer: Optional[str] = Field(default=None, min_length=20, max_length=50_000)
    title: Optional[str] = Field(default=None, max_length=200)


class ImportMemoryRequest(BaseModel):
    user_id: str = Field("guest", pattern=SAFE_IDENTIFIER_PATTERN)
    memories: List[Dict[str, Any]] = Field(default_factory=list)
    skip_duplicates: bool = True


def _configured_user_tokens() -> Dict[str, str]:
    """读取 user_id -> bearer token 映射；未配置时保持本地单用户模式。"""
    raw = settings.api_user_tokens_json.get_secret_value().strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception as e:
        raise HTTPException(status_code=500, detail="API_USER_TOKENS_JSON 配置不是合法 JSON") from e
    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail="API_USER_TOKENS_JSON 必须是 JSON 对象")
    tokens: Dict[str, str] = {}
    for uid, token in data.items():
        try:
            safe_uid = validate_identifier(str(uid), "user_id")
        except ValueError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
        if not isinstance(token, str) or len(token) < 16:
            raise HTTPException(status_code=500, detail=f"用户 {safe_uid} 的 API Token 至少需要 16 个字符")
        tokens[safe_uid] = token
    return tokens


def _authorize_user(request: Request, claimed_user_id: Optional[str]) -> str:
    """在配置 Token 时把请求身份绑定到唯一 user_id。"""
    tokens = _configured_user_tokens()
    if not tokens:
        try:
            return validate_identifier(claimed_user_id or "guest", "user_id")
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e

    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="缺少 Bearer Token")
    supplied = auth[7:].strip()
    authenticated_user: Optional[str] = None
    for uid, expected in tokens.items():
        if secrets.compare_digest(supplied, expected):
            authenticated_user = uid
            break
    if authenticated_user is None:
        raise HTTPException(status_code=401, detail="Bearer Token 无效")
    if claimed_user_id and claimed_user_id != authenticated_user:
        raise HTTPException(status_code=403, detail="user_id 与认证身份不一致")
    return authenticated_user


def _session_or_http_error(session_id: str, user_id: str):
    try:
        session = get_session_manager().get_existing(session_id, user_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} not found")
    return session


# ============= 路由：基础 =============


@app.get("/", response_class=HTMLResponse)
def root():
    index_html = PROJECT_ROOT / "frontend" / "index.html"
    if not index_html.exists():
        return HTMLResponse(
            "<h1>服务运行中（drama-multi-agent）</h1>"
            "<p>前端资源未找到；请把 frontend/index.html 放入项目根目录。</p>"
            "<p>API 文档：<a href='/docs'>/docs</a></p>",
            status_code=200,
        )
    return HTMLResponse(index_html.read_text(encoding="utf-8"))


@app.get("/health")
def health() -> Dict[str, Any]:
    user_memory_count = 0
    try:
        user_memory_count = get_user_memory_store().count()
    except Exception:
        pass
    return {
        "status": "ok",
        "version": "2.1.0",
        "uptime_seconds": int(time.time() - getattr(app, "_start_time", time.time())),
        "memory_module": True,
        "user_memory_enabled": settings.enable_user_memory,
        "user_memory_count": user_memory_count,
        "embedding": embedding_status(),
    }


@app.get("/v1/tools")
def get_tools() -> Dict[str, List[str]]:
    return {"tools": list_tools()}


# ============= 路由：同步 =============


@app.post("/v1/generate")
def generate(req: GenerateRequest, request: Request) -> GenerateResponse:
    try:
        user_id = _authorize_user(request, req.user_id)
        resp: FinalResponse = run_workflow(req.raw_input, user_id, req.session_id)
        return GenerateResponse(
            status="ok" if resp.success else "rejected",
            data=resp.model_dump(),
            error=resp.error,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[API] /v1/generate 异常：{e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============= 路由：SSE 流式 =============


@app.post("/v1/stream")
async def stream_generate(req: GenerateRequest, request: Request):
    """Server-Sent Events 流式输出。

    事件类型：
    - start: 工作流开始（含输入、session_id）
    - node_start: 节点开始
    - node_done: 节点结束（含 duration_ms、summary）
    - node_error: 节点异常
    - workflow_done: 工作流结束
    - final: 最终输出（含 content、audit_result）
    - error: 全局异常
    """

    user_id = _authorize_user(request, req.user_id)
    loop_shim: Dict[str, Any] = {"queue": []}
    done_flag: Dict[str, Any] = {"value": False, "status": "failed"}

    def _sync_callback(event: dict):
        loop_shim["queue"].append(event)

    def _run_sync():
        try:
            resp = run_workflow_with_events(
                req.raw_input, user_id,
                event_callback=_sync_callback, session_id=req.session_id,
            )
            loop_shim["queue"].append({"type": "final", "data": resp.model_dump()})
            done_flag["status"] = "ok" if resp.success else "rejected"
        except Exception as e:
            logger.exception(f"[API] /v1/stream 工作流异常：{e}")
            loop_shim["queue"].append({"type": "error", "message": str(e)})
        finally:
            done_flag["value"] = True

    thread = threading.Thread(target=_run_sync, daemon=True)
    thread.start()

    async def _sse_generator():
        # 起始事件（可选：让前端立刻知道已连上）
        yield _format_sse("start", {
            "input": req.raw_input[:200],
            "session_id": req.session_id or "auto",
        })
        # 轮询队列：一边读一边 flush
        while True:
            if loop_shim["queue"]:
                ev = loop_shim["queue"].pop(0)
                # 允许节点直接写 dict 以外的类型
                if not isinstance(ev, dict):
                    continue
                yield _format_sse(ev.get("type", "message"),
                                  {k: v for k, v in ev.items() if k != "type"})
                continue
            if done_flag["value"]:
                break
            # 让出 CPU
            import asyncio
            await asyncio.sleep(0.05)
        # 结束标记
        yield _format_sse("workflow_complete", {"status": done_flag["status"]})

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Accel-Charset": "utf-8",
        },
    )


def _format_sse(typ: str, payload: Dict[str, Any]) -> bytes:
    """把一个事件编码成 SSE 字节流（UTF-8，不做 unicode-escape）。"""
    merged = {"type": typ}
    merged.update(payload)
    try:
        data = json.dumps(merged, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        # 兜底：如果 payload 里有不可 JSON 序列化的对象（例如 pydantic 实例）
        def _default(o: Any) -> Any:
            if hasattr(o, "model_dump"):
                return o.model_dump()
            if hasattr(o, "dict"):
                return o.dict()
            return str(o)
        data = json.dumps(merged, ensure_ascii=False, separators=(",", ":"), default=_default)
    line = f"event: {typ}\ndata: {data}\n\n"
    return line.encode("utf-8")


# ============= 路由：异步任务 =============


MAX_ASYNC_TASKS = 200
_async_tasks_lock = threading.Lock()
_async_tasks: Dict[str, Dict[str, Any]] = {}


@app.post("/v1/async/generate")
def async_generate(req: GenerateRequest, request: Request) -> GenerateResponse:
    user_id = _authorize_user(request, req.user_id)
    task_id = uuid.uuid4().hex
    with _async_tasks_lock:
        _async_tasks[task_id] = {
            "status": "pending",
            "created_at": time.time(),
            "session_id": req.session_id,
            "user_id": user_id,
        }
        # 淘汰老任务
        if len(_async_tasks) > MAX_ASYNC_TASKS:
            non_pending = sorted(
                [(k, v) for k, v in _async_tasks.items() if v.get("status") != "pending"],
                key=lambda kv: kv[1].get("created_at", 0),
            )
            to_remove = len(_async_tasks) - MAX_ASYNC_TASKS
            for k, _ in non_pending[:to_remove]:
                _async_tasks.pop(k, None)

    def _run():
        try:
            resp = run_workflow(req.raw_input, user_id, req.session_id)
            with _async_tasks_lock:
                _async_tasks[task_id] = {
                    "status": "ok",
                    "result": resp.model_dump(),
                    "created_at": time.time(),
                    "user_id": user_id,
                }
        except Exception as e:
            with _async_tasks_lock:
                _async_tasks[task_id] = {
                    "status": "failed",
                    "error": str(e),
                    "created_at": time.time(),
                    "user_id": user_id,
                }

    threading.Thread(target=_run, daemon=True).start()
    return GenerateResponse(task_id=task_id, status="pending")


@app.get("/v1/async/{task_id}")
def get_async_status(
    task_id: str,
    request: Request,
    user_id: Optional[str] = Query(default=None, pattern=SAFE_IDENTIFIER_PATTERN),
) -> GenerateResponse:
    effective_user_id = _authorize_user(request, user_id)
    with _async_tasks_lock:
        task = _async_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    if task.get("user_id") != effective_user_id:
        raise HTTPException(status_code=403, detail="任务不属于当前用户")
    return GenerateResponse(
        task_id=task_id,
        status=task["status"],
        data=task.get("result"),
        error=task.get("error"),
    )


# ============= 记忆模块：会话管理 =============


@app.get("/v1/sessions")
def list_sessions(
    request: Request,
    user_id: Optional[str] = Query(default=None, pattern=SAFE_IDENTIFIER_PATTERN),
) -> Dict[str, Any]:
    effective_user_id = _authorize_user(request, user_id)
    sm = get_session_manager()
    sessions = sm.list_sessions(user_id=effective_user_id)
    return {"total": len(sessions), "sessions": sessions}


@app.get("/v1/sessions/{session_id}")
def get_session(
    session_id: str,
    request: Request,
    user_id: Optional[str] = Query(default=None, pattern=SAFE_IDENTIFIER_PATTERN),
) -> Dict[str, Any]:
    effective_user_id = _authorize_user(request, user_id)
    session = _session_or_http_error(session_id, effective_user_id)
    return {
        "session_id": session.session_id,
        "user_id": session.user_id,
        "created_ts": session.created_ts,
        "updated_ts": session.updated_ts,
        "messages": [
            {"role": m.role, "content": m.content[:500], "ts": m.ts}
            for m in session.messages
        ],
        "profile": session.profile.model_dump() if hasattr(session.profile, "model_dump") else session.profile.__dict__,
        "reflections": [r.model_dump() for r in session.reflections] if hasattr(session, "reflections") else [],
    }


@app.delete("/v1/sessions/{session_id}")
def delete_session(
    session_id: str,
    request: Request,
    user_id: Optional[str] = Query(default=None, pattern=SAFE_IDENTIFIER_PATTERN),
) -> Dict[str, Any]:
    effective_user_id = _authorize_user(request, user_id)
    sm = get_session_manager()
    try:
        existed = sm.delete(session_id, effective_user_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    if not existed:
        raise HTTPException(status_code=404, detail=f"session {session_id} not found")
    return {"status": "ok", "deleted": session_id}


# ============= 记忆模块：高分内容回写 =============


@app.post("/v1/sessions/{session_id}/writeback")
def writeback_to_knowledge(
    session_id: str, req: WritebackRequest, request: Request
) -> Dict[str, Any]:
    user_id = _authorize_user(request, req.user_id)
    session = _session_or_http_error(session_id, user_id)

    last_assistant = session.last_assistant() or ""
    content_to_write: str = req.content or last_assistant
    if req.content and req.content.strip() != last_assistant.strip():
        raise HTTPException(status_code=400, detail="只能回写该会话最近一次已审核的输出")
    if not content_to_write or len(content_to_write.strip()) < 50:
        raise HTTPException(status_code=400,
                            detail="没有足够的内容用于回写（至少 50 字）")

    # 审核分数校验
    latest_audit = session.last_audit_result
    if latest_audit is None:
        raise HTTPException(status_code=400, detail="该会话没有可验证的审核结果")
    recent_audit_score = float(latest_audit.score)
    passed = bool(latest_audit.passed) and recent_audit_score >= req.score_threshold

    if not passed:
        raise HTTPException(
            status_code=400,
            detail=f"审核分 {recent_audit_score} 低于阈值 {req.score_threshold}，不允许回写",
        )

    try:
        vs = get_vector_store()
        vs.add_documents([{
            "title": req.title,
            "category": req.category,
            "content": content_to_write,
        }])
        vs.save()
        return {"status": "ok", "title": req.title, "category": req.category,
                "chars": len(content_to_write)}
    except Exception as e:
        logger.exception(f"[API] 回写知识库失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============= 个人记忆库 =============


@app.post("/v1/memory/save")
def save_user_memory(req: SaveMemoryRequest, request: Request) -> Dict[str, Any]:
    if not settings.enable_user_memory:
        raise HTTPException(status_code=400, detail="个人记忆库功能已关闭")
    user_id = _authorize_user(request, req.user_id)
    try:
        store = get_user_memory_store()
        memory_id = store.add(
            user_id=user_id,
            question=req.question,
            answer=req.answer,
            title=req.title,
            session_id=req.session_id,
        )
        return {
            "status": "ok",
            "memory_id": memory_id,
            "total": store.count(user_id),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"[API] 保存个人记忆失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/v1/memory")
def list_user_memory(
    request: Request,
    user_id: Optional[str] = Query(default=None, pattern=SAFE_IDENTIFIER_PATTERN),
    limit: int = Query(default=50, ge=1, le=200),
    full: bool = Query(default=True, description="是否返回完整问答内容"),
) -> Dict[str, Any]:
    effective_user_id = _authorize_user(request, user_id)
    store = get_user_memory_store()
    memories = store.list_memories(effective_user_id, limit=limit, full=full)
    return {
        "total": store.count(effective_user_id),
        "memories": memories,
    }


@app.get("/v1/memory/export")
def export_user_memories(
    request: Request,
    user_id: Optional[str] = Query(default=None, pattern=SAFE_IDENTIFIER_PATTERN),
) -> Dict[str, Any]:
    effective_user_id = _authorize_user(request, user_id)
    store = get_user_memory_store()
    return {
        "status": "ok",
        "user_id": effective_user_id,
        "exported_at": time.time(),
        "total": store.count(effective_user_id),
        "memories": store.export_all(effective_user_id),
    }


@app.post("/v1/memory/import")
def import_user_memories(req: ImportMemoryRequest, request: Request) -> Dict[str, Any]:
    if not settings.enable_user_memory:
        raise HTTPException(status_code=400, detail="个人记忆库功能已关闭")
    user_id = _authorize_user(request, req.user_id)
    try:
        store = get_user_memory_store()
        result = store.import_entries(
            user_id, req.memories, skip_duplicates=req.skip_duplicates,
        )
        return {"status": "ok", "user_id": user_id, **result}
    except Exception as e:
        logger.exception(f"[API] 导入个人记忆失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/v1/memory/{memory_id}")
def get_user_memory(
    memory_id: str,
    request: Request,
    user_id: Optional[str] = Query(default=None, pattern=SAFE_IDENTIFIER_PATTERN),
) -> Dict[str, Any]:
    effective_user_id = _authorize_user(request, user_id)
    store = get_user_memory_store()
    item = store.get(memory_id, effective_user_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"memory {memory_id} not found")
    return {"status": "ok", "memory": item}


@app.put("/v1/memory/{memory_id}")
def update_user_memory(
    memory_id: str, req: UpdateMemoryRequest, request: Request
) -> Dict[str, Any]:
    if not settings.enable_user_memory:
        raise HTTPException(status_code=400, detail="个人记忆库功能已关闭")
    if req.question is None and req.answer is None and req.title is None:
        raise HTTPException(status_code=400, detail="请至少提供 question、answer 或 title 之一")
    user_id = _authorize_user(request, req.user_id)
    try:
        store = get_user_memory_store()
        ok = store.update(
            memory_id,
            user_id,
            question=req.question,
            answer=req.answer,
            title=req.title,
        )
        if not ok:
            raise HTTPException(status_code=404, detail=f"memory {memory_id} not found")
        item = store.get(memory_id, user_id)
        return {"status": "ok", "memory": item}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[API] 更新个人记忆失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/v1/memory/{memory_id}")
def delete_user_memory(
    memory_id: str,
    request: Request,
    user_id: Optional[str] = Query(default=None, pattern=SAFE_IDENTIFIER_PATTERN),
) -> Dict[str, Any]:
    effective_user_id = _authorize_user(request, user_id)
    store = get_user_memory_store()
    if not store.delete(memory_id, effective_user_id):
        raise HTTPException(status_code=404, detail=f"memory {memory_id} not found")
    return {"status": "ok", "deleted": memory_id, "total": store.count(effective_user_id)}
