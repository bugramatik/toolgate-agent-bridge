# Toolgate Agent Bridge

Turns internal REST APIs into safe, audited tools an LLM agent can call.

<img src="screenshots/1.png" alt="Toolgate dashboard showing registered tools, call latency and the audit feed" width="800">

## Why

Wiring an assistant to an internal service usually means writing the same glue again for every endpoint: a JSON schema, a timeout, a retry, a permission check and a log line that nobody can query later. The tool definitions drift from the APIs they wrap, and when a call fails there is no record of what the model asked for. Toolgate keeps the declaration in one manifest per domain and sends every call through a single path that checks scope, enforces a timeout and writes an audit row.

## How it works

At startup the bridge reads every YAML file in the tool directory, validates each entry against the tool spec and builds an in-memory registry. The registry renders the same tools as an OpenAI-compatible `tools` array, so an agent can be handed the definitions directly. A call arrives at the HTTP API with a shared token, the executor checks the caller's scopes, fills path placeholders, splits arguments into query and body, retries on 5xx and connection errors, and returns a result object. The result is written to the audit table and counted in Prometheus metrics.

```mermaid
flowchart LR
    agent["LLM agent"] -->|"tool call"| api["FastAPI bridge"]
    api --> auth["Token check"]
    auth --> exec["Tool executor"]
    exec -->|"scope check"| reg["Tool registry"]
    exec -->|"HTTP request"| upstream["Internal REST APIs"]
    upstream -->|"JSON body"| exec
    exec --> audit[("Audit table")]
    exec --> metrics["Prometheus metrics"]
    reg -->|"OpenAI tool schema"| agent
    manifests["YAML manifests"] --> reg
```

## Key decisions

- Tools are declared in YAML and validated at startup rather than registered in code, so a new endpoint is a manifest entry; the cost is that anything the manifest cannot express, such as a custom signing step, needs a code change.
- One executor handles every call, which keeps scope checks, timeouts and audit writes in one place; the cost is that a tool with unusual transport needs cannot bypass it.
- The audit write is best effort: a database outage logs a warning and the call still returns, so the bridge keeps serving tools; the cost is that a few rows can be missing during an outage.
- The API returns HTTP 200 with an `ok` flag for a failed upstream call, which keeps the agent's parsing simple; the cost is that plain HTTP clients must read the body to see the failure.

## Tech stack

- Python 3.11+, FastAPI and uvicorn
- httpx for upstream calls, pydantic and pydantic-settings for validation and configuration
- SQLAlchemy 2 with psycopg 3 against PostgreSQL for the audit table
- PyYAML for manifests, prometheus-client for metrics
- Docker and Kubernetes manifests for deployment

## Quick start

Prerequisites: Python 3.11 or newer, PostgreSQL 14 or newer, and the dependencies pinned in `pyproject.toml`.

```bash
pip install -e ".[dev]"
toolgate validate --tool-dir tools
toolgate serve --host 0.0.0.0 --port 8000
kubectl apply -f deploy/k8s.yaml
pytest
```

| Variable | What it sets | Default |
| --- | --- | --- |
| `TOOLGATE_DATABASE_URL` | SQLAlchemy URL of the audit database | `postgresql+psycopg://toolgate:toolgate@localhost:5432/toolgate` |
| `TOOLGATE_TOOL_DIR` | Directory scanned for manifests | `tools` |
| `TOOLGATE_CALL_TIMEOUT_S` | Default timeout for one upstream call | `5.0` |
| `TOOLGATE_MAX_RETRIES` | Retries after a 5xx or connection error | `2` |
| `TOOLGATE_API_TOKEN` | Shared token sent in `X-API-Token` | `dev-token` |
| `TOOLGATE_OPENAI_MODEL` | Model name written into the tool definitions | `gpt-4o-mini` |
| `TOOLGATE_UPSTREAM_BASE_URL` | Base URL for relative tool URLs | `http://localhost:8080` |

## Usage

```bash
curl -s -X POST http://localhost:8000/tools/incidents.list_open/call \
  -H "X-API-Token: dev-token" \
  -H "Content-Type: application/json" \
  -d '{"arguments": {"severity": "sev2", "limit": 5}, "scopes": ["incidents:read"]}'
```

```json
{
  "call_id": "6f1c2a4e-8b0d-4f3a-9a51-2c7d0e5b1f88",
  "tool": "incidents.list_open",
  "ok": true,
  "status": 200,
  "body": {"incidents": [{"id": "INC-4821", "severity": "sev2", "status": "investigating"}]},
  "latency_ms": 38,
  "error": null
}
```

## Project structure

```text
toolgate/        config, manifest loading, registry, executor, audit, API, CLI
tools/           sample manifests for incidents and the service catalog
tests/           manifest and executor tests
deploy/          Kubernetes deployment, service and config map
ui/              static dashboard page
```

## Limitations

- Manifests cover plain HTTP JSON APIs; SOAP, gRPC and streaming endpoints are not supported.
- Scopes are passed by the caller in the request body and checked against the tool, not resolved from an identity provider.
- The registry is loaded once at startup, so a manifest change needs a restart.
- The audit table grows without a retention job.

## License

MIT. The project builds on FastAPI, httpx, SQLAlchemy and prometheus-client, each under its own licence.
