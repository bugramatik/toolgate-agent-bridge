"""In-memory registry of tool specs and the OpenAI tool schema they produce."""

from __future__ import annotations

from typing import Iterable

from toolgate.manifest import ToolSpec


class UnknownToolError(KeyError):
    """Raised when a caller asks for a tool that is not registered."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name

    def __str__(self) -> str:  # KeyError quotes its argument, which reads badly in logs
        return f"unknown tool: {self.name}"


class ToolRegistry:
    """Holds the validated tools and renders them for the model."""

    def __init__(self, specs: Iterable[ToolSpec]) -> None:
        self._specs: dict[str, ToolSpec] = {}
        for spec in specs:
            if spec.name in self._specs:
                raise ValueError(f"duplicate tool name: {spec.name}")
            self._specs[spec.name] = spec

    @classmethod
    def from_specs(cls, specs: Iterable[ToolSpec]) -> "ToolRegistry":
        return cls(specs)

    def get(self, name: str) -> ToolSpec:
        try:
            return self._specs[name]
        except KeyError:
            raise UnknownToolError(name) from None

    def names(self) -> list[str]:
        return sorted(self._specs)

    def all(self) -> list[ToolSpec]:
        return [self._specs[name] for name in self.names()]

    def scopes(self) -> set[str]:
        scopes: set[str] = set()
        for spec in self._specs.values():
            scopes.update(spec.scopes)
        return scopes

    def openai_tools(self) -> list[dict]:
        """Return the registry as the `tools` array an OpenAI chat call expects."""
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters_schema(),
                },
            }
            for spec in self.all()
        ]

    def __len__(self) -> int:
        return len(self._specs)

    def __contains__(self, name: object) -> bool:
        return name in self._specs
