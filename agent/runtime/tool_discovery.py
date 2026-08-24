from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from ..tool_registry import AgentTool, ToolRegistry
from .tool_catalog import schema_tool_name, tool_runtime_metadata


TOOL_SEARCH_NAME = "tool_search"
DEFAULT_CORE_TOOL_LIMIT = 6
DEFAULT_SEARCH_RESULT_LIMIT = 8
MAX_NAMESPACE_TOOLS = 10
_NATIVE_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]+|[\u3400-\u9fff]{1,8}")
_CORE_TOOL_NAMES = (
    "datetime",
    "vision_analyze",
    "generate_image",
    "recall_user_memory",
    "recall_group_memory",
    "inspect_current_user_avatar",
    "web_search",
    "search_web",
)


def normalize_tool_disclosure_mode(value: Any) -> str:
    mode = str(value or "off").strip().lower()
    return mode if mode in {"off", "client", "auto", "native"} else "off"


def _namespace(tool: AgentTool) -> str:
    metadata = dict(tool.metadata or {})
    raw = str(metadata.get("namespace") or metadata.get("source_kind") or "").strip()
    if not raw:
        name = str(tool.name or "")
        raw = name.split("__", 1)[0] if "__" in name else "builtin"
    return _NATIVE_NAME_RE.sub("_", raw).strip("_")[:48] or "builtin"


def _tokens(value: Any) -> set[str]:
    text = str(value or "").casefold()
    tokens = {item.casefold() for item in _TOKEN_RE.findall(text)}
    # Chinese tool queries often do not contain spaces. Short n-grams keep the
    # local index useful without turning this into a conversational intent gate.
    chinese = "".join(char for char in text if "\u3400" <= char <= "\u9fff")
    for size in (2, 3, 4):
        tokens.update(chinese[index : index + size] for index in range(max(0, len(chinese) - size + 1)))
    return {token for token in tokens if token}


def _tool_index_item(registry: ToolRegistry, tool: AgentTool) -> dict[str, Any]:
    metadata = tool_runtime_metadata(registry, tool.name)
    risk = str(metadata.get("risk_level", "low") or "low")[:24]
    side_effect = str(metadata.get("side_effect", "none") or "none")[:32]
    permission = str(metadata.get("permission") or metadata.get("permission_requirement") or "").strip()
    if not permission:
        permission = "admin" if risk == "admin" else "runtime_policy"
    return {
        "name": tool.name,
        "namespace": _namespace(tool),
        "description": str(tool.description or "").strip()[:240],
        "risk": risk,
        "side_effect": side_effect,
        "has_side_effect": side_effect not in {"", "none"},
        "permission": permission[:64],
    }


def _schema_by_name(schemas: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        name: schema
        for schema in schemas
        for name in (schema_tool_name(schema),)
        if name
    }


def _flat_response_function(schema: dict[str, Any], *, defer_loading: bool) -> dict[str, Any]:
    function = schema.get("function") if isinstance(schema.get("function"), dict) else {}
    output = {
        "type": "function",
        "name": str(function.get("name") or "")[:64],
        "description": str(function.get("description") or "")[:1024],
        "parameters": dict(function.get("parameters") or {"type": "object", "properties": {}}),
    }
    if defer_loading:
        output["defer_loading"] = True
    return output


