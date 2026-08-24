from __future__ import annotations

from types import SimpleNamespace

from ._loader import load_personification_module


emotion_state = load_personification_module("plugin.personification.core.emotion_state")


def _v2_state() -> dict:
    return {
        "global": {
            "category": "好奇",
            "vad": {"valence": 0.2, "arousal": 0.7, "dominance": 0.1},
            "confidence": 0.8,
            "action_tendency": "approach",
        },
        "per_user": {
            "u1": {
                "category": "期待",
                "vad": {"valence": 0.5, "arousal": 0.6, "dominance": 0.2},
                "confidence": 0.7,
                "action_tendency": "support",
            }
        },
        "per_group": {},
    }


def test_emotion_v2_mode_is_fail_closed() -> None:
    assert emotion_state.emotion_v2_mode(SimpleNamespace()) == "off"
    assert (
        emotion_state.emotion_v2_mode(
            SimpleNamespace(personification_emotion_v2_mode="shadow")
        )
        == "shadow"
    )
    assert (
        emotion_state.emotion_v2_mode(
            SimpleNamespace(personification_emotion_v2_mode="unexpected")
        )
        == "off"
    )


def test_shadow_does_not_change_prompt_but_on_uses_same_shared_renderer() -> None:
    base = {
        "per_user": {
            "u1": {
                "bot_emotion": "平静",
                "expression_style": "自然",
            }
        },
        "per_group": {},
        "_v2": _v2_state(),
    }
    shadow = emotion_state.render_emotion_memory_hint(
        {**base, "_v2_mode": "shadow"},
        user_id="u1",
    )
    enabled = emotion_state.render_emotion_memory_hint(
        {**base, "_v2_mode": "on"},
        user_id="u1",
    )

    assert "情绪状态 v2" not in shadow
    assert "情绪状态 v2" in enabled
    assert "全局=好奇" in enabled
    assert "当前用户=期待" in enabled
    assert "不得越过权限、概率或发送边界" in enabled
