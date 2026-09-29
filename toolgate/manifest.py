"""Load and validate tool manifests from a directory of YAML files."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

SCOPE_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*:[a-z][a-z0-9_-]*$")


class ManifestError(Exception):
    """Raised when a manifest file cannot be read or does not match ToolSpec."""


class ToolParam(BaseModel):
    """One parameter of a tool, and where it travels in the HTTP request."""

    name: str
    location: Literal["path", "query", "body"] = "query"
    required: bool = False
    schema_: dict[str, Any] = Field(default_factory=dict, alias="schema")

    model_config = {"populate_by_name": True}


class ToolSpec(BaseModel):
    """A single tool declared in a manifest."""

    name: str
    description: str
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    url: str
    scopes: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    timeout_s: float = Field(default=5.0, gt=0)
    retries: int = Field(default=1, ge=0, le=5)

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)?", value):
            raise ValueError(f"invalid tool name: {value}")
        return value

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        if not value:
            raise ValueError("url is required")
        return value

    @field_validator("scopes")
    @classmethod
    def _check_scopes(cls, value: list[str]) -> list[str]:
        for scope in value:
            if not SCOPE_PATTERN.fullmatch(scope):
                raise ValueError(f"invalid scope: {scope}")
        return value

    @model_validator(mode="after")
    def _check_path_params(self) -> "ToolSpec":
        for placeholder in re.findall(r"\{([a-z_][a-z0-9_]*)\}", self.url):
            if placeholder not in self.param_names():
                raise ValueError(f"url placeholder {{{placeholder}}} has no parameter")
        return self

    def param_names(self) -> list[str]:
        return list((self.params.get("properties") or {}).keys())

    def param_location(self, name: str) -> str:
        """Path parameters are the ones the URL names; the rest go in query or body."""
        if "{" + name + "}" in self.url:
            return "path"
        if self.method in {"GET", "DELETE"}:
            return "query"
        return "body"

    def parameters_schema(self) -> dict[str, Any]:
        """The JSON schema handed to the model for this tool's arguments."""
        schema = dict(self.params) or {"type": "object", "properties": {}}
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        schema.setdefault("required", [])
        return schema


def _read_file(path: Path) -> list[dict[str, Any]]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ManifestError(f"{path.name}: invalid YAML: {exc}") from exc
    if raw is None:
        return []
    if isinstance(raw, dict):
        entries = raw.get("tools", [])
    elif isinstance(raw, list):
        entries = raw
    else:
        raise ManifestError(f"{path.name}: expected a list of tools")
    if not isinstance(entries, list):
        raise ManifestError(f"{path.name}: 'tools' must be a list")
    return entries


def load_manifests(path: Path) -> list[ToolSpec]:
    """Read every *.yaml file under path and return the validated tools."""
    directory = Path(path)
    if not directory.is_dir():
        raise ManifestError(f"tool directory not found: {directory}")

    specs: list[ToolSpec] = []
    seen: set[str] = set()
    for file in sorted(directory.glob("*.y*ml")):
        for index, entry in enumerate(_read_file(file)):
            if not isinstance(entry, dict):
                raise ManifestError(f"{file.name}: entry {index} is not a mapping")
            try:
                spec = ToolSpec.model_validate(entry)
            except ValidationError as exc:
                fields = ", ".join(str(err["loc"][0]) for err in exc.errors())
                raise ManifestError(f"{file.name}: tool {index} is invalid ({fields})") from exc
            if spec.name in seen:
                raise ManifestError(f"duplicate tool name: {spec.name}")
            seen.add(spec.name)
            specs.append(spec)

    if not specs:
        raise ManifestError(f"no tools found in {directory}")
    return specs