def build_native_tool_search_payload(
    registry: ToolRegistry,
    schemas: list[dict[str, Any]],
    *,
    core_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Build the official Responses Tool Search shape.

    This payload is used only by callers that explicitly implement
    ``chat_with_deferred_tools`` and declare native support. Chat-completions
    compatible callers never receive this shape.
    """

    core = set(core_names or ())
    grouped: dict[str, list[dict[str, Any]]] = {}
    for schema in schemas:
        name = schema_tool_name(schema)
        tool = registry.get(name)
        if not name or tool is None:
            continue
        namespace = _namespace(tool)
        grouped.setdefault(namespace, []).append(
            _flat_response_function(schema, defer_loading=name not in core)
        )

    payload: list[dict[str, Any]] = []
    for namespace, tools in sorted(grouped.items()):
        for chunk_index in range(0, len(tools), MAX_NAMESPACE_TOOLS):
            chunk = tools[chunk_index : chunk_index + MAX_NAMESPACE_TOOLS]
            suffix = "" if chunk_index == 0 else f"_{chunk_index // MAX_NAMESPACE_TOOLS + 1}"
            payload.append(
                {
                    "type": "namespace",
                    "name": f"{namespace}{suffix}"[:64],
                    "description": f"{namespace} namespace tools",
                    "tools": chunk,
                }
            )
    payload.append({"type": "tool_search"})
    return payload


@dataclass(slots=True)
class ToolDisclosureSession:
    registry: ToolRegistry
    mode: str = "client"
    core_limit: int = DEFAULT_CORE_TOOL_LIMIT
    search_limit: int = DEFAULT_SEARCH_RESULT_LIMIT
    loaded_names: set[str] = field(default_factory=set)
    _last_exposed_names: set[str] = field(default_factory=set, init=False)
    _candidate_names: set[str] = field(default_factory=set, init=False)

    def __post_init__(self) -> None:
        self.mode = normalize_tool_disclosure_mode(self.mode)
        self.core_limit = max(1, min(10, int(self.core_limit)))
        self.search_limit = max(1, min(8, int(self.search_limit)))

    @property
    def search_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": TOOL_SEARCH_NAME,
                "description": (
                    "Search the local tool index. Returns metadata only; it never executes a tool. "
                    "Selected full schemas become available on the next model step."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Capability or operation to find.",
                            "maxLength": 200,
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Optional exact namespace filter.",
                            "maxLength": 64,
                        },
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        }

    def _core_names(self, schemas: list[dict[str, Any]]) -> set[str]:
        by_name = _schema_by_name(schemas)
        ordered: list[str] = []
        for name in _CORE_TOOL_NAMES:
            if name in by_name and name not in ordered:
                ordered.append(name)
        for schema in schemas:
            name = schema_tool_name(schema)
            if not name or name in ordered:
                continue
            metadata = tool_runtime_metadata(self.registry, name)
            if (
                str(metadata.get("risk_level", "low")) == "low"
                and str(metadata.get("side_effect", "none")) == "none"
            ):
                ordered.append(name)
        for schema in schemas:
            name = schema_tool_name(schema)
            if name and name not in ordered:
                ordered.append(name)
        return set(ordered[: self.core_limit])

    def client_schemas(self, schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self.mode == "off":
            self._last_exposed_names = set(_schema_by_name(schemas))
            return list(schemas)
        by_name = _schema_by_name(schemas)
        self._candidate_names = set(by_name)
        exposed = self._core_names(schemas) | (self.loaded_names & self._candidate_names)
        self._last_exposed_names = set(exposed) | {TOOL_SEARCH_NAME}
        return [schema for schema in schemas if schema_tool_name(schema) in exposed] + [self.search_schema]

    def native_payload(self, schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
        core = self._core_names(schemas)
        self._candidate_names = set(_schema_by_name(schemas))
        self._last_exposed_names = set(self._candidate_names) | {TOOL_SEARCH_NAME}
        return build_native_tool_search_payload(self.registry, schemas, core_names=core)

    def is_call_allowed(self, tool_name: Any) -> bool:
        name = str(tool_name or "").strip()
        return self.mode == "off" or name in self._last_exposed_names

    def search(self, *, query: Any = "", namespace: Any = "", **_extra: Any) -> str:
        needle = str(query or "").strip()[:200]
        namespace_filter = _NATIVE_NAME_RE.sub("_", str(namespace or "")).strip("_")[:64]
        query_tokens = _tokens(needle)
        candidates: list[tuple[int, str, dict[str, Any]]] = []
        for tool in self.registry.active():
            if tool.name not in self._candidate_names:
                continue
            item = _tool_index_item(self.registry, tool)
            if namespace_filter and item["namespace"] != namespace_filter:
                continue
            haystack = " ".join(
                [item["name"], item["namespace"], item["description"], *(tool.metadata.get("intent_tags") or [])]
            )
            haystack_tokens = _tokens(haystack)
            overlap = len(query_tokens & haystack_tokens)
            contains = 1 if needle and needle.casefold() in haystack.casefold() else 0
            score = overlap * 10 + contains * 20
            if score > 0 or not query_tokens:
                candidates.append((-score, item["name"], item))
        candidates.sort(key=lambda row: (row[0], row[1]))
        items = [row[2] for row in candidates[: self.search_limit]]
        self.loaded_names.update(item["name"] for item in items)
        return json.dumps(
            {
                "status": "ok",
                "query": needle,
                "candidates": items,
                "loaded_next_step": [item["name"] for item in items],
                "executed": False,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )


def caller_supports_native_tool_search(caller: Any) -> bool:
    return bool(
        getattr(caller, "supports_responses_tool_search", False)
        and callable(getattr(caller, "chat_with_deferred_tools", None))
    )


def resolve_tool_disclosure_mode(configured_mode: Any, caller: Any) -> str:
    mode = normalize_tool_disclosure_mode(configured_mode)
    if mode == "auto":
        return "native" if caller_supports_native_tool_search(caller) else "client"
    if mode == "native" and not caller_supports_native_tool_search(caller):
        return "client"
    return mode


__all__ = [
    "DEFAULT_CORE_TOOL_LIMIT",
    "DEFAULT_SEARCH_RESULT_LIMIT",
    "MAX_NAMESPACE_TOOLS",
    "TOOL_SEARCH_NAME",
    "ToolDisclosureSession",
    "build_native_tool_search_payload",
    "caller_supports_native_tool_search",
    "normalize_tool_disclosure_mode",
    "resolve_tool_disclosure_mode",
]
