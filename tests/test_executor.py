import asyncio

import httpx
import pytest

from toolgate.config import Settings
from toolgate.executor import ScopeDenied, ToolExecutor
from toolgate.manifest import ToolSpec
from toolgate.registry import ToolRegistry


def make_spec(**overrides) -> ToolSpec:
    data = {
        "name": "incidents.list_open",
        "description": "List open incidents.",
        "method": "GET",
        "url": "https://incidents.example.com/v1/incidents",
        "scopes": ["incidents:read"],
        "timeout_s": 2,
        "retries": 2,
        "params": {
            "type": "object",
            "properties": {"severity": {"type": "string"}, "limit": {"type": "integer"}},
            "required": [],
        },
    }
    data.update(overrides)
    return ToolSpec.model_validate(data)


def make_executor(handler, spec: ToolSpec | None = None) -> ToolExecutor:
    spec = spec or make_spec()
    registry = ToolRegistry.from_specs([spec])
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = Settings(call_timeout_s=2.0, max_retries=2)
    return ToolExecutor(registry, client, settings)


async def test_call_success():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"incidents": [{"id": "INC-4821"}]})

    executor = make_executor(handler)
    result = await executor.call("incidents.list_open", {"limit": 5}, {"incidents:read"})

    assert result.ok is True
    assert result.status == 200
    assert result.body == {"incidents": [{"id": "INC-4821"}]}
    assert "limit=5" in seen["url"]
    assert result.latency_ms >= 0


async def test_scope_denied():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - never called
        raise AssertionError("upstream must not be reached")

    executor = make_executor(handler)
    with pytest.raises(ScopeDenied) as excinfo:
        await executor.call("incidents.list_open", {}, set())

    assert excinfo.value.missing == ["incidents:read"]


async def test_retries_on_503():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json={"incidents": []})

    executor = make_executor(handler)
    result = await executor.call("incidents.list_open", {}, {"incidents:read"})

    assert calls["n"] == 3
    assert result.ok is True
    assert result.body == {"incidents": []}


async def test_timeout_returns_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    executor = make_executor(handler)
    result = await executor.call("incidents.list_open", {}, {"incidents:read"})

    assert result.ok is False
    assert result.status == 504
    assert result.error is not None and "timeout" in result.error


async def test_unexpected_argument_is_rejected():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - never called
        raise AssertionError("upstream must not be reached")

    executor = make_executor(handler)
    result = await executor.call("incidents.list_open", {"colour": "red"}, {"incidents:read"})

    assert result.ok is False
    assert result.status == 400
    assert "colour" in (result.error or "")


async def test_retry_sleep_is_short():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    executor = make_executor(handler)
    started = asyncio.get_running_loop().time()
    result = await executor.call("incidents.list_open", {}, {"incidents:read"})
    elapsed = asyncio.get_running_loop().time() - started

    assert result.ok is False
    assert result.status == 504
    assert elapsed < 1.0
