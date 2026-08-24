from __future__ import annotations

import asyncio
import json

import pytest

from ._loader import load_personification_module


compat = load_personification_module("plugin.personification.core.tool_schema_compat")


def _tool(name: str, value_schema: dict | None = None, *, description: str = "测试工具") -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {"value": value_schema or {"type": "string"}},
                "required": [],
            },
        },
    }


def _value_schema(tool: dict) -> dict:
    return tool["function"]["parameters"]["properties"]["value"]


def test_profile_resolution_distinguishes_official_and_compatible_routes() -> None:
    assert compat.resolve_schema_profile(provider="openai", api_type="openai").name == "openai"
    assert compat.resolve_schema_profile(provider="anthropic", api_type="anthropic").name == "anthropic"
    assert compat.resolve_schema_profile(provider="google", api_type="gemini").name == "gemini"
    assert compat.resolve_schema_profile(provider="grok-gateway", api_type="openai").name == "openai_compatible"


def test_prepare_tools_records_safe_stable_request_diagnostics() -> None:
    tools = [_tool("weather"), _tool("wiki_lookup")]

    first = compat.prepare_tools_for_provider(
        tools,
        provider="gateway",
        api_type="openai",
        model="model-a",
        route_fingerprint="https://secret.example/v1?key=private",
    )
    reordered_dict = json.loads(json.dumps(tools, ensure_ascii=False))
    second = compat.prepare_tools_for_provider(
        reordered_dict,
        provider="gateway",
        api_type="openai",
        model="model-a",
        route_fingerprint="https://secret.example/v1?key=private",
    )

    diagnostic = first.diagnostics
    assert diagnostic.input_tool_count == 2
    assert diagnostic.tool_count == 2
    assert diagnostic.excluded_count == 0
    assert diagnostic.schema_chars > 0
    assert diagnostic.schema_bytes >= diagnostic.schema_chars
    assert diagnostic.tool_names_summary == ("weather", "wiki_lookup")
    assert len(diagnostic.tool_names_hash) == 12
    assert len(diagnostic.tool_schema_hash) == 12
    assert diagnostic.tool_schema_hash == second.diagnostics.tool_schema_hash
    serialized = json.dumps(diagnostic.to_safe_dict(), ensure_ascii=False)
    assert "secret.example" not in serialized
    assert "private" not in serialized


def test_diagnostics_never_copy_description_defaults_or_enum_values() -> None:
    tool = _tool(
        "lookup",
        {
            "type": "string",
            "description": "PRIVATE_DESCRIPTION_VALUE",
            "default": "PRIVATE_DEFAULT_VALUE",
            "enum": ["PRIVATE_ENUM_VALUE"],
        },
    )

    result = compat.prepare_tools_for_provider(
        [tool],
        provider="third-party",
        api_type="openai",
    )

    diagnostic = json.dumps(result.diagnostics.to_safe_dict(), ensure_ascii=False)
    assert "PRIVATE_DESCRIPTION_VALUE" not in diagnostic
    assert "PRIVATE_DEFAULT_VALUE" not in diagnostic
    assert "PRIVATE_ENUM_VALUE" not in diagnostic


def test_safe_mechanical_downgrades_remove_default_and_truncate_description() -> None:
    profile = compat.SchemaCompatibilityProfile(
        name="short",
        allow_default=False,
        max_description_chars=16,
    )
    original = _tool(
        "lookup",
        {"type": "string", "default": "x", "description": "a" * 100},
        description="b" * 100,
    )

    result = compat.prepare_tools_for_provider([original], profile=profile)

    prepared = result.tools[0]
    assert "default" not in _value_schema(prepared)
    assert len(_value_schema(prepared)["description"]) == 16
    assert len(prepared["function"]["description"]) == 16
    assert set(result.diagnostics.downgraded_tools[0].reason_codes) >= {
        "description_truncated",
        "default_removed",
    }
    assert _value_schema(original)["default"] == "x"
    assert len(original["function"]["description"]) == 100


def test_unsupported_combination_excludes_tool_instead_of_changing_meaning() -> None:
    tool = _tool(
        "ambiguous",
        {"oneOf": [{"type": "string"}, {"type": "integer"}]},
    )

    result = compat.prepare_tools_for_provider(
        [tool],
        profile=compat.OPENAI_COMPATIBLE_PROFILE,
    )

    assert result.tools == ()
    assert result.diagnostics.excluded_count == 1
    assert "schema_incompatible" in result.diagnostics.excluded_tools[0].reason_codes
    assert "oneof_unsupported" in result.diagnostics.excluded_tools[0].reason_codes


