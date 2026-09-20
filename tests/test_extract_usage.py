from __future__ import annotations

from types import SimpleNamespace

from ._loader import load_personification_module


impl = load_personification_module(
    "plugin.personification.skills.skillpacks.tool_caller.scripts.impl"
)


def test_openai_chat_completion_dict() -> None:
    response = {
        "usage": {"prompt_tokens": 120, "completion_tokens": 50, "total_tokens": 170}
    }
    assert impl._extract_usage(response) == {
        "prompt_tokens": 120,
        "completion_tokens": 50,
        "total_tokens": 170,
    }


def test_openai_pydantic_object_attrs() -> None:
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    response = SimpleNamespace(usage=usage)
    assert impl._extract_usage(response)["total_tokens"] == 15


def test_anthropic_input_output_tokens() -> None:
    response = {"usage": {"input_tokens": 200, "output_tokens": 80}}
    out = impl._extract_usage(response)
    assert out["prompt_tokens"] == 200
    assert out["completion_tokens"] == 80
    # total 自动求和
    assert out["total_tokens"] == 280


def test_gemini_usage_metadata_camelcase() -> None:
    response = {
        "usageMetadata": {
            "promptTokenCount": 333,
            "candidatesTokenCount": 111,
            "totalTokenCount": 444,
        }
    }
    out = impl._extract_usage(response)
    assert out["prompt_tokens"] == 333
    assert out["completion_tokens"] == 111
    assert out["total_tokens"] == 444


def test_gemini_cli_nested_inner_response() -> None:
    inner = {
        "usageMetadata": {
            "promptTokenCount": 50,
            "candidatesTokenCount": 30,
            "totalTokenCount": 80,
        }
    }
    # 模拟 Gemini CLI 的 generateContent 包装：data["response"] = inner
    assert impl._extract_usage(inner)["total_tokens"] == 80


def test_missing_usage_returns_empty() -> None:
    assert impl._extract_usage({}) == {}
    assert impl._extract_usage({"some": "other"}) == {}
    assert impl._extract_usage(None) == {}


def test_zero_usage_treated_as_empty() -> None:
    # 全 0 视为无效 usage（避免向 token_ledger 灌空数据）
    assert impl._extract_usage({"usage": {"prompt_tokens": 0, "completion_tokens": 0}}) == {}


def test_partial_usage_inferred_total() -> None:
    # 只有 prompt + completion 没 total，应当自动算
    out = impl._extract_usage({"usage": {"prompt_tokens": 7, "completion_tokens": 3}})
    assert out["total_tokens"] == 10


def test_corrupted_usage_value_falls_back_to_zero() -> None:
    out = impl._extract_usage({"usage": {"prompt_tokens": "abc", "completion_tokens": 5}})
    # "abc" 解析失败应当返 0；5 仍读到 → 但 prompt=0 完成度不够 → 实际 total=5 不为 0，所以返回 dict
    assert out["completion_tokens"] == 5
    assert out["prompt_tokens"] == 0
    assert out["usage_complete"] is False


def test_usage_metadata_snake_case_variant() -> None:
    # Vertex AI 某些响应用 usage_metadata 而非 usageMetadata
    response = {
        "usage_metadata": {
            "promptTokenCount": 11,
            "candidatesTokenCount": 22,
            "totalTokenCount": 33,
        }
    }
    out = impl._extract_usage(response)
    assert out == {"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33}


def test_openai_cache_usage_preserves_explicit_zero() -> None:
    response = {
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 5,
            "total_tokens": 125,
            "prompt_tokens_details": {"cached_tokens": 0},
        }
    }
    out = impl._extract_usage(response)
    assert out["cache_provider"] == "openai"
    assert out["cache_read_input_tokens"] == 0
    assert out["total_tokens"] == 125


def test_openai_responses_cache_usage_object_shape() -> None:
    usage = SimpleNamespace(
        input_tokens=80,
        output_tokens=7,
        total_tokens=87,
        input_tokens_details=SimpleNamespace(cached_tokens=64),
    )
    out = impl._extract_usage(SimpleNamespace(usage=usage))
    assert out["cache_provider"] == "openai"
    assert out["cache_read_input_tokens"] == 64


def test_anthropic_cache_usage_keeps_read_and_creation_separate() -> None:
    out = impl._extract_usage(
        {
            "usage": {
                "input_tokens": 20,
                "output_tokens": 4,
                "cache_read_input_tokens": 100,
                "cache_creation_input_tokens": 50,
            }
        }
    )
    assert out["cache_provider"] == "anthropic"
    assert out["cache_read_input_tokens"] == 100
    assert out["cache_creation_input_tokens"] == 50
    assert out["total_tokens"] == 24


def test_gemini_cache_usage_supports_sdk_snake_case() -> None:
    usage = SimpleNamespace(
        promptTokenCount=90,
        candidatesTokenCount=10,
        totalTokenCount=100,
        cached_content_token_count=72,
    )
    out = impl._extract_usage(SimpleNamespace(usage_metadata=usage))
    assert out["cache_provider"] == "gemini"
    assert out["cache_read_input_tokens"] == 72


def test_missing_cache_usage_stays_unknown_instead_of_zero() -> None:
    out = impl._extract_usage(
        {"usage": {"prompt_tokens": 9, "completion_tokens": 1, "total_tokens": 10}}
    )
    assert "cache_provider" not in out
    assert "cache_read_input_tokens" not in out


def test_cache_usage_rejects_bool_float_negative_and_malformed_values() -> None:
    for value in (True, 1.5, -1, "-1", "1.5", "bad"):
        out = impl._extract_usage(
            {
                "usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 1,
                    "prompt_tokens_details": {"cached_tokens": value},
                }
            }
        )
        assert "cache_provider" not in out
        assert "cache_read_input_tokens" not in out


def test_cache_only_explicit_usage_is_not_discarded() -> None:
    out = impl._extract_usage(
        {
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_input_tokens": 32,
            }
        }
    )
    assert out == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cache_provider": "anthropic",
        "cache_read_input_tokens": 32,
    }


def test_anthropic_cache_creation_duration_is_preserved() -> None:
    result = impl._extract_usage({"usage": {
        "input_tokens": 10, "output_tokens": 2,
        "cache_read_input_tokens": 100, "cache_creation_input_tokens": 80,
        "cache_creation": {"ephemeral_5m_input_tokens": 30, "ephemeral_1h_input_tokens": 50},
    }})
    assert result["cache_creation_input_tokens"] == 80
    assert result["cache_creation_5m_input_tokens"] == 30
    assert result["cache_creation_1h_input_tokens"] == 50


def test_response_usage_identity_is_unique_and_not_route_affinity() -> None:
    first = impl.ToolCallerResponse("stop", "", [], None)
    second = impl.ToolCallerResponse("stop", "", [], None)
    assert first.usage_event_id != second.usage_event_id
    assert first.usage_route_id == second.usage_route_id == ""
