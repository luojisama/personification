from __future__ import annotations

import warnings

from ._loader import load_personification_module

config_mod = load_personification_module("plugin.personification.config")


def test_config_defaults_do_not_emit_deprecated_web_search_warning() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        config_mod.Config()

    assert not [
        item
        for item in caught
        if issubclass(item.category, DeprecationWarning)
        and "personification_web_search" in str(item.message)
    ]


def test_qzone_cookie_deprecated_alias_copies_to_prefixed_field() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cfg = config_mod.Config(qzone_cookie="uin=o123; skey=abc")

    assert cfg.personification_qzone_cookie == "uin=o123; skey=abc"
    assert any(
        issubclass(item.category, DeprecationWarning)
        and "qzone_cookie" in str(item.message)
        for item in caught
    )


def test_llm_review_features_default_disabled() -> None:
    cfg = config_mod.Config()

    assert cfg.personification_response_review_enabled is False
    assert cfg.personification_tts_llm_decision_enabled is False
    assert cfg.personification_response_review_model_role == "review"
    assert cfg.personification_tts_llm_decision_model_role == "agent"


def test_qzone_social_and_frequency_defaults() -> None:
    cfg = config_mod.Config()

    assert cfg.personification_qzone_check_interval == 90
    assert cfg.personification_qzone_daily_limit == 3
    assert cfg.personification_qzone_min_interval_hours == 6.0
    assert cfg.personification_qzone_social_enabled is True
    assert cfg.personification_qzone_social_check_interval == 60
    assert cfg.personification_qzone_social_scope == "recent_interactions"
    assert cfg.personification_qzone_social_like_limit == 0
    assert cfg.personification_qzone_social_comment_limit == 0
    assert cfg.personification_qzone_social_max_feeds_per_scan == 5
    assert cfg.personification_proactive_require_user_profile is True
