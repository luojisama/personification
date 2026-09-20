from __future__ import annotations

from types import SimpleNamespace

from ._loader import load_personification_module


catalog = load_personification_module("plugin.personification.core.provider_catalog")
router = load_personification_module("plugin.personification.core.provider_router")
ai_routes = load_personification_module("plugin.personification.core.ai_routes")
config_routes = load_personification_module("plugin.personification.webui.routes.config_routes")


def _pool() -> dict:
    return {
        "provider_id": "gateway-a",
        "name": "gateway",
        "api_type": "openai",
        "api_url": "https://api.example/v1",
        "api_key": "secret",
        "model": "main-model",
        "models": [
            {"model_id": "main-model", "context_window_tokens": 1050000},
            {"model_id": "fast-model", "max_input_tokens": 272000},
        ],
        "default_model_id": "main-model",
    }


def test_disabled_default_does_not_automatically_select_another_model():
    pool = _pool()
    pool["models"][0]["enabled"] = False
    assert catalog.expand_catalog_pools([pool]) == []
    pool["default_model_id"] = "fast-model"
    assert catalog.expand_catalog_pools([pool])[0]["model"] == "fast-model"


def _cfg(pools, bindings=None, strict=False):  # noqa: ANN001
    return SimpleNamespace(
        personification_api_pools=pools,
        personification_model_purpose_bindings=bindings or {},
        personification_strict_main_model=strict,
        personification_api_type="openai",
        personification_api_url="",
        personification_api_key="",
        personification_model="",
        personification_context_budget_enabled=True,
        personification_context_input_ratio=0.5,
        personification_context_safety_margin_ratio=0.05,
    )


def test_legacy_pool_gets_durable_connection_and_model_entry() -> None:
    normalized = catalog.normalize_catalog_pools([_pool()])[0]
    assert normalized["provider_id"] == "gateway-a"
    assert normalized["models"][0]["model_id"] == "main-model"
    assert normalized["default_model_id"] == "main-model"


def test_global_purpose_binding_selects_exact_provider_and_model() -> None:
    cfg = _cfg([_pool()], {"lite": {"provider_id": "gateway-a", "model_id": "fast-model"}})
    providers = router.get_configured_api_providers(cfg, None, purpose="lite")
    assert len(providers) == 1
    assert providers[0]["provider_id"] == "gateway-a"
    assert providers[0]["model"] == "fast-model"
    assert providers[0]["max_input_tokens"] == 272000


def test_invalid_explicit_binding_fails_closed() -> None:
    cfg = _cfg([_pool()], {"lite": {"provider_id": "gateway-a", "model_id": "gone"}})
    cfg.personification_api_url = "https://legacy.example/v1"
    cfg.personification_api_key = "legacy-secret"
    cfg.personification_model = "legacy-model"
    assert router.get_configured_api_providers(cfg, None, purpose="lite") == []


def test_strict_main_only_redirects_lite_not_persona() -> None:
    cfg = _cfg(
        [_pool()],
        {
            "lite": {"provider_id": "gateway-a", "model_id": "fast-model"},
            "persona": {"provider_id": "gateway-a", "model_id": "fast-model"},
        },
        strict=True,
    )
    assert router.resolve_purpose_provider(cfg, "lite", None)["model"] == "main-model"
    assert router.resolve_purpose_provider(cfg, "persona", None)["model"] == "fast-model"


def test_same_model_name_on_two_suppliers_remains_distinct() -> None:
    second = _pool() | {"provider_id": "gateway-b", "name": "gateway-b"}
    cfg = _cfg([_pool(), second], {"vision": {"provider_id": "gateway-b", "model_id": "main-model"}})
    resolved = router.resolve_purpose_provider(cfg, "vision", None)
    assert resolved is not None and resolved["provider_id"] == "gateway-b"


def test_masked_secret_reference_survives_provider_reordering() -> None:
    first = _pool()
    second = _pool() | {"provider_id": "gateway-b", "name": "gateway-b", "api_key": "other-secret"}
    assert config_routes._provider_secret_ref(first, 0) == config_routes._provider_secret_ref(first, 9)
    cfg = SimpleNamespace(personification_api_pools=[first, second])
    rendered = config_routes._mask_api_pool_config([first, second])
    restored = config_routes._restore_masked_config_secrets(
        "personification_api_pools", list(reversed(rendered)), cfg
    )
    assert restored[0]["api_key"] == "other-secret"
    assert restored[1]["api_key"] == "secret"


def test_catalog_rejects_removing_globally_bound_model() -> None:
    cfg = _cfg([_pool()], {"lite": {"provider_id": "gateway-a", "model_id": "fast-model"}})
    deleted = _pool() | {"models": [{"model_id": "main-model"}]}
    try:
        config_routes._validate_catalog_model_references(
            [deleted], cfg.personification_model_purpose_bindings
        )
    except ValueError as exc:
        assert "global purpose lite" in str(exc)
    else:
        raise AssertionError("removing a selected model must be rejected")


def test_fallback_vision_caller_uses_exact_catalog_vision_pair(monkeypatch) -> None:  # noqa: ANN001
    cfg = _cfg([_pool()], {"vision": {"provider_id": "gateway-a", "model_id": "fast-model"}})
    captured: dict = {}

    def _fake_build(config):  # noqa: ANN001
        captured["type"] = config.personification_labeler_api_type
        captured["url"] = config.personification_labeler_api_url
        captured["model"] = config.personification_labeler_model
        return "caller"

    monkeypatch.setattr(ai_routes, "_build_vision_caller", _fake_build)
    assert ai_routes.build_fallback_vision_caller(cfg, purpose="vision") == "caller"
    assert captured == {"type": "openai", "url": "https://api.example/v1", "model": "fast-model"}


def test_invalid_catalog_vision_pair_never_uses_global_fallback(monkeypatch) -> None:  # noqa: ANN001
    cfg = _cfg([_pool()], {"vision": {"provider_id": "gateway-a", "model_id": "gone"}})
    monkeypatch.setattr(ai_routes, "resolve_global_fallback_provider", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not fall back")))
    assert ai_routes.build_fallback_vision_caller(cfg, purpose="vision") is None