def test_single_combination_branch_is_collapsed_without_semantic_loss() -> None:
    tool = _tool("single", {"oneOf": [{"type": "string", "minLength": 2}]})

    result = compat.prepare_tools_for_provider(
        [tool],
        profile=compat.OPENAI_COMPATIBLE_PROFILE,
    )

    assert _value_schema(result.tools[0]) == {"type": "string", "minLength": 2}
    assert "single_oneof_collapsed" in result.diagnostics.downgraded_tools[0].reason_codes


def test_nullable_anyof_can_be_converted_for_profile_that_supports_nullable() -> None:
    tool = _tool(
        "nullable_value",
        {"anyOf": [{"type": "string"}, {"type": "null"}]},
    )

    result = compat.prepare_tools_for_provider([tool], profile=compat.GEMINI_PROFILE)

    assert _value_schema(result.tools[0]) == {"type": "string", "nullable": True}
    assert "nullable_anyof_converted" in result.diagnostics.downgraded_tools[0].reason_codes


def test_nullable_keyword_converts_to_standard_union_when_supported() -> None:
    tool = _tool("nullable_value", {"type": "string", "nullable": True})

    result = compat.prepare_tools_for_provider([tool], profile=compat.OPENAI_PROFILE)

    assert _value_schema(result.tools[0]) == {"type": ["string", "null"]}
    assert "nullable_to_union" in result.diagnostics.downgraded_tools[0].reason_codes


def test_union_is_excluded_when_no_lossless_representation_is_available() -> None:
    tool = _tool("union", {"type": ["string", "integer"]})

    result = compat.prepare_tools_for_provider(
        [tool],
        profile=compat.OPENAI_COMPATIBLE_PROFILE,
    )

    assert result.tools == ()
    assert "union_type_unsupported" in result.diagnostics.excluded_tools[0].reason_codes


def test_schema_valued_additional_properties_is_excluded_for_limited_profile() -> None:
    tool = _tool(
        "mapping",
        {"type": "object", "additionalProperties": {"type": "string"}},
    )

    result = compat.prepare_tools_for_provider([tool], profile=compat.GEMINI_PROFILE)

    assert result.tools == ()
    assert "additional_properties_schema_unsupported" in result.diagnostics.excluded_tools[0].reason_codes


def test_redundant_additional_properties_true_can_be_removed_losslessly() -> None:
    profile = compat.SchemaCompatibilityProfile(
        name="no_additional_keyword",
        allow_boolean_additional_properties=False,
    )
    tool = _tool("mapping", {"type": "object", "additionalProperties": True})

    result = compat.prepare_tools_for_provider([tool], profile=profile)

    assert "additionalProperties" not in _value_schema(result.tools[0])
    assert "additional_properties_true_removed" in result.diagnostics.downgraded_tools[0].reason_codes


def test_additional_properties_false_is_not_silently_removed() -> None:
    profile = compat.SchemaCompatibilityProfile(
        name="no_additional_keyword",
        allow_boolean_additional_properties=False,
    )
    tool = _tool("closed", {"type": "object", "additionalProperties": False})

    result = compat.prepare_tools_for_provider([tool], profile=profile)

    assert result.tools == ()
    assert "additional_properties_false_unsupported" in result.diagnostics.excluded_tools[0].reason_codes


def test_complex_enum_and_excessive_depth_are_stable_exclusion_reasons() -> None:
    enum_result = compat.prepare_tools_for_provider(
        [_tool("enum", {"enum": [{"kind": "a"}, {"kind": "b"}]})],
        profile=compat.OPENAI_COMPATIBLE_PROFILE,
    )
    shallow_profile = compat.SchemaCompatibilityProfile(name="shallow", max_schema_depth=2)
    deep_schema = {
        "type": "object",
        "properties": {
            "nested": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
            }
        },
    }
    depth_result = compat.prepare_tools_for_provider(
        [_tool("deep", deep_schema)],
        profile=shallow_profile,
    )

    assert "complex_enum_unsupported" in enum_result.diagnostics.excluded_tools[0].reason_codes
    assert "schema_depth_exceeded" in depth_result.diagnostics.excluded_tools[0].reason_codes


