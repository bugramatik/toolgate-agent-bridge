"""FastAPI app: tool listing, invocation, audit feed, metrics and health."""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, Field

from toolgate import __version__
from toolgate.audit import AuditWriter
from toolgate.config import Settings, get_settings
from toolgate.executor import ScopeDenied, ToolExecutor, ToolResult
from toolgate.manifest import ManifestError, load_manifests
from toolgate.registry import ToolRegistry, UnknownToolError

log = logging.getLogger("toolgate")

CALLS = Counter("toolgate_calls_total", "Tool calls", ["tool", "outcome"])
LATENCY = Histogram(
    "toolgate_call_latency_seconds",
    "Tool call latency",
    ["tool"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)


class CallRequest(BaseModel):
    """Body of a tool invocation."""

    arguments: dict[str, Any] = Field(default_factory=dict)
    scopes: list[str] = Field(default_factory=list)
    call_id: str | None = None


class CallResponse(BaseModel):
    """What the caller gets back for one invocation."""

    call_id: str
    tool: str
    ok: bool
    status: int
    body: Any = None
    latency_ms: int = 0
    error: str | None = None


def _build_registry(settings: Settings) -> ToolRegistry:
    specs = load_manifests(Path(settings.tool_dir))
    return ToolRegistry.from_specs(specs)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    try:
        registry = _build_registry(settings)
    except ManifestError as exc:
        log.error("manifest error: %s", exc)
        raise
    writer = AuditWriter.from_url(settings.database_url)
    try:
        writer.create_schema()
    except Exception as exc:  # the bridge still serves tools when the database is down
        log.warning("audit table not ready: %s", exc)
    client = httpx.AsyncClient(follow_redirects=True)
    app.state.settings = settings
    app.state.registry = registry
    app.state.writer = writer
    app.state.executor = ToolExecutor(registry, client, settings)
    log.info("toolgate %s serving %d tools", __version__, len(registry))
    try:
        yield
    finally:
        await client.aclose()


app = FastAPI(title="Toolgate Agent Bridge", version=__version__, lifespan=lifespan)


def require_token(x_api_token: str = Header(default="")) -> None:
    """Reject a request that does not carry the shared token."""
    settings = get_settings()
    if x_api_token != settings.api_token:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Token")


def _registry(request: Request) -> ToolRegistry:
    return request.app.state.registry


@app.get("/healthz", response_class=PlainTextResponse)
def healthz(request: Request) -> str:
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="registry not loaded")
    return "ok"


@app.get("/tools", dependencies=[Depends(require_token)])
def list_tools(request: Request) -> dict[str, Any]:
    registry = _registry(request)
    return {
        "count": len(registry),
        "tools": [
            {
                "name": spec.name,
                "description": spec.description,
                "method": spec.method,
                "url": spec.url,
                "scopes": spec.scopes,
                "timeout_s": spec.timeout_s,
                "retries": spec.retries,
            }
            for spec in registry.all()
        ],
    }


@app.get("/tools/schema", dependencies=[Depends(require_token)])
def tool_schema(request: Request) -> list[dict[str, Any]]:
    """The registry as the `tools` array an OpenAI chat call expects."""
    return _registry(request).openai_tools()


@app.post("/tools/{name}/call", dependencies=[Depends(require_token)])
async def call_tool(name: str, payload: CallRequest, request: Request) -> JSONResponse:
    executor: ToolExecutor = request.app.state.executor
    writer: AuditWriter = request.app.state.writer
    call_id = payload.call_id or str(uuid.uuid4())
    scopes = set(payload.scopes)

    try:
        result = await executor.call(name, payload.arguments, scopes)
    except UnknownToolError:
        CALLS.labels(tool=name, outcome="unknown").inc()
        raise HTTPException(status_code=404, detail=f"unknown tool: {name}") from None
    except ScopeDenied as exc:
        CALLS.labels(tool=name, outcome="denied").inc()
        result = ToolResult(ok=False, status=403, error=str(exc))

    outcome = "ok" if result.ok else "error"
    CALLS.labels(tool=name, outcome=outcome).inc()
    LATENCY.labels(tool=name).observe(result.latency_ms / 1000.0)

    try:
        await writer.record(call_id, name, payload.arguments, result, scopes)
    except Exception as exc:
        log.warning("audit write failed for %s: %s", call_id, exc)

    response = CallResponse(
        call_id=call_id,
        tool=name,
        ok=result.ok,
        status=result.status,
        body=result.body,
        latency_ms=result.latency_ms,
        error=result.error,
    )
    return JSONResponse(status_code=200, content=response.model_dump())


@app.get("/audit", dependencies=[Depends(require_token)])
async def audit_feed(request: Request, limit: int = Query(default=50, ge=1, le=500)) -> dict[str, Any]:
    writer: AuditWriter = request.app.state.writer
    try:
        rows = await writer.recent(limit)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"audit store unavailable: {exc}") from exc
    return {"count": len(rows), "records": rows}


@app.get("/metrics")
def metrics() -> PlainTextResponse:
    return PlainTextResponse(generate_latest().decode("utf-8"), media_type=CONTENT_TYPE_LATEST)
