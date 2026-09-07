from __future__ import annotations

import json
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Iterable

from ..tool_registry import AgentTool, ToolRegistry
from .tool_catalog import schema_tool_name, tool_runtime_metadata


TOOL_SEARCH_NAME = "tool_search"
DEFAULT_CORE_TOOL_LIMIT = 2
DEFAULT_SEARCH_RESULT_LIMIT = 4
MAX_CLIENT_REAL_SCHEMAS = 8
MAX_NAMESPACE_TOOLS = 10
_NATIVE_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]+|[\u3400-\u9fff]{1,8}")
_INTENT_TAG_ALIASES = {
    "lookup_web": {"lookup"},
    "lookup_plugin": {"plugin_question", "plugin_local", "plugin_latest"},
    "runtime_capability": {"runtime_capability", "plugin_question", "plugin_local"},
    "image_generation": {"image_generation", "image_gen"},
    "expression": {"expression"},
    "vision": {"vision"},
    "memory": {"memory"},
}


def normalize_tool_disclosure_mode(value: Any) -> str:
    # Missing settings opt into progressive disclosure, while an explicitly
    # configured off remains an escape hatch for old provider integrations.
    mode = str(value or "auto").strip().lower()
    return mode if mode in {"off", "client", "auto", "native"} else "auto"


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
        "intent_tags": [str(item)[:48] for item in metadata.get("intent_tags", []) if str(item).strip()][:8],
        "evidence_kind": str(metadata.get("evidence_kind", "generic") or "generic")[:32],
        "latency_class": str(metadata.get("latency_class", "normal") or "normal")[:24],
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
    loaded_names: OrderedDict[str, None] = field(default_factory=OrderedDict)
    executed_names: set[str] = field(default_factory=set)
    turn_intents: set[str] = field(default_factory=set)
    recommended_names: list[str] = field(default_factory=list)
    allow_side_effects: bool = False
    initialized: bool = False
    discovery_count: int = 0
    _last_exposed_names: set[str] = field(default_factory=set, init=False)
    _candidate_names: set[str] = field(default_factory=set, init=False)

    def __post_init__(self) -> None:
        self.mode = normalize_tool_disclosure_mode(self.mode)
        self.core_limit = max(1, min(10, int(self.core_limit)))
        self.search_limit = max(1, min(4, int(self.search_limit)))

    def configure_turn(
        self,
        *,
        tool_intents: Iterable[Any] = (),
        recommended_tools: Iterable[Any] = (),
        speech_act: Any = "",
        caller: Any = None,
    ) -> None:
        """Bind this disclosure session to the model's existing TurnPlan.

        This is deliberately metadata-only: it narrows schemas, but never
        infers a conversational intent from the user's wording.
        """
        self.turn_intents = {str(item or "").strip() for item in tool_intents if str(item or "").strip() and str(item or "").strip() != "none"}
        self.recommended_names = self.filter_recommended_tools(recommended_tools)
        self.allow_side_effects = bool(
            str(speech_act or "").strip().lower() == "execute_action"
            or self.turn_intents & {"expression", "image_generation"}
        )
    def _eligible_names(self, schemas: list[dict[str, Any]]) -> set[str]:
        by_name = _schema_by_name(schemas)
        if not self.turn_intents:
            return set()
        eligible: set[str] = set()
        wanted = set(self.turn_intents)
        for intent in list(wanted):
            wanted.update(_INTENT_TAG_ALIASES.get(intent, set()))
        for name in by_name:
            metadata = tool_runtime_metadata(self.registry, name)
            tags = {str(item or "").strip() for item in (metadata.get("intent_tags") or [])}
            if tags & wanted:
                eligible.add(name)
        return eligible

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

    def _core_names(self, schemas: list[dict[str, Any]]) -> list[str]:
        eligible = self._eligible_names(schemas)
        # Client-side first disclosure is deliberately tiny for every caller;
        # provider wrapper class names are not a reliable Gemini capability.
        limit = min(2, self.core_limit)
        eligible_ordered = list(
            dict.fromkeys(
                name
                for schema in schemas
                for name in (schema_tool_name(schema),)
                if name in eligible
            )
        )
        ordered = [name for name in self.recommended_names if name in eligible]
        ordered.extend(name for name in eligible_ordered if name not in ordered)
        return ordered[:limit]

    def client_schemas(self, schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self.mode == "off":
            self._last_exposed_names = set(_schema_by_name(schemas))
            return list(schemas)
        schemas = [
            schema
            for schema in schemas
            if self.allow_side_effects
            or str(tool_runtime_metadata(self.registry, schema_tool_name(schema)).get("side_effect", "none") or "none")
            in {"", "none"}
        ]
        by_name = _schema_by_name(schemas)
        self._candidate_names = set(by_name)
        # TurnPlan only preloads matching real schemas.  The directory remains
        # a safe index over all schemas so the model can discover a capability
        # that the planner did not anticipate.
        if not self.initialized:
            for name in self._core_names(schemas):
                self.loaded_names[name] = None
            self.initialized = True
        retained = set(self.loaded_names) & self._candidate_names
        exposed = retained
        while len(exposed) > MAX_CLIENT_REAL_SCHEMAS:
            evicted = next((name for name in self.loaded_names if name in exposed and name not in self.executed_names), None)
            if evicted is None:
                break
            self.loaded_names.pop(evicted, None)
            exposed.discard(evicted)
        self._last_exposed_names = set(exposed) | {TOOL_SEARCH_NAME}
        return [schema for schema in schemas if schema_tool_name(schema) in exposed] + [self.search_schema]

    def native_payload(self, schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
        core = set(self._core_names(schemas))
        self._candidate_names = set(_schema_by_name(schemas))
        self._last_exposed_names = set(self._candidate_names) | {TOOL_SEARCH_NAME}
        return build_native_tool_search_payload(self.registry, schemas, core_names=core)

    def is_call_allowed(self, tool_name: Any) -> bool:
        name = str(tool_name or "").strip()
        return self.mode == "off" or name in self._last_exposed_names

    def mark_executed(self, tool_name: Any) -> None:
        name = str(tool_name or "").strip()
        if name and name != TOOL_SEARCH_NAME:
            self.executed_names.add(name)

    def filter_recommended_tools(self, names: Iterable[Any]) -> list[str]:
        """Keep only real, TurnPlan-eligible tool names from model advice."""
        eligible = self._eligible_names(self.registry.openai_schemas())
        result: list[str] = []
        for raw in names:
            name = str(raw or "").strip()
            if name and name not in result and self.registry.get(name) is not None and name in eligible:
                result.append(name)
        return result

    def search(self, *, query: Any = "", namespace: Any = "", **_extra: Any) -> str:
        self.discovery_count += 1
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
        loaded_next_step: list[str] = []
        protected_names: set[str] = set()
        for item in items:
            name = item["name"]
            if name not in self.loaded_names:
                while len(self.loaded_names) >= MAX_CLIENT_REAL_SCHEMAS:
                    evicted = next(
                        (
                            old
                            for old in self.loaded_names
                            if old not in self.executed_names and old not in protected_names
                        ),
                        None,
                    )
                    if evicted is None:
                        break
                    self.loaded_names.pop(evicted, None)
                if len(self.loaded_names) >= MAX_CLIENT_REAL_SCHEMAS:
                    continue
            self.loaded_names.pop(name, None)
            self.loaded_names[name] = None
            loaded_next_step.append(name)
            protected_names.add(name)
        # Preserve schemas that have executed in this turn.  Only the oldest
        # unexecuted discovery candidate may be displaced when capacity fills.
        while len(self.loaded_names) > MAX_CLIENT_REAL_SCHEMAS:
            evicted = next(
                (name for name in self.loaded_names if name not in self.executed_names and name not in protected_names),
                None,
            )
            if evicted is None:
                break
            self.loaded_names.pop(evicted, None)
        exhausted = bool(items) and not loaded_next_step
        return json.dumps(
            {
                "status": "tool_disclosure_budget_exhausted" if exhausted else "ok",
                "query": needle,
                "candidates": items,
                "loaded_next_step": loaded_next_step,
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
    "MAX_CLIENT_REAL_SCHEMAS",
    "MAX_NAMESPACE_TOOLS",
    "TOOL_SEARCH_NAME",
    "ToolDisclosureSession",
    "build_native_tool_search_payload",
    "caller_supports_native_tool_search",
    "normalize_tool_disclosure_mode",
    "resolve_tool_disclosure_mode",
]