def test_error_diagnostic_extracts_only_safe_type_and_field_path() -> None:
    payload = {
        "status_code": 400,
        "error": {
            "type": "invalid_request_error",
            "param": ["tools", 3, "function", "parameters", "properties", "query"],
            "message": "PRIVATE_UPSTREAM_MESSAGE sk-private-key",
        },
    }

    diagnostic = compat.classify_schema_rejection(payload)

    assert diagnostic.status_code == 400
    assert diagnostic.reason_code == "schema_rejected"
    assert diagnostic.field_path == "tools[3].function.parameters.properties.query"
    serialized = json.dumps(diagnostic.to_safe_dict(), ensure_ascii=False)
    assert "PRIVATE_UPSTREAM_MESSAGE" not in serialized
    assert "sk-private-key" not in serialized


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({"status_code": 400, "error": {"code": "invalid_tool_schema"}}, "schema_rejected"),
        ({"status_code": 422, "error": {"type": "invalid_request_error"}}, "provider_request_rejected"),
        ({"status_code": 429}, "provider_rate_limited"),
        ({"status_code": 503}, "provider_server_error"),
    ],
)
def test_error_classification_uses_stable_codes(payload: dict, reason: str) -> None:
    assert compat.classify_schema_rejection(payload).reason_code == reason


def test_declaration_only_minimizer_finds_single_rejected_tool() -> None:
    tools = [_tool("good_a"), _tool("bad_schema"), _tool("good_b")]
    calls: list[tuple[str, ...]] = []

    async def probe(declarations: list[dict]) -> bool:
        names = tuple(_tool_name(item) for item in declarations)
        calls.append(names)
        return "bad_schema" not in names

    def _tool_name(item: dict) -> str:
        return item["function"]["name"]

    result = asyncio.run(compat.minimize_declared_schema_rejection(tools, probe))

    assert result.verdict == compat.ProbeVerdict.REJECTED
    assert result.tool_names == ("bad_schema",)
    assert result.complete is True
    assert result.probe_count == len(calls)
    assert result.diagnostic_code == "schema_minimal_rejection_found"


def test_minimizer_preserves_interaction_rejection_set() -> None:
    tools = [_tool("interaction_a"), _tool("interaction_b"), _tool("good")]

    async def probe(declarations: list[dict]) -> compat.ProbeVerdict:
        names = {item["function"]["name"] for item in declarations}
        return (
            compat.ProbeVerdict.REJECTED
            if {"interaction_a", "interaction_b"}.issubset(names)
            else compat.ProbeVerdict.ACCEPTED
        )

    result = asyncio.run(compat.minimize_declared_schema_rejection(tools, probe))

    assert set(result.tool_names) == {"interaction_a", "interaction_b"}
    assert result.complete is True


def test_minimizer_stops_on_unknown_subprobes() -> None:
    tools = [_tool("a"), _tool("b")]
    calls = 0

    async def probe(declarations: list[dict]) -> compat.ProbeVerdict:
        nonlocal calls
        calls += 1
        return compat.ProbeVerdict.REJECTED if len(declarations) == 2 else compat.ProbeVerdict.UNKNOWN

    result = asyncio.run(compat.minimize_declared_schema_rejection(tools, probe))

    assert result.verdict == compat.ProbeVerdict.REJECTED
    assert result.complete is False
    assert result.diagnostic_code == "schema_minimization_incomplete"
    assert calls >= 2


def test_transient_probe_exception_is_unknown_not_schema_rejection() -> None:
    class GatewayError(RuntimeError):
        status_code = 503

    async def probe(_declarations: list[dict]) -> bool:
        raise GatewayError("PRIVATE_GATEWAY_BODY")

    result = asyncio.run(
        compat.minimize_declared_schema_rejection([_tool("a")], probe)
    )

    assert result.verdict == compat.ProbeVerdict.UNKNOWN
    assert result.minimal_tools == ()
    assert result.diagnostic_code == "schema_probe_unknown"


def test_synthetic_feature_probes_are_declarations_without_execution_payloads() -> None:
    tools = compat.build_schema_feature_probe_tools()

    assert len(tools) == 7
    assert all(item["type"] == "function" for item in tools)
    assert all(item["function"]["name"].startswith("schema_probe_") for item in tools)
    assert all("handler" not in item and "arguments" not in item for item in tools)
