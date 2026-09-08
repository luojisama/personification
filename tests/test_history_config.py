from __future__ import annotations

from types import SimpleNamespace
import json

from ._loader import load_personification_module


history_config = load_personification_module("plugin.personification.core.history_config")
config_manager = load_personification_module("plugin.personification.core.config_manager")
config_module = load_personification_module("plugin.personification.config")


def _config(**values):
    return SimpleNamespace(**values)


def test_legacy_explicit_limit_narrows_new_default_only_without_new_override() -> None:
    config = _config(
        personification_private_history_max_messages=4000,
        personification_private_history_turns=60,
        __pydantic_fields_set__={"personification_private_history_turns"},
    )
    assert history_config.effective_history_message_limit(config, private=True) == (60, True)

    config.__pydantic_fields_set__.add("personification_private_history_max_messages")
    assert history_config.effective_history_message_limit(config, private=True) == (4000, False)


def test_persisted_snapshot_is_not_mistaken_for_explicit_legacy_override() -> None:
    config = _config(
        personification_group_history_max_messages=12000,
        personification_history_len=320,
        __pydantic_fields_set__=set(),
        _personification_env_config_info={"applied_fields": ["personification_history_len"], "imported_fields": []},
    )
    assert history_config.effective_history_message_limit(config, private=False) == (12000, False)


def test_real_pydantic_load_uses_preload_provenance_not_setattr_fields(tmp_path, monkeypatch) -> None:
    """A full legacy env snapshot mutates fields_set, but is not user intent."""
    path = tmp_path / "env.json"
    path.write_text(json.dumps({
        "personification_private_history_turns": 60,
        "personification_private_history_max_messages": 4000,
    }), encoding="utf-8")
    cfg = config_module.Config()
    monkeypatch.setattr(config_manager, "get_env_config_path", lambda _config: path)
    config_manager.ConfigManager(plugin_config=cfg, logger=None).load()
    assert "personification_private_history_turns" in cfg.__pydantic_fields_set__
    # Test startup can legitimately inject unrelated environment settings. The
    # old field itself must not be invented by ConfigManager setattr.
    assert "personification_private_history_turns" not in history_config.explicit_config_fields(cfg)
    # No durable provenance is an explicit compatibility uncertainty: retain
    # the old cap rather than silently expanding the existing snapshot.
    assert history_config.effective_history_message_limit(cfg, private=True) == (60, True)


def test_explicit_legacy_expiry_zero_remains_unlimited_until_new_days_selected() -> None:
    cfg = _config(
        personification_private_history_days=14,
        personification_message_expire_hours=0,
        __pydantic_fields_set__={"personification_message_expire_hours"},
    )
    assert history_config.effective_history_days(cfg, private=True) == (None, True)
    cfg.__pydantic_fields_set__.add("personification_private_history_days")
    assert history_config.effective_history_days(cfg, private=True) == (14, False)
