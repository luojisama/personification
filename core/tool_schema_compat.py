from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence


class SchemaAction(str, Enum):
    KEEP = "keep"
    DOWNGRADE = "downgrade"
    EXCLUDE = "exclude"


class ProbeVerdict(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SchemaCompatibilityProfile:
    name: str
    allow_one_of: bool = True
    allow_any_of: bool = True
    allow_union_types: bool = True
    allow_nullable: bool = False
    allow_default: bool = True
    allow_complex_enum: bool = False
    allow_boolean_additional_properties: bool = True
    allow_schema_additional_properties: bool = True
    max_schema_depth: int = 16
    max_description_chars: int = 4096
    max_enum_values: int = 128
    require_object_root: bool = True


JSON_SCHEMA_PROFILE = SchemaCompatibilityProfile(
    name="json_schema",
    allow_complex_enum=True,
    max_schema_depth=32,
    max_description_chars=16_384,
    max_enum_values=1024,
)

OPENAI_PROFILE = SchemaCompatibilityProfile(
    name="openai",
    allow_one_of=True,
    allow_any_of=True,
    allow_union_types=True,
    allow_nullable=False,
    allow_default=True,
    allow_complex_enum=False,
    max_schema_depth=16,
    max_description_chars=4096,
)

ANTHROPIC_PROFILE = SchemaCompatibilityProfile(
    name="anthropic",
    allow_one_of=True,
    allow_any_of=True,
    allow_union_types=True,
    allow_nullable=False,
    allow_default=True,
    allow_complex_enum=False,
    max_schema_depth=16,
    max_description_chars=4096,
)

GEMINI_PROFILE = SchemaCompatibilityProfile(
    name="gemini",
    allow_one_of=False,
    allow_any_of=False,
    allow_union_types=False,
    allow_nullable=True,
    allow_default=False,
    allow_complex_enum=False,
    allow_boolean_additional_properties=True,
    allow_schema_additional_properties=False,
    max_schema_depth=8,
    max_description_chars=1024,
    max_enum_values=64,
)

OPENAI_COMPATIBLE_PROFILE = SchemaCompatibilityProfile(
    name="openai_compatible",
    allow_one_of=False,
    allow_any_of=False,
    allow_union_types=False,
    allow_nullable=False,
    allow_default=False,
    allow_complex_enum=False,
    allow_boolean_additional_properties=True,
    allow_schema_additional_properties=False,
    max_schema_depth=8,
    max_description_chars=1024,
    max_enum_values=64,
)


BUILTIN_PROFILES: dict[str, SchemaCompatibilityProfile] = {
    profile.name: profile
    for profile in (
        JSON_SCHEMA_PROFILE,
        OPENAI_PROFILE,
        ANTHROPIC_PROFILE,
        GEMINI_PROFILE,
        OPENAI_COMPATIBLE_PROFILE,
    )
}


@dataclass(frozen=True, slots=True)
class SchemaIssue:
    code: str
    path: str
    action: SchemaAction

    def to_safe_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "action": self.action.value}


@dataclass(frozen=True, slots=True)
class ToolCompatibilitySummary:
    name: str
    reason_codes: tuple[str, ...]

    def to_safe_dict(self) -> dict[str, Any]:
        return {"name": self.name, "reason_codes": list(self.reason_codes)}


@dataclass(frozen=True, slots=True)
class ToolSchemaDiagnostics:
    provider: str
    api_type: str
    model: str
    route_fingerprint: str
    profile: str
    input_tool_count: int
    tool_count: int
    excluded_count: int
    schema_chars: int
    schema_bytes: int
    tool_names_summary: tuple[str, ...]
    tool_names_omitted: int
    tool_names_hash: str
    tool_schema_hash: str
    excluded_tools: tuple[ToolCompatibilitySummary, ...]
    downgraded_tools: tuple[ToolCompatibilitySummary, ...]

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "api_type": self.api_type,
            "model": self.model,
            "route_fingerprint": self.route_fingerprint,
            "profile": self.profile,
            "input_tool_count": self.input_tool_count,
            "tool_count": self.tool_count,
            "excluded_count": self.excluded_count,
            "schema_chars": self.schema_chars,
            "schema_bytes": self.schema_bytes,
            "tool_names_summary": list(self.tool_names_summary),
            "tool_names_omitted": self.tool_names_omitted,
            "tool_names_hash": self.tool_names_hash,
            "tool_schema_hash": self.tool_schema_hash,
            "excluded_tools": [item.to_safe_dict() for item in self.excluded_tools],
            "downgraded_tools": [item.to_safe_dict() for item in self.downgraded_tools],
        }


