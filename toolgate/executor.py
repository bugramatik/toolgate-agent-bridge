"""Run one tool call: scope check, HTTP request, retries, timeout, result check."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
from pydantic import BaseModel, Field

from toolgate.config import Settings
from toolgate.manifest import ToolSpec
from toolgate.registry import ToolRegistry


class ToolResult(BaseModel):
    """Outcome of a single tool invocation."""

    ok: bool
    status: int
    body: Any = None
    latency_ms: int = 0
    error: str | None = None


class ScopeDenied(Exception):
    """Raised when the caller does not hold a scope the tool requires."""

    def __init__(self, tool: str, missing: list[str]) -> None:
        self.tool = tool
        self.missing = missing
        super().__init__(f"missing scopes for {tool}: {', '.join(missing)}")


class UnknownTool(Exception):
    """Raised when a call names a tool the registry does not hold."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"unknown tool: {name}")


class ToolExecutor:
    """Single call path shared by every tool in the registry."""

    def __init__(
        self,
        registry: ToolRegistry,
        client: httpx.AsyncClient,
        settings: Settings,
    ) -> None:
        self.registry = registry
        self.client = client
        self.settings = settings

    def _check_scopes(self, spec: ToolSpec, scopes: set[str]) -> None:
        missing = [scope for scope in spec.scopes if scope not in scopes]
        if missing:
            raise ScopeDenied(spec.name, missing)

    def _render(self, spec: ToolSpec, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Fill path placeholders and split arguments into query and body."""
        url = spec.url
        remaining = dict(arguments)
        for param in spec.params:
            if param.location == "path":
                if param.name not in remaining:
                    raise ValueError(f"missing path parameter {param.name}")
                value = remaining.pop(param.name)
                url = url.replace("{" + param.name + "}", str(value))
        query = {
            param.name: remaining.pop(param.name)
            for param in spec.params
            if param.location == "query" and param.name in remaining
        }
        body = {
            param.name: remaining.pop(param.name)
            for param in spec.params
            if param.location == "body" and param.name in remaining
        }
        if remaining:
            raise ValueError(f"unexpected arguments: {', '.join(sorted(remaining))}")
        return url, {"query": query, "body": body}

    async def _send(
        self,
        spec: ToolSpec,
        url: str,
        parts: dict[str, Any],
        timeout_s: float,
    ) -> httpx.Response:
        request_kwargs: dict[str, Any] = {"params": parts["query"]}
        if parts["body"]:
            request_kwargs["json"] = parts["body"]
        return await self.client.request(
            spec.method,
            url,
            timeout=timeout_s,
            **request_kwargs,
        )

    async def call(
        self,
        name: str,
        arguments: dict[str, Any],
        scopes: set[str],
    ) -> ToolResult:
        """Invoke a tool and return a result; never raises for a failed call."""
        spec = self.registry.get(name)
        if spec is None:
            raise UnknownTool(name)
        self._check_scopes(spec, scopes)

        try:
            url, parts = self._render(spec, arguments)
        except ValueError as exc:
            return ToolResult(ok=False, status=400, error=str(exc))

        timeout_s = min(spec.timeout_s, self.settings.call_timeout_s)
        attempts = max(1, min(spec.retries, self.settings.max_retries) + 1)
        started = time.perf_counter()
        last_error: str | None = None

        for attempt in range(attempts):
            try:
                response = await self._send(spec, url, parts, timeout_s)
            except httpx.TimeoutException:
                last_error = f"timeout after {timeout_s:.1f}s"
                break
            except httpx.HTTPError as exc:
                last_error = f"transport error: {exc.__class__.__name__}"
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.1 * (2**attempt))
                    continue
                break

            latency_ms = int((time.perf_counter() - started) * 1000)
            if response.status_code >= 500 and attempt + 1 < attempts:
                last_error = f"upstream {response.status_code}"
                await asyncio.sleep(0.1 * (2**attempt))
                continue
            if response.status_code >= 400:
                return ToolResult(
                    ok=False,
                    status=response.status_code,
                    body=_safe_json(response),
                    latency_ms=latency_ms,
                    error=f"upstream {response.status_code}",
                )
            return ToolResult(
                ok=True,
                status=response.status_code,
                body=_safe_json(response),
                latency_ms=latency_ms,
            )

        latency_ms = int((time.perf_counter() - started) * 1000)
        return ToolResult(ok=False, status=504, latency_ms=latency_ms, error=last_error)


def _safe_json(response: httpx.Response) -> Any:
    """Return the parsed body, or the raw text when it is not JSON."""
    try:
        return response.json()
    except ValueError:
        return response.text[:2000]
