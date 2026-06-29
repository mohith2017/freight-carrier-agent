from __future__ import annotations

import json
import logging
import time
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from threading import Lock
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic_ai import Agent
from pydantic_ai.messages import ToolCallPart, ToolReturnPart
from sse_starlette.sse import EventSourceResponse

from freight_agent import tools
from freight_agent.api.deps import Resources, get_resources, make_agent_deps
from freight_agent.api.schemas import QueryRequest, QueryResponse, ToolCallTrace
from freight_agent.config import get_settings

logger = logging.getLogger("freight_agent.api")

app = FastAPI(
    title="Freight Carrier Agent API",
    version="0.1.0",
    summary="Query a freight intake agent and look up loads, carriers, and rates.",
)

_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origin_list or ["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


_rl_lock = Lock()
_rl_hits: dict[str, deque[float]] = defaultdict(deque)
_RL_WINDOW = 60.0


def _rate_limited(client: str, limit: int) -> bool:
    if limit <= 0:
        return False
    now = time.monotonic()
    with _rl_lock:
        hits = _rl_hits[client]
        while hits and now - hits[0] > _RL_WINDOW:
            hits.popleft()
        if len(hits) >= limit:
            return True
        hits.append(now)
        return False


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    client = request.client.host if request.client else "unknown"

    if request.url.path not in ("/health", "/docs", "/openapi.json"):
        limit = get_settings().api_rate_limit_per_min
        if _rate_limited(client, limit):
            logger.warning(
                "rate_limit id=%s client=%s path=%s",
                request_id,
                client,
                request.url.path,
            )
            return JSONResponse(
                {"detail": "rate limit exceeded"},
                status_code=429,
                headers={"x-request-id": request_id, "retry-after": "60"},
            )

    start = time.monotonic()
    response = await call_next(request)
    elapsed_ms = (time.monotonic() - start) * 1000
    response.headers["x-request-id"] = request_id
    logger.info(
        "request id=%s client=%s %s %s -> %s (%.0fms)",
        request_id,
        client,
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response


def _summarize(content: Any) -> str:
    try:
        text = content if isinstance(content, str) else json.dumps(content, default=str)
    except (TypeError, ValueError):
        text = str(content)
    return text[:1500]


_OUTPUT_TOOL = "final_result"


def extract_tool_calls(messages: list[Any]) -> list[ToolCallTrace]:
    calls: dict[str, ToolCallTrace] = {}
    order: list[str] = []
    for msg in messages:
        for part in getattr(msg, "parts", []):
            if isinstance(part, ToolCallPart):
                if part.tool_name == _OUTPUT_TOOL:
                    continue
                args = part.args
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"_raw": args}
                calls[part.tool_call_id] = ToolCallTrace(
                    tool=part.tool_name, args=args or {}
                )
                order.append(part.tool_call_id)
            elif isinstance(part, ToolReturnPart) and part.tool_call_id in calls:
                calls[part.tool_call_id].result_summary = _summarize(part.content)
    return [calls[tid] for tid in order]


@app.get("/health")
def health() -> dict[str, Any]:
    settings = get_settings()
    return {
        "status": "ok",
        "llm_configured": bool(settings.openai_api_key),
        "store": "postgres" if settings.uses_postgres else "sqlite",
        "agent_model": settings.agent_model,
    }


@app.post("/query/sync", response_model=QueryResponse)
async def query_sync(
    req: QueryRequest, res: Resources = Depends(get_resources)
) -> QueryResponse:
    _require_llm(res)
    deps = make_agent_deps(res)
    result = await res.agent.run(req.question, deps=deps, model=req.model or None)
    trace = extract_tool_calls(result.all_messages())
    return QueryResponse(**result.output.model_dump(), tool_calls=trace)


@app.post("/query")
async def query_stream(
    req: QueryRequest, res: Resources = Depends(get_resources)
) -> EventSourceResponse:
    _require_llm(res)
    deps = make_agent_deps(res)

    async def events() -> AsyncIterator[dict[str, str]]:
        yield {"event": "status", "data": json.dumps({"state": "thinking"})}
        try:
            async with res.agent.iter(
                req.question, deps=deps, model=req.model or None
            ) as run:
                async for node in run:
                    if Agent.is_call_tools_node(node):
                        for part in node.model_response.parts:
                            if (
                                isinstance(part, ToolCallPart)
                                and part.tool_name != _OUTPUT_TOOL
                            ):
                                yield {
                                    "event": "tool",
                                    "data": json.dumps(
                                        {"tool": part.tool_name, "args": part.args}
                                        if isinstance(part.args, dict)
                                        else {"tool": part.tool_name}
                                    ),
                                }
            result = run.result
            assert result is not None
            trace = extract_tool_calls(result.all_messages())
            payload = QueryResponse(
                **result.output.model_dump(), tool_calls=trace
            )
            yield {"event": "result", "data": payload.model_dump_json()}
        except Exception as exc:
            logger.exception("query stream failed")
            yield {"event": "error", "data": json.dumps({"detail": str(exc)})}
        finally:
            yield {"event": "done", "data": "{}"}

    return EventSourceResponse(events())


@app.get("/loads/{load_id}")
def get_load(load_id: str, res: Resources = Depends(get_resources)) -> dict[str, Any]:
    with res.session_factory() as s:
        info = tools.get_load(s, load_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"load {load_id} not found")
    return info.model_dump(mode="json")


@app.get("/carriers/resolve")
def resolve_carrier(q: str, res: Resources = Depends(get_resources)) -> dict[str, Any]:
    with res.session_factory() as s:
        info = tools.resolve_carrier(s, q)
    if info is None:
        raise HTTPException(status_code=404, detail=f"no carrier matched '{q}'")
    return info.model_dump(mode="json")


@app.get("/carriers/{carrier_id}/history")
def carrier_history(
    carrier_id: int, res: Resources = Depends(get_resources)
) -> dict[str, Any]:
    with res.session_factory() as s:
        info = tools.get_carrier_history(s, carrier_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"carrier {carrier_id} not found")
    return info.model_dump(mode="json")


@app.get("/rates/context")
def rate_context(
    origin: str,
    destination: str,
    equipment: str | None = None,
    flat_usd: float | None = None,
    distance_miles: int | None = None,
    res: Resources = Depends(get_resources),
) -> dict[str, Any]:
    with res.session_factory() as s:
        ctx = tools.get_rate_context(
            s,
            origin,
            destination,
            equipment,
            flat_usd=flat_usd,
            distance_miles=distance_miles,
        )
    return ctx.model_dump(mode="json")


def _require_llm(res: Resources) -> None:
    if not res.settings.openai_api_key:
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY is not configured on the server.",
        )