@dataclass(frozen=True, slots=True)
class SchemaPreparationResult:
    tools: tuple[dict[str, Any], ...]
    diagnostics: ToolSchemaDiagnostics


@dataclass(frozen=True, slots=True)
class SchemaRejectionDiagnostic:
    status_code: int
    error_type: str
    reason_code: str
    field_path: str

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "status_code": self.status_code,
            "error_type": self.error_type,
            "reason_code": self.reason_code,
            "field_path": self.field_path,
        }


@dataclass(frozen=True, slots=True)
class SchemaMinimizationResult:
    verdict: ProbeVerdict
    minimal_tools: tuple[dict[str, Any], ...]
    tool_names: tuple[str, ...]
    probe_count: int
    complete: bool
    diagnostic_code: str


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.:-]+")
_SAFE_ERROR_ATOM_RE = re.compile(r"[^a-z0-9_.:-]+")
_SCHEMA_REJECTION_CODES = frozenset(
    {
        "invalid_function_parameters",
        "invalid_function_schema",
        "invalid_schema",
        "invalid_tool_schema",
        "schema_validation_error",
        "tool_schema_invalid",
    }
)
_COMBINATION_ANNOTATION_KEYS = frozenset(
    {"description", "title", "default", "examples", "$comment", "deprecated", "readOnly", "writeOnly"}
)


