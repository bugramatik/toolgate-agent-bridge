"""Command line entry point: validate manifests, print the tool schema, run the server."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from toolgate.config import get_settings
from toolgate.manifest import ManifestError, load_manifests
from toolgate.registry import ToolRegistry


def _cmd_validate(args: argparse.Namespace) -> int:
    tool_dir = Path(args.tool_dir)
    try:
        specs = load_manifests(tool_dir)
    except ManifestError as exc:
        print(f"manifest error: {exc}", file=sys.stderr)
        return 1

    registry = ToolRegistry.from_specs(specs)
    print(f"ok: {len(registry)} tools from {tool_dir}")
    for name in registry.names():
        spec = registry.get(name)
        print(f"  {name:<32} {spec.method:<6} {spec.url}")
    return 0


def _cmd_schema(args: argparse.Namespace) -> int:
    tool_dir = Path(args.tool_dir)
    try:
        registry = ToolRegistry.from_specs(load_manifests(tool_dir))
    except ManifestError as exc:
        print(f"manifest error: {exc}", file=sys.stderr)
        return 1

    json.dump(registry.openai_tools(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "toolgate.api:app",
        host=args.host,
        port=args.port,
        log_level=settings.log_level.lower(),
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    settings = get_settings()
    parser = argparse.ArgumentParser(prog="toolgate", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="load and check every manifest in a directory")
    validate.add_argument("--tool-dir", default=settings.tool_dir)
    validate.set_defaults(func=_cmd_validate)

    schema = sub.add_parser("schema", help="print the OpenAI tool definitions as JSON")
    schema.add_argument("--tool-dir", default=settings.tool_dir)
    schema.set_defaults(func=_cmd_schema)

    serve = sub.add_parser("serve", help="run the HTTP API")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=_cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
