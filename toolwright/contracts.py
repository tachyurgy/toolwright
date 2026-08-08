"""Typed tool contracts for an agent, checked on both sides of the call.

An MCP server hands a model a set of tools and then executes whatever the model
asks for. That is a remote code execution surface where the caller is a text
generator, so the contract cannot be advisory.

Most implementations validate arguments and stop there. That covers the obvious
half. The half that bites later is the **return** side: a tool that quietly
returns a shape its own declaration does not permit corrupts the model's next
step, and the resulting failure surfaces somewhere else entirely, in a step that
looks unrelated. By then the trail is cold.

So every call is checked twice, against the same declared schema:

  * **Inbound.** Arguments are validated, coerced only where coercion is lossless
    and unambiguous, and unknown arguments are rejected rather than ignored.
  * **Outbound.** The tool's own return value is validated before it leaves. A
    tool that violates its declaration fails loudly at the tool, not three steps
    downstream.

Plus two smaller rules that exist because agents reliably find them:

  * **Unknown properties are refused, not dropped.** Silently discarding an
    argument the model believed it was passing produces a call that does
    something other than what was asked, with no error anywhere.
  * **A tool that raises is reported as a failed call**, never as a result.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable


class ContractError(Exception):
    """A tool declaration is itself invalid."""


@dataclass
class Violation:
    where: str  # "arguments" or "result"
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.where}{'.' + self.path if self.path else ''}: {self.message}"


@dataclass
class CallResult:
    tool: str
    ok: bool
    value: Any = None
    violations: list[Violation] = field(default_factory=list)
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "tool": self.tool,
            "ok": self.ok,
            "value": self.value,
            "violations": [
                {"where": v.where, "path": v.path, "message": v.message} for v in self.violations
            ],
            "error": self.error,
        }


# -- schema ---------------------------------------------------------------

_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list,),
}


def _type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    for name, types in _TYPES.items():
        if name not in ("boolean", "integer", "number") and isinstance(value, types):
            return name
    return type(value).__name__


def validate(value: Any, schema: dict, where: str, path: str = "") -> list[Violation]:
    """Validate `value` against a small JSON-Schema subset.

    Supports type, properties, required, additionalProperties, enum, minimum,
    maximum, minLength, maxLength, pattern and items. Deliberately a subset: a
    contract nobody can read is a contract nobody checks.
    """
    out: list[Violation] = []
    expected = schema.get("type")

    if expected:
        if expected not in _TYPES:
            raise ContractError(f"unknown type in schema: {expected}")
        # bool is a subclass of int in Python; an agent passing True where a
        # number belongs is a real mistake and must not slip through.
        if expected in ("number", "integer") and isinstance(value, bool):
            out.append(Violation(where, path, f"expected {expected}, got boolean"))
            return out
        if not isinstance(value, _TYPES[expected]):
            out.append(Violation(where, path, f"expected {expected}, got {_type_name(value)}"))
            return out

    if "enum" in schema and value not in schema["enum"]:
        out.append(Violation(where, path, f"must be one of {schema['enum']}, got {value!r}"))

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            out.append(Violation(where, path, f"must be >= {schema['minimum']}, got {value}"))
        if "maximum" in schema and value > schema["maximum"]:
            out.append(Violation(where, path, f"must be <= {schema['maximum']}, got {value}"))

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            out.append(Violation(where, path, f"must be at least {schema['minLength']} characters"))
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            out.append(Violation(where, path, f"must be at most {schema['maxLength']} characters"))
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.append(Violation(where, path, f"must match {schema['pattern']}"))

    if isinstance(value, dict) and "properties" in schema:
        props = schema["properties"]
        for key in schema.get("required", []):
            if key not in value:
                out.append(Violation(where, f"{path}.{key}".lstrip("."), "is required"))
        if schema.get("additionalProperties", False) is False:
            for key in value:
                if key not in props:
                    # Refused, not dropped. A silently discarded argument makes
                    # the call do something other than what was asked.
                    out.append(
                        Violation(where, f"{path}.{key}".lstrip("."), "is not a declared property")
                    )
        for key, sub in props.items():
            if key in value:
                out.extend(validate(value[key], sub, where, f"{path}.{key}".lstrip(".")))

    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            out.extend(validate(item, schema["items"], where, f"{path}[{i}]"))

    return out


# -- tools ----------------------------------------------------------------


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    returns: dict
    handler: Callable[..., Any]

    def __post_init__(self) -> None:
        if self.parameters.get("type") != "object":
            raise ContractError(f"{self.name}: parameters must be an object schema")
        if "properties" not in self.parameters:
            raise ContractError(f"{self.name}: parameters must declare properties")
        # Default to closed. An open contract on a tool an LLM can call is not a
        # contract, and defaulting to closed means forgetting to say so is safe.
        self.parameters.setdefault("additionalProperties", False)


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ContractError(f"duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def describe(self) -> list[dict]:
        """The tool list an MCP client would receive."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.parameters,
                "outputSchema": t.returns,
            }
            for t in self._tools.values()
        ]

    def call(self, name: str, arguments: Any) -> CallResult:
        tool = self._tools.get(name)
        if tool is None:
            return CallResult(tool=name, ok=False, error=f"unknown tool: {name}")

        if not isinstance(arguments, dict):
            return CallResult(
                tool=name,
                ok=False,
                violations=[Violation("arguments", "", f"expected object, got {_type_name(arguments)}")],
            )

        inbound = validate(arguments, tool.parameters, "arguments")
        if inbound:
            return CallResult(tool=name, ok=False, violations=inbound)

        try:
            value = tool.handler(**arguments)
        except Exception as exc:  # noqa: BLE001 -- a raising tool is a failed call
            return CallResult(tool=name, ok=False, error=f"{type(exc).__name__}: {exc}")

        outbound = validate(value, tool.returns, "result")
        if outbound:
            # The tool broke its own declaration. Failing here keeps the blame
            # attached to the tool rather than to whatever consumes it next.
            return CallResult(tool=name, ok=False, value=value, violations=outbound)

        return CallResult(tool=name, ok=True, value=value)


def to_mcp_error(result: CallResult) -> dict:
    """Render a failed call the way an MCP client expects to receive it."""
    detail = "; ".join(str(v) for v in result.violations) or result.error or "unknown error"
    return {"isError": True, "content": [{"type": "text", "text": detail}]}


def to_mcp_result(result: CallResult) -> dict:
    if not result.ok:
        return to_mcp_error(result)
    return {"isError": False, "content": [{"type": "text", "text": json.dumps(result.value)}]}