def _normalize_api_type(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def resolve_schema_profile(
    *,
    provider: Any = "",
    api_type: Any = "",
    profile: SchemaCompatibilityProfile | str | None = None,
) -> SchemaCompatibilityProfile:
    if isinstance(profile, SchemaCompatibilityProfile):
        return profile
    if isinstance(profile, str) and profile.strip():
        try:
            return BUILTIN_PROFILES[profile.strip().lower()]
        except KeyError as exc:
            raise ValueError(f"unknown schema compatibility profile: {profile}") from exc

    provider_name = str(provider or "").strip().lower()
    normalized_type = _normalize_api_type(api_type)
    if normalized_type in {"anthropic", "claude_code", "claude_cli"} or provider_name == "anthropic":
        return ANTHROPIC_PROFILE
    if normalized_type in {
        "gemini",
        "gemini_official",
        "gemini_cli",
        "antigravity",
        "antigravity_cli",
    } or provider_name in {"google", "gemini"}:
        return GEMINI_PROFILE
    if normalized_type in {"openai_codex", "codex", "responses"} or provider_name == "openai":
        return OPENAI_PROFILE
    return OPENAI_COMPATIBLE_PROFILE


def _safe_tool_name(value: Any, *, fallback: str = "unnamed_tool") -> str:
    rendered = _SAFE_NAME_RE.sub("_", str(value or "").strip()).strip("_.:-")
    return (rendered or fallback)[:80]


def _safe_route_fingerprint(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    if re.fullmatch(r"[0-9a-f]{8,64}", raw):
        return raw
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _tool_name(tool: Any, *, fallback: str = "unnamed_tool") -> str:
    if not isinstance(tool, Mapping):
        return fallback
    function = tool.get("function")
    if isinstance(function, Mapping):
        return _safe_tool_name(function.get("name"), fallback=fallback)
    return _safe_tool_name(tool.get("name"), fallback=fallback)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _schema_location(tool: dict[str, Any]) -> tuple[dict[str, Any], str] | None:
    function = tool.get("function")
    if isinstance(function, dict):
        if "parameters" not in function:
            function["parameters"] = {"type": "object", "properties": {}, "required": []}
        return function, "parameters"
    if "input_schema" in tool:
        return tool, "input_schema"
    if "parameters" not in tool:
        tool["parameters"] = {"type": "object", "properties": {}, "required": []}
    return tool, "parameters"


def _safe_path(path: str) -> str:
    rendered = re.sub(r"[^A-Za-z0-9_.$/\[\]-]+", "_", str(path or "$"))
    return rendered[:240] or "$"


def _path(parent: str, child: Any) -> str:
    token = str(child).replace("~", "~0").replace("/", "~1")
    return _safe_path(f"{parent}/{token}")


def _add_issue(
    issues: list[SchemaIssue],
    code: str,
    path: str,
    action: SchemaAction,
) -> None:
    issues.append(SchemaIssue(code=code, path=_safe_path(path), action=action))


def _truncate_description(
    container: dict[str, Any],
    *,
    profile: SchemaCompatibilityProfile,
    issues: list[SchemaIssue],
    path: str,
) -> bool:
    if "description" not in container:
        return True
    description = container.get("description")
    if not isinstance(description, str):
        _add_issue(issues, "description_invalid", _path(path, "description"), SchemaAction.EXCLUDE)
        return False
    limit = max(1, int(profile.max_description_chars))
    if len(description) <= limit:
        return True
    suffix = "..." if limit >= 3 else ""
    container["description"] = description[: max(0, limit - len(suffix))] + suffix
    _add_issue(issues, "description_truncated", _path(path, "description"), SchemaAction.DOWNGRADE)
    return True


def _enum_value_kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "number"
    if isinstance(value, str):
        return "string"
    return "complex"


def _merge_single_branch(node: dict[str, Any], keyword: str) -> bool:
    branches = node.get(keyword)
    if not isinstance(branches, list) or len(branches) != 1:
        return False
    branch = branches[0]
    if branch is True:
        node.pop(keyword, None)
        return True
    if not isinstance(branch, dict):
        return False
    parent = {key: value for key, value in node.items() if key != keyword}
    for key, value in branch.items():
        if key in parent and parent[key] != value and key not in {
            "description",
            "title",
            "default",
            "examples",
            "$comment",
        }:
            return False
    merged = dict(branch)
    merged.update(parent)
    node.clear()
    node.update(merged)
    return True


def _nullable_combination_branch(node: dict[str, Any], keyword: str) -> dict[str, Any] | None:
    branches = node.get(keyword)
    if not isinstance(branches, list) or len(branches) != 2:
        return None
    null_indexes = [
        index
        for index, branch in enumerate(branches)
        if isinstance(branch, Mapping) and branch.get("type") == "null" and len(branch) == 1
    ]
    if len(null_indexes) != 1:
        return None
    branch = branches[1 - null_indexes[0]]
    return dict(branch) if isinstance(branch, Mapping) else None


def _merge_nullable_combination(
    node: dict[str, Any],
    keyword: str,
    *,
    profile: SchemaCompatibilityProfile,
) -> bool:
    branch = _nullable_combination_branch(node, keyword)
    if branch is None:
        return False
    parent = {key: value for key, value in node.items() if key != keyword}
    if any(key not in _COMBINATION_ANNOTATION_KEYS for key in parent):
        return False
    for key, value in branch.items():
        if key in parent and parent[key] != value and key not in {
            "description",
            "title",
            "default",
            "examples",
            "$comment",
        }:
            return False
    merged = dict(branch)
    merged.update(parent)
    branch_type = merged.get("type")
    if not isinstance(branch_type, str) or branch_type == "null":
        return False
    if profile.allow_nullable:
        merged["nullable"] = True
    elif profile.allow_union_types:
        merged["type"] = [branch_type, "null"]
    else:
        return False
    node.clear()
    node.update(merged)
    return True


def _rewrite_schema(
    schema: Any,
    *,
    profile: SchemaCompatibilityProfile,
    issues: list[SchemaIssue],
    path: str,
    depth: int,
) -> bool:
    if depth > max(1, int(profile.max_schema_depth)):
        _add_issue(issues, "schema_depth_exceeded", path, SchemaAction.EXCLUDE)
        return False
    if schema is True:
        return True
    if schema is False or not isinstance(schema, dict):
        _add_issue(issues, "schema_node_invalid", path, SchemaAction.EXCLUDE)
        return False
    if not _truncate_description(schema, profile=profile, issues=issues, path=path):
        return False

    if "default" in schema and not profile.allow_default:
        schema.pop("default", None)
        _add_issue(issues, "default_removed", _path(path, "default"), SchemaAction.DOWNGRADE)

    nullable = schema.get("nullable")
    if nullable is not None and not isinstance(nullable, bool):
        _add_issue(issues, "nullable_invalid", _path(path, "nullable"), SchemaAction.EXCLUDE)
        return False
    if nullable is False and not profile.allow_nullable:
        schema.pop("nullable", None)
        _add_issue(issues, "nullable_false_removed", _path(path, "nullable"), SchemaAction.DOWNGRADE)
    elif nullable is True and not profile.allow_nullable:
        schema_type = schema.get("type")
        if profile.allow_union_types and isinstance(schema_type, str) and schema_type != "null":
            schema["type"] = [schema_type, "null"]
            schema.pop("nullable", None)
            _add_issue(issues, "nullable_to_union", _path(path, "nullable"), SchemaAction.DOWNGRADE)
        else:
            _add_issue(issues, "nullable_unsupported", _path(path, "nullable"), SchemaAction.EXCLUDE)
            return False

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        if not schema_type or any(not isinstance(item, str) for item in schema_type):
            _add_issue(issues, "union_type_invalid", _path(path, "type"), SchemaAction.EXCLUDE)
            return False
        unique_types = list(dict.fromkeys(schema_type))
        if len(unique_types) == 1:
            schema["type"] = unique_types[0]
            _add_issue(issues, "single_union_collapsed", _path(path, "type"), SchemaAction.DOWNGRADE)
        elif not profile.allow_union_types:
            non_null = [item for item in unique_types if item != "null"]
            if profile.allow_nullable and len(non_null) == 1 and len(non_null) != len(unique_types):
                schema["type"] = non_null[0]
                schema["nullable"] = True
                _add_issue(issues, "union_to_nullable", _path(path, "type"), SchemaAction.DOWNGRADE)
            else:
                _add_issue(issues, "union_type_unsupported", _path(path, "type"), SchemaAction.EXCLUDE)
                return False
        else:
            schema["type"] = unique_types
    elif schema_type is not None and not isinstance(schema_type, str):
        _add_issue(issues, "schema_type_invalid", _path(path, "type"), SchemaAction.EXCLUDE)
        return False

    for keyword, allowed in (("oneOf", profile.allow_one_of), ("anyOf", profile.allow_any_of)):
        if keyword not in schema:
            continue
        branches = schema.get(keyword)
        if not isinstance(branches, list) or not branches:
            _add_issue(issues, f"{keyword.lower()}_invalid", _path(path, keyword), SchemaAction.EXCLUDE)
            return False
        if not allowed:
            if _merge_single_branch(schema, keyword):
                _add_issue(
                    issues,
                    f"single_{keyword.lower()}_collapsed",
                    _path(path, keyword),
                    SchemaAction.DOWNGRADE,
                )
                return _rewrite_schema(
                    schema,
                    profile=profile,
                    issues=issues,
                    path=path,
                    depth=depth,
                )
            if _merge_nullable_combination(schema, keyword, profile=profile):
                _add_issue(
                    issues,
                    f"nullable_{keyword.lower()}_converted",
                    _path(path, keyword),
                    SchemaAction.DOWNGRADE,
                )
                return _rewrite_schema(
                    schema,
                    profile=profile,
                    issues=issues,
                    path=path,
                    depth=depth,
                )
            _add_issue(issues, f"{keyword.lower()}_unsupported", _path(path, keyword), SchemaAction.EXCLUDE)
            return False
        for index, branch in enumerate(branches):
            if not _rewrite_schema(
                branch,
                profile=profile,
                issues=issues,
                path=_path(_path(path, keyword), index),
                depth=depth + 1,
            ):
                return False

    enum = schema.get("enum")
    if enum is not None:
        if not isinstance(enum, list) or not enum:
            _add_issue(issues, "enum_invalid", _path(path, "enum"), SchemaAction.EXCLUDE)
            return False
        if len(enum) > max(1, int(profile.max_enum_values)):
            _add_issue(issues, "enum_too_large", _path(path, "enum"), SchemaAction.EXCLUDE)
            return False
        kinds = {_enum_value_kind(value) for value in enum}
        complex_enum = "complex" in kinds or len(kinds) > 1
        if complex_enum and not profile.allow_complex_enum:
            _add_issue(issues, "complex_enum_unsupported", _path(path, "enum"), SchemaAction.EXCLUDE)
            return False

    if "additionalProperties" in schema:
        additional = schema.get("additionalProperties")
        additional_path = _path(path, "additionalProperties")
        if isinstance(additional, bool):
            if not profile.allow_boolean_additional_properties:
                if additional is True:
                    schema.pop("additionalProperties", None)
                    _add_issue(
                        issues,
                        "additional_properties_true_removed",
                        additional_path,
                        SchemaAction.DOWNGRADE,
                    )
                else:
                    _add_issue(
                        issues,
                        "additional_properties_false_unsupported",
                        additional_path,
                        SchemaAction.EXCLUDE,
                    )
                    return False
        elif isinstance(additional, dict):
            if not profile.allow_schema_additional_properties:
                _add_issue(
                    issues,
                    "additional_properties_schema_unsupported",
                    additional_path,
                    SchemaAction.EXCLUDE,
                )
                return False
            if not _rewrite_schema(
                additional,
                profile=profile,
                issues=issues,
                path=additional_path,
                depth=depth + 1,
            ):
                return False
        else:
            _add_issue(issues, "additional_properties_invalid", additional_path, SchemaAction.EXCLUDE)
            return False

    mapping_children = ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas")
    for keyword in mapping_children:
        children = schema.get(keyword)
        if children is None:
            continue
        if not isinstance(children, dict):
            _add_issue(issues, f"{keyword.lower()}_invalid", _path(path, keyword), SchemaAction.EXCLUDE)
            return False
        for child_name, child_schema in children.items():
            if not _rewrite_schema(
                child_schema,
                profile=profile,
                issues=issues,
                path=_path(_path(path, keyword), child_name),
                depth=depth + 1,
            ):
                return False

    direct_children = ("items", "contains", "not", "if", "then", "else", "propertyNames")
    for keyword in direct_children:
        child = schema.get(keyword)
        if child is None:
            continue
        if isinstance(child, list) and keyword == "items":
            for index, item in enumerate(child):
                if not _rewrite_schema(
                    item,
                    profile=profile,
                    issues=issues,
                    path=_path(_path(path, keyword), index),
                    depth=depth + 1,
                ):
                    return False
            continue
        if not _rewrite_schema(
            child,
            profile=profile,
            issues=issues,
            path=_path(path, keyword),
            depth=depth + 1,
        ):
            return False

    for keyword in ("allOf", "prefixItems"):
        children = schema.get(keyword)
        if children is None:
            continue
        if not isinstance(children, list):
            _add_issue(issues, f"{keyword.lower()}_invalid", _path(path, keyword), SchemaAction.EXCLUDE)
            return False
        for index, child in enumerate(children):
            if not _rewrite_schema(
                child,
                profile=profile,
                issues=issues,
                path=_path(_path(path, keyword), index),
                depth=depth + 1,
            ):
                return False
    return True


def _prepare_one_tool(
    tool: Any,
    *,
    profile: SchemaCompatibilityProfile,
    index: int,
) -> tuple[dict[str, Any] | None, str, tuple[SchemaIssue, ...]]:
    fallback_name = f"unnamed_tool_{index + 1}"
    name = _tool_name(tool, fallback=fallback_name)
    if not isinstance(tool, Mapping):
        return None, name, (
            SchemaIssue("tool_invalid_shape", "$", SchemaAction.EXCLUDE),
        )
    try:
        prepared = copy.deepcopy(dict(tool))
    except Exception:
        return None, name, (
            SchemaIssue("tool_copy_failed", "$", SchemaAction.EXCLUDE),
        )
    issues: list[SchemaIssue] = []
    function = prepared.get("function")
    description_holder = function if isinstance(function, dict) else prepared
    if not _truncate_description(
        description_holder,
        profile=profile,
        issues=issues,
        path="$.function" if isinstance(function, dict) else "$",
    ):
        return None, name, tuple(issues)
    location = _schema_location(prepared)
    if location is None:
        _add_issue(issues, "tool_schema_missing", "$", SchemaAction.EXCLUDE)
        return None, name, tuple(issues)
    holder, key = location
    schema = holder.get(key)
    if not isinstance(schema, dict):
        _add_issue(issues, "tool_schema_invalid", _path("$", key), SchemaAction.EXCLUDE)
        return None, name, tuple(issues)
    if profile.require_object_root:
        root_type = schema.get("type")
        if root_type is None and not any(term in schema for term in ("oneOf", "anyOf", "allOf")):
            schema["type"] = "object"
            _add_issue(issues, "object_root_added", _path("$", key), SchemaAction.DOWNGRADE)
        elif root_type is not None and root_type != "object":
            _add_issue(issues, "parameters_root_not_object", _path("$", key), SchemaAction.EXCLUDE)
            return None, name, tuple(issues)
    if not _rewrite_schema(
        schema,
        profile=profile,
        issues=issues,
        path=_path("$", key),
        depth=1,
    ):
        return None, name, tuple(issues)
    try:
        _canonical_json(prepared)
    except (TypeError, ValueError, OverflowError, RecursionError):
        _add_issue(issues, "tool_schema_not_json", "$", SchemaAction.EXCLUDE)
        return None, name, tuple(issues)
    return prepared, name, tuple(issues)


def _summary_from_issues(
    name: str,
    issues: Iterable[SchemaIssue],
    *,
    excluded: bool = False,
) -> ToolCompatibilitySummary:
    prefix = ("schema_incompatible",) if excluded else ()
    codes = tuple(dict.fromkeys((*prefix, *(issue.code for issue in issues))))
    return ToolCompatibilitySummary(name=name, reason_codes=codes)


def prepare_tools_for_provider(
    tools: Sequence[dict[str, Any]] | None,
    *,
    provider: Any = "",
    api_type: Any = "",
    model: Any = "",
    route_fingerprint: Any = "",
    profile: SchemaCompatibilityProfile | str | None = None,
    summary_limit: int = 8,
) -> SchemaPreparationResult:
    selected_profile = resolve_schema_profile(
        provider=provider,
        api_type=api_type,
        profile=profile,
    )
    original = list(tools or [])
    prepared: list[dict[str, Any]] = []
    excluded: list[ToolCompatibilitySummary] = []
    downgraded: list[ToolCompatibilitySummary] = []
    for index, tool in enumerate(original):
        compatible, name, issues = _prepare_one_tool(tool, profile=selected_profile, index=index)
        if compatible is None:
            excluded.append(_summary_from_issues(name, issues, excluded=True))
            continue
        prepared.append(compatible)
        downgrade_issues = [issue for issue in issues if issue.action == SchemaAction.DOWNGRADE]
        if downgrade_issues:
            downgraded.append(_summary_from_issues(name, downgrade_issues))

    names = sorted(_tool_name(tool) for tool in prepared)
    names_payload = "\0".join(names)
    names_hash = hashlib.sha256(names_payload.encode("utf-8")).hexdigest()[:12] if names else ""
    schema_json = _canonical_json(prepared)
    schema_hash = hashlib.sha256(schema_json.encode("utf-8")).hexdigest()[:12] if prepared else ""
    limit = max(0, int(summary_limit))
    summary = tuple(names[:limit]) if limit else ()
    diagnostics = ToolSchemaDiagnostics(
        provider=_safe_tool_name(provider, fallback="provider"),
        api_type=_normalize_api_type(api_type)[:48],
        model=_safe_tool_name(model, fallback="")[:120],
        route_fingerprint=_safe_route_fingerprint(route_fingerprint),
        profile=selected_profile.name,
        input_tool_count=len(original),
        tool_count=len(prepared),
        excluded_count=len(excluded),
        schema_chars=len(schema_json),
        schema_bytes=len(schema_json.encode("utf-8")),
        tool_names_summary=summary,
        tool_names_omitted=max(0, len(names) - len(summary)),
        tool_names_hash=names_hash,
        tool_schema_hash=schema_hash,
        excluded_tools=tuple(excluded),
        downgraded_tools=tuple(downgraded),
    )
    return SchemaPreparationResult(tools=tuple(prepared), diagnostics=diagnostics)


def analyze_tool_schema(
    tool: dict[str, Any],
    *,
    profile: SchemaCompatibilityProfile | str = OPENAI_COMPATIBLE_PROFILE,
) -> tuple[SchemaIssue, ...]:
    selected = resolve_schema_profile(profile=profile)
    _, _, issues = _prepare_one_tool(tool, profile=selected, index=0)
    return issues


def _normalize_error_atom(value: Any, *, fallback: str = "") -> str:
    atom = _SAFE_ERROR_ATOM_RE.sub("_", str(value or "").strip().lower()).strip("_.:-")
    return (atom or fallback)[:80]


def _exception_chain(error: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(chain) < 6:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _error_status(error: Any) -> int:
    if isinstance(error, Mapping):
        values = (error.get("status_code"), error.get("status"))
    elif isinstance(error, BaseException):
        values = tuple(
            value
            for item in _exception_chain(error)
            for value in (
                getattr(item, "status_code", None),
                getattr(getattr(item, "response", None), "status_code", None),
            )
        )
    else:
        values = ()
    for value in values:
        try:
            status = int(value or 0)
        except (TypeError, ValueError):
            continue
        if status:
            return max(0, status)
    return 0


def _structured_error_payloads(error: Any) -> list[Mapping[str, Any]]:
    payloads: list[Mapping[str, Any]] = []
    if isinstance(error, Mapping):
        payloads.append(error)
    if not isinstance(error, BaseException):
        return payloads
    for item in _exception_chain(error):
        for value in (getattr(item, "body", None), getattr(item, "error", None)):
            if isinstance(value, Mapping):
                payloads.append(value)
        response = getattr(item, "response", None)
        json_method = getattr(response, "json", None)
        if callable(json_method):
            try:
                value = json_method()
            except Exception:
                value = None
            if isinstance(value, Mapping):
                payloads.append(value)
    return payloads


def _walk_mappings(value: Any, *, limit: int = 64) -> Iterable[Mapping[str, Any]]:
    pending = [value]
    seen = 0
    while pending and seen < limit:
        current = pending.pop(0)
        seen += 1
        if isinstance(current, Mapping):
            yield current
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)


def _render_field_path(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        parts: list[str] = []
        for item in value:
            if isinstance(item, int):
                parts.append(f"[{max(0, item)}]")
            else:
                token = re.sub(r"[^A-Za-z0-9_$-]+", "_", str(item or "")).strip("_")
                if token:
                    parts.append(("." if parts else "") + token[:64])
        return _safe_path("".join(parts)) if parts else ""
    rendered = _safe_path(str(value or "").strip())
    return "" if rendered == "$" and not str(value or "").strip() else rendered


def classify_schema_rejection(error: Any) -> SchemaRejectionDiagnostic:
    status = _error_status(error)
    error_type = ""
    field_path = ""
    for payload in _structured_error_payloads(error):
        for current in _walk_mappings(payload):
            if not error_type:
                for key in ("code", "type", "error_type"):
                    candidate = _normalize_error_atom(current.get(key))
                    if candidate:
                        error_type = candidate
                        break
            if not field_path:
                for key in ("param", "field", "path", "loc", "pointer"):
                    candidate = _render_field_path(current.get(key))
                    if candidate:
                        field_path = candidate
                        break
            if error_type and field_path:
                break
        if error_type and field_path:
            break

    normalized_path = field_path.lower()
    path_is_schema = any(
        marker in normalized_path
        for marker in ("tool", "function", "schema", "parameter", "input_schema")
    )
    if error_type in _SCHEMA_REJECTION_CODES or (status in {400, 422} and path_is_schema):
        reason = "schema_rejected"
    elif status in {400, 422}:
        reason = "provider_request_rejected"
    elif status in {401, 403}:
        reason = "provider_auth_failed"
    elif status == 429:
        reason = "provider_rate_limited"
    elif status >= 500:
        reason = "provider_server_error"
    elif isinstance(error, BaseException) and any(
        "timeout" in type(item).__name__.lower() for item in _exception_chain(error)
    ):
        reason = "provider_timeout"
    else:
        reason = "provider_error_unknown"
    return SchemaRejectionDiagnostic(
        status_code=status,
        error_type=error_type or type(error).__name__[:80],
        reason_code=reason,
        field_path=field_path,
    )


def _normalize_probe_verdict(value: Any) -> ProbeVerdict:
    if isinstance(value, ProbeVerdict):
        return value
    if isinstance(value, bool):
        return ProbeVerdict.ACCEPTED if value else ProbeVerdict.REJECTED
    if isinstance(value, str):
        return ProbeVerdict(value.strip().lower())
    raise ValueError("probe must return bool, ProbeVerdict, or its string value")


async def minimize_declared_schema_rejection(
    tools: Sequence[dict[str, Any]],
    probe: Callable[[list[dict[str, Any]]], Awaitable[ProbeVerdict | str | bool] | ProbeVerdict | str | bool],
    *,
    max_probes: int = 32,
) -> SchemaMinimizationResult:
    """Find a 1-minimal rejection set using declaration-only probes.

    The callback must submit tool declarations without executing any selected
    tool. Unknown/transient outcomes stop minimization instead of being treated
    as evidence that a schema is unsupported.
    """

    candidates = [copy.deepcopy(dict(tool)) for tool in tools]
    probe_limit = max(1, int(max_probes))
    probe_count = 0
    cache: dict[str, ProbeVerdict] = {}

    async def run_probe(subset: list[dict[str, Any]]) -> ProbeVerdict:
        nonlocal probe_count
        payload = _canonical_json(subset)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        if digest in cache:
            return cache[digest]
        if probe_count >= probe_limit:
            return ProbeVerdict.UNKNOWN
        probe_count += 1
        try:
            result = probe(copy.deepcopy(subset))
            if inspect.isawaitable(result):
                result = await result
            verdict = _normalize_probe_verdict(result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            diagnostic = classify_schema_rejection(exc)
            verdict = (
                ProbeVerdict.REJECTED
                if diagnostic.reason_code in {"schema_rejected", "provider_request_rejected"}
                else ProbeVerdict.UNKNOWN
            )
        cache[digest] = verdict
        return verdict

    if not candidates:
        return SchemaMinimizationResult(
            verdict=ProbeVerdict.ACCEPTED,
            minimal_tools=(),
            tool_names=(),
            probe_count=0,
            complete=True,
            diagnostic_code="schema_probe_empty",
        )

    initial = await run_probe(candidates)
    if initial != ProbeVerdict.REJECTED:
        return SchemaMinimizationResult(
            verdict=initial,
            minimal_tools=(),
            tool_names=(),
            probe_count=probe_count,
            complete=initial == ProbeVerdict.ACCEPTED,
            diagnostic_code=(
                "schema_set_accepted" if initial == ProbeVerdict.ACCEPTED else "schema_probe_unknown"
            ),
        )

    granularity = 2
    complete = True
    while len(candidates) >= 2:
        if probe_count >= probe_limit:
            complete = False
            break
        chunk_size = int(math.ceil(len(candidates) / granularity))
        chunks = [candidates[index : index + chunk_size] for index in range(0, len(candidates), chunk_size)]
        reduced = False
        saw_unknown = False

        for chunk in chunks:
            verdict = await run_probe(chunk)
            if verdict == ProbeVerdict.UNKNOWN:
                saw_unknown = True
                continue
            if verdict == ProbeVerdict.REJECTED:
                candidates = chunk
                granularity = 2
                reduced = True
                break
        if reduced:
            continue

        for chunk in chunks:
            chunk_ids = {id(tool) for tool in chunk}
            complement = [tool for tool in candidates if id(tool) not in chunk_ids]
            if not complement:
                continue
            verdict = await run_probe(complement)
            if verdict == ProbeVerdict.UNKNOWN:
                saw_unknown = True
                continue
            if verdict == ProbeVerdict.REJECTED:
                candidates = complement
                granularity = max(2, granularity - 1)
                reduced = True
                break
        if reduced:
            continue
        if saw_unknown:
            complete = False
            break
        if granularity >= len(candidates):
            break
        granularity = min(len(candidates), granularity * 2)

    names = tuple(_tool_name(tool) for tool in candidates)
    return SchemaMinimizationResult(
        verdict=ProbeVerdict.REJECTED,
        minimal_tools=tuple(candidates),
        tool_names=names,
        probe_count=probe_count,
        complete=complete,
        diagnostic_code=(
            "schema_minimal_rejection_found" if complete else "schema_minimization_incomplete"
        ),
    )


def build_schema_feature_probe_tools() -> list[dict[str, Any]]:
    """Return harmless declarations for administrator-triggered compatibility probes."""

    features: list[tuple[str, dict[str, Any]]] = [
        ("one_of", {"oneOf": [{"type": "string"}, {"type": "integer"}]}),
        ("any_of", {"anyOf": [{"type": "string"}, {"type": "integer"}]}),
        ("nullable", {"type": "string", "nullable": True}),
        ("union_type", {"type": ["string", "null"]}),
        ("default", {"type": "string", "default": "probe"}),
        ("complex_enum", {"enum": [{"kind": "a"}, {"kind": "b"}]}),
        ("additional_properties", {"type": "object", "additionalProperties": {"type": "string"}}),
    ]
    tools: list[dict[str, Any]] = []
    for name, value_schema in features:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": f"schema_probe_{name}",
                    "description": "只声明、不执行的工具 Schema 兼容探针。",
                    "parameters": {
                        "type": "object",
                        "properties": {"value": value_schema},
                        "required": [],
                    },
                },
            }
        )
    return tools


__all__ = [
    "ANTHROPIC_PROFILE",
    "BUILTIN_PROFILES",
    "GEMINI_PROFILE",
    "JSON_SCHEMA_PROFILE",
    "OPENAI_COMPATIBLE_PROFILE",
    "OPENAI_PROFILE",
    "ProbeVerdict",
    "SchemaAction",
    "SchemaCompatibilityProfile",
    "SchemaIssue",
    "SchemaMinimizationResult",
    "SchemaPreparationResult",
    "SchemaRejectionDiagnostic",
    "ToolCompatibilitySummary",
    "ToolSchemaDiagnostics",
    "analyze_tool_schema",
    "build_schema_feature_probe_tools",
    "classify_schema_rejection",
    "minimize_declared_schema_rejection",
    "prepare_tools_for_provider",
    "resolve_schema_profile",
]
