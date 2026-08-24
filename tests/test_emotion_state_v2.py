from __future__ import annotations

import asyncio
import copy
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from ._loader import load_personification_module


data_store = load_personification_module("plugin.personification.core.data_store")
emotion_v2 = load_personification_module("plugin.personification.core.emotion_state_v2")


UTC = timezone.utc
NOW = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _record(
    *,
    updated_at: datetime = NOW,
    valence: float = 0.0,
    arousal: float = 0.5,
    dominance: float = 0.0,
    confidence: float = 0.0,
    category: str = "平静",
    action_tendency: str = "observe",
) -> dict[str, Any]:
    return {
        "vad": {
            "valence": valence,
            "arousal": arousal,
            "dominance": dominance,
        },
        "category": category,
        "confidence": confidence,
        "appraisal": {
            "reason": "",
            "goal": "",
            "certainty": "",
            "controllability": "",
        },
        "action_tendency": action_tendency,
        "updated_at": _timestamp(updated_at),
    }


class MemoryStore:
    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self.data = copy.deepcopy(initial or {})
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def load(self, name: str) -> Any:
        async with self._locks[name]:
            return copy.deepcopy(self.data.get(name, {}))

    async def mutate(self, name: str, mutator) -> Any:  # noqa: ANN001
        async with self._locks[name]:
            current = copy.deepcopy(self.data.get(name, {}))
            updated = mutator(current)
            if updated is None:
                updated = current
            self.data[name] = copy.deepcopy(updated)
            return copy.deepcopy(updated)


class FailingLegacyStore(MemoryStore):
    async def mutate(self, name: str, mutator) -> Any:  # noqa: ANN001
        if name == emotion_v2.V1_EMOTION_STORE_NAME:
            raise RuntimeError("legacy write unavailable")
        return await super().mutate(name, mutator)


def test_constants_match_the_v2_contract() -> None:
    assert emotion_v2.SCHEMA_VERSION == 2
    assert emotion_v2.GLOBAL_HALF_LIFE_HOURS == 6.0
    assert emotion_v2.RELATION_HALF_LIFE_HOURS == 48.0
    assert emotion_v2.ENTRY_TTL_DAYS == 30
    assert emotion_v2.EMOTION_CATEGORIES == (
        "平静",
        "开心",
        "疲惫",
        "困倦",
        "烦躁",
        "低落",
        "期待",
        "紧张",
        "放松",
        "无语",
        "好奇",
    )
    assert emotion_v2.ACTION_TENDENCIES == (
        "approach",
        "avoid",
        "support",
        "observe",
    )


def test_patch_json_schema_exposes_exact_ranges_and_returns_a_copy() -> None:
    schema = emotion_v2.EmotionPatch.json_schema()

    assert schema["additionalProperties"] is False
    assert schema["properties"]["scope"]["enum"] == ["global", "user", "group"]
    assert schema["properties"]["vad"]["properties"] == {
        "valence": {"type": "number", "minimum": -1.0, "maximum": 1.0},
        "arousal": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "dominance": {"type": "number", "minimum": -1.0, "maximum": 1.0},
    }
    assert schema["properties"]["category"]["enum"] == list(
        emotion_v2.EMOTION_CATEGORIES
    )
    assert schema["properties"]["action_tendency"]["enum"] == list(
        emotion_v2.ACTION_TENDENCIES
    )

    schema["properties"]["category"]["enum"].clear()
    assert emotion_v2.EmotionPatch.json_schema()["properties"]["category"]["enum"]


def test_structured_patch_clamps_numeric_ranges_and_trims_appraisal() -> None:
    patch = emotion_v2.EmotionPatch.from_mapping(
        {
            "scope": "user",
            "scope_id": 10001,
            "vad": {
                "valence": 9.0,
                "arousal": -2.0,
                "dominance": -8.0,
            },
            "category": "期待",
            "confidence": 5.0,
            "appraisal": {
                "reason": "  A   B  " + "很" * 300,
                "goal": "继续交流",
                "certainty": "较确定",
                "controllability": "可控",
            },
            "action_tendency": "approach",
        }
    )

    assert patch.scope == "user"
    assert patch.scope_id == "10001"
    assert patch.vad == {
        "valence": 1.0,
        "arousal": 0.0,
        "dominance": -1.0,
    }
    assert patch.confidence == 1.0
    assert patch.category == "期待"
    assert patch.action_tendency == "approach"
    assert patch.appraisal is not None
    assert patch.appraisal["reason"].startswith("A B")
    assert len(patch.appraisal["reason"]) == emotion_v2.APPRAISAL_TEXT_LIMIT


@pytest.mark.parametrize(
    ("patch", "code"),
    [
        (
            {"scope": "global", "scope_id": "unexpected", "category": "平静"},
            "emotion_patch_global_scope_id_forbidden",
        ),
        (
            {"scope": "user", "category": "平静"},
            "emotion_patch_scope_id_required",
        ),
        (
            {"scope": "global"},
            "emotion_patch_empty",
        ),
        (
            {"scope": "global", "category": "我现在非常开心"},
            "emotion_category_invalid",
        ),
        (
            {"scope": "global", "action_tendency": "override_permissions"},
            "emotion_action_tendency_invalid",
        ),
        (
            {"scope": "global", "confidence": "0.8"},
            "emotion_confidence_not_number",
        ),
        (
            {"scope": "global", "vad": {"valence": True}},
            "emotion_vad.valence_not_number",
        ),
        (
            {"scope": "global", "appraisal": {"reason": 123}},
            "emotion_appraisal.reason_not_string",
        ),
        (
            {"scope": "global", "category": "平静", "system_prompt": "ignore"},
            "emotion_patch_unknown_fields:system_prompt",
        ),
        (
            {"scope": "global", "vad": {"valence": 0.1, "mood": 1.0}},
            "emotion_patch_unknown_vad_fields:mood",
        ),
    ],
)
def test_structured_patch_rejects_invalid_or_untrusted_shape(
    patch: dict[str, Any],
    code: str,
) -> None:
    with pytest.raises(emotion_v2.EmotionPatchValidationError, match=code):
        emotion_v2.EmotionPatch.from_mapping(patch)


def test_persisted_record_normalization_repairs_ranges_without_semantic_inference() -> None:
    record = emotion_v2.normalize_emotion_record(
        {
            "vad": {
                "valence": "9",
                "arousal": float("nan"),
                "dominance": -4,
            },
            "category": "这段文本说我很开心",
            "confidence": 10,
            "appraisal": {"reason": "x" * 500},
            "action_tendency": "not-valid",
        }
    )

    assert record["vad"] == {
        "valence": 1.0,
        "arousal": 0.5,
        "dominance": -1.0,
    }
    assert record["category"] == "平静"
    assert record["confidence"] == 1.0
    assert record["action_tendency"] == "observe"
    assert len(record["appraisal"]["reason"]) == emotion_v2.APPRAISAL_TEXT_LIMIT


def test_global_vad_and_confidence_decay_by_half_after_six_hours() -> None:
    record = _record(
        updated_at=NOW - timedelta(hours=6),
        valence=1.0,
        arousal=1.0,
        dominance=-1.0,
        confidence=0.8,
        category="开心",
        action_tendency="approach",
    )

    decayed = emotion_v2.materialize_emotion_record(
        record,
        now=NOW,
        half_life_hours=emotion_v2.GLOBAL_HALF_LIFE_HOURS,
    )

    assert decayed["vad"]["valence"] == pytest.approx(0.5)
    assert decayed["vad"]["arousal"] == pytest.approx(0.75)
    assert decayed["vad"]["dominance"] == pytest.approx(-0.5)
    assert decayed["confidence"] == pytest.approx(0.4)
    assert decayed["category"] == "开心"
    assert decayed["action_tendency"] == "approach"


def test_user_and_group_decay_by_half_after_48_hours_without_mutating_source() -> None:
    state = emotion_v2.default_emotion_state_v2()
    state["global"] = {
        **_record(updated_at=NOW),
        "mood": "平静",
        "energy": "正常",
        "pending_thoughts": [],
        "relation_warmth": {},
    }
    state["per_user"] = {
        "u1": {
            **_record(
                updated_at=NOW - timedelta(hours=48),
                valence=-1.0,
                arousal=0.0,
                dominance=1.0,
                confidence=1.0,
            ),
            "bot_emotion": "低落",
        }
    }
    state["per_group"] = {
        "g1": {
            **_record(
                updated_at=NOW - timedelta(hours=48),
                valence=0.8,
                arousal=1.0,
                dominance=-0.6,
                confidence=0.6,
            ),
            "bot_emotion": "期待",
        }
    }
    state["updated_at"] = _timestamp(NOW)
    before = copy.deepcopy(state)

    view = emotion_v2.materialize_emotion_state_v2(state, now=NOW)

    assert view["per_user"]["u1"]["vad"] == pytest.approx(
        {"valence": -0.5, "arousal": 0.25, "dominance": 0.5}
    )
    assert view["per_user"]["u1"]["confidence"] == pytest.approx(0.5)
    assert view["per_group"]["g1"]["vad"] == pytest.approx(
        {"valence": 0.4, "arousal": 0.75, "dominance": -0.3}
    )
    assert view["per_group"]["g1"]["confidence"] == pytest.approx(0.3)
    assert state == before


def test_ttl_prunes_only_stale_user_and_group_entries() -> None:
    stale = NOW - timedelta(days=30, seconds=1)
    fresh = NOW - timedelta(days=29)
    state = emotion_v2.default_emotion_state_v2()
    state["global"] = {
        **_record(updated_at=stale, valence=1.0),
        "mood": "开心",
        "energy": "正常",
        "pending_thoughts": [],
        "relation_warmth": {},
    }
    state["per_user"] = {
        "stale": _record(updated_at=stale),
        "fresh": _record(updated_at=fresh),
    }
    state["per_group"] = {
        "stale": _record(updated_at=stale),
        "fresh": _record(updated_at=fresh),
    }
    state["updated_at"] = _timestamp(NOW)

    normalized = emotion_v2.normalize_emotion_state_v2(state, now=NOW)

    assert set(normalized["per_user"]) == {"fresh"}
    assert set(normalized["per_group"]) == {"fresh"}
    assert normalized["global"]["updated_at"] == _timestamp(stale)


def test_v1_migration_is_non_destructive_and_preserves_legacy_labels() -> None:
    stale = _timestamp(NOW - timedelta(days=31))
    recent = _timestamp(NOW - timedelta(hours=1))
    v1_emotion = {
        "per_user": {
            "10001": {
                "user_attitude": "愿意继续聊天",
                "bot_emotion": "期待",
                "emotion_intensity": "medium",
                "expression_style": "自然简短",
                "tts_style_hint": "轻快",
                "sticker_mood_hint": "期待",
                "last_group_id": "20001",
                "last_reply": "好呀",
                "updated_at": recent,
            },
            "10002": {
                "bot_emotion": "从这句话猜我很开心",
                "updated_at": recent,
            },
            "expired": {"bot_emotion": "低落", "updated_at": stale},
        },
        "per_group": {
            "20001": {
                "group_climate": "轻松",
                "bot_social_posture": "自然参与",
                "bot_emotion": "放松",
                "emotion_intensity": "low",
                "last_user_id": "10001",
                "updated_at": recent,
            }
        },
        "updated_at": recent,
    }
    v1_inner = {
        "mood": "好奇",
        "energy": "中",
        "pending_thoughts": [{"thought": "稍后继续这个话题"}],
        "relation_warmth": {"10001": 0.4},
        "updated_at": recent,
    }
    emotion_before = copy.deepcopy(v1_emotion)
    inner_before = copy.deepcopy(v1_inner)

    migrated = emotion_v2.migrate_v1_emotion_state(
        v1_emotion,
        v1_inner,
        now=NOW,
    )

    assert v1_emotion == emotion_before
    assert v1_inner == inner_before
    assert migrated["schema_version"] == 2
    assert migrated["migration"] == {
        "source": "v1",
        "migrated_at": _timestamp(NOW),
        "v1_emotion_present": True,
        "v1_inner_present": True,
    }
    assert migrated["global"]["category"] == "好奇"
    assert migrated["global"]["mood"] == "好奇"
    assert migrated["global"]["vad"] == {
        "valence": 0.0,
        "arousal": 0.5,
        "dominance": 0.0,
    }
    assert migrated["per_user"]["10001"]["category"] == "期待"
    assert migrated["per_user"]["10001"]["bot_emotion"] == "期待"
    assert migrated["per_user"]["10001"]["confidence"] == 0.0
    assert migrated["per_user"]["10002"]["category"] == "平静"
    assert (
        migrated["per_user"]["10002"]["bot_emotion"]
        == "从这句话猜我很开心"
    )
    assert "expired" not in migrated["per_user"]
    assert migrated["per_group"]["20001"]["category"] == "放松"


def test_v1_compatibility_view_keeps_exact_legacy_shapes() -> None:
    migrated = emotion_v2.migrate_v1_emotion_state(
        {
            "per_user": {
                "u1": {
                    "user_attitude": "友好",
                    "bot_emotion": "开心",
                    "last_reply": "收到",
                    "updated_at": _timestamp(NOW),
                }
            },
            "per_group": {
                "g1": {
                    "group_climate": "活跃",
                    "bot_social_posture": "参与",
                    "bot_emotion": "好奇",
                    "updated_at": _timestamp(NOW),
                }
            },
        },
        {
            "mood": "放松",
            "energy": "正常",
            "pending_thoughts": [],
            "relation_warmth": {"u1": 0.2},
            "updated_at": _timestamp(NOW),
        },
        now=NOW,
    )

    view = emotion_v2.build_v1_compatibility_view(migrated, now=NOW)

    assert set(view) == {"inner_state", "emotion_state"}
    assert view["inner_state"] == {
        "mood": "放松",
        "energy": "正常",
        "pending_thoughts": [],
        "relation_warmth": {"u1": 0.2},
        "updated_at": _timestamp(NOW),
    }
    assert view["emotion_state"]["per_user"]["u1"]["bot_emotion"] == "开心"
    assert view["emotion_state"]["per_user"]["u1"]["last_reply"] == "收到"
    assert "vad" not in view["emotion_state"]["per_user"]["u1"]
    assert view["emotion_state"]["per_group"]["g1"]["group_climate"] == "活跃"


def test_partial_patch_preserves_other_fields_and_updates_legacy_category() -> None:
    initial = emotion_v2.migrate_v1_emotion_state(
        {
            "per_user": {
                "u1": {
                    "user_attitude": "友好",
                    "bot_emotion": "平静",
                    "expression_style": "自然",
                    "last_reply": "旧回复",
                    "updated_at": _timestamp(NOW - timedelta(hours=1)),
                }
            }
        },
        {},
        now=NOW,
    )

    updated = emotion_v2.apply_emotion_patch(
        initial,
        {
            "scope": "user",
            "scope_id": "u1",
            "vad": {"valence": 0.75},
            "category": "期待",
            "confidence": 0.9,
            "appraisal": {"reason": "对话进展顺利"},
            "action_tendency": "approach",
        },
        now=NOW + timedelta(minutes=5),
    )

    entry = updated["per_user"]["u1"]
    assert entry["vad"] == {
        "valence": 0.75,
        "arousal": 0.5,
        "dominance": 0.0,
    }
    assert entry["category"] == "期待"
    assert entry["bot_emotion"] == "期待"
    assert entry["confidence"] == 0.9
    assert entry["appraisal"] == {
        "reason": "对话进展顺利",
        "goal": "",
        "certainty": "",
        "controllability": "",
    }
    assert entry["action_tendency"] == "approach"
    assert entry["user_attitude"] == "友好"
    assert entry["expression_style"] == "自然"
    assert entry["last_reply"] == "旧回复"


def test_patch_does_not_decay_or_retimestamp_unrelated_records() -> None:
    initial = emotion_v2.default_emotion_state_v2()
    old_time = NOW - timedelta(hours=48)
    initial["per_user"] = {
        "untouched": {
            **_record(
                updated_at=old_time,
                valence=1.0,
                arousal=1.0,
                dominance=1.0,
                confidence=1.0,
            ),
            "bot_emotion": "开心",
        }
    }
    initial["updated_at"] = _timestamp(old_time)

    updated = emotion_v2.apply_emotion_patch(
        initial,
        {"scope": "global", "category": "好奇"},
        now=NOW,
    )

    assert updated["per_user"]["untouched"]["vad"] == {
        "valence": 1.0,
        "arousal": 1.0,
        "dominance": 1.0,
    }
    assert updated["per_user"]["untouched"]["confidence"] == 1.0
    assert updated["per_user"]["untouched"]["updated_at"] == _timestamp(old_time)


def test_service_lazy_migration_persists_v2_once_without_modifying_v1() -> None:
    v1_emotion = {
        "per_user": {
            "u1": {
                "bot_emotion": "期待",
                "updated_at": _timestamp(NOW),
            }
        },
        "per_group": {},
        "updated_at": _timestamp(NOW),
    }
    v1_inner = {
        "mood": "好奇",
        "energy": "中",
        "pending_thoughts": [],
        "relation_warmth": {},
        "updated_at": _timestamp(NOW),
    }
    store = MemoryStore(
        {
            emotion_v2.V1_EMOTION_STORE_NAME: v1_emotion,
            emotion_v2.V1_INNER_STORE_NAME: v1_inner,
        }
    )
    service = emotion_v2.EmotionStateV2Service(
        store=store,
        clock=lambda: NOW,
        mirror_v1=False,
    )

    first = asyncio.run(service.load())
    assert first["global"]["category"] == "好奇"
    assert first["per_user"]["u1"]["category"] == "期待"
    assert store.data[emotion_v2.V1_EMOTION_STORE_NAME] == v1_emotion
    assert store.data[emotion_v2.V1_INNER_STORE_NAME] == v1_inner

    store.data[emotion_v2.V1_INNER_STORE_NAME]["mood"] = "低落"
    second = asyncio.run(service.load())
    assert second["global"]["category"] == "好奇"
    assert store.data[emotion_v2.STORE_NAME]["schema_version"] == 2


def test_service_preserves_string_schema_version_two_instead_of_remigrating() -> None:
    existing = emotion_v2.default_emotion_state_v2()
    existing["schema_version"] = "2"
    existing["global"] = {
        **_record(updated_at=NOW, category="期待"),
        "mood": "期待",
        "energy": "正常",
        "pending_thoughts": [],
        "relation_warmth": {},
    }
    existing["updated_at"] = _timestamp(NOW)
    store = MemoryStore(
        {
            emotion_v2.STORE_NAME: existing,
            emotion_v2.V1_INNER_STORE_NAME: {"mood": "低落"},
        }
    )
    service = emotion_v2.EmotionStateV2Service(
        store=store,
        clock=lambda: NOW,
        mirror_v1=False,
    )

    loaded = asyncio.run(service.load())

    assert loaded["global"]["category"] == "期待"
    assert store.data[emotion_v2.STORE_NAME]["global"]["category"] == "期待"


def test_service_patch_mirrors_only_targeted_v1_scope_and_preserves_unknown_fields() -> None:
    store = MemoryStore(
        {
            emotion_v2.V1_EMOTION_STORE_NAME: {
                "per_user": {
                    "u1": {
                        "bot_emotion": "平静",
                        "custom_legacy_field": "保留",
                        "updated_at": _timestamp(NOW),
                    },
                    "u2": {
                        "bot_emotion": "开心",
                        "updated_at": _timestamp(NOW),
                    },
                },
                "per_group": {"g1": {"custom_group": "保留"}},
                "custom_root": "保留",
                "updated_at": _timestamp(NOW),
            },
            emotion_v2.V1_INNER_STORE_NAME: {
                "mood": "平静",
                "energy": "正常",
                "custom_inner": "保留",
                "updated_at": _timestamp(NOW),
            },
        }
    )
    service = emotion_v2.EmotionStateV2Service(store=store, clock=lambda: NOW)

    asyncio.run(
        service.apply_patch(
            {
                "scope": "user",
                "scope_id": "u1",
                "category": "期待",
                "confidence": 0.8,
            }
        )
    )
    legacy_emotion = store.data[emotion_v2.V1_EMOTION_STORE_NAME]
    assert legacy_emotion["per_user"]["u1"]["bot_emotion"] == "期待"
    assert legacy_emotion["per_user"]["u1"]["custom_legacy_field"] == "保留"
    assert legacy_emotion["per_user"]["u2"]["bot_emotion"] == "开心"
    assert legacy_emotion["per_group"]["g1"]["custom_group"] == "保留"
    assert legacy_emotion["custom_root"] == "保留"

    asyncio.run(
        service.apply_patch(
            {"scope": "global", "category": "好奇", "confidence": 0.7}
        )
    )
    legacy_inner = store.data[emotion_v2.V1_INNER_STORE_NAME]
    assert legacy_inner["mood"] == "好奇"
    assert legacy_inner["custom_inner"] == "保留"


def test_service_reports_partial_compatibility_write_with_written_v2_state() -> None:
    store = FailingLegacyStore(
        {
            emotion_v2.V1_EMOTION_STORE_NAME: {
                "per_user": {},
                "per_group": {},
            },
            emotion_v2.V1_INNER_STORE_NAME: {},
        }
    )
    service = emotion_v2.EmotionStateV2Service(store=store, clock=lambda: NOW)

    with pytest.raises(emotion_v2.EmotionCompatibilityWriteError) as captured:
        asyncio.run(
            service.apply_patch(
                {"scope": "user", "scope_id": "u1", "category": "期待"}
            )
        )

    assert captured.value.state["per_user"]["u1"]["category"] == "期待"
    assert store.data[emotion_v2.STORE_NAME]["per_user"]["u1"]["category"] == "期待"


def test_real_data_store_concurrent_patches_are_atomic(tmp_path) -> None:  # noqa: ANN001
    data_store.init_data_store(
        SimpleNamespace(personification_data_dir=str(tmp_path))
    )
    service = emotion_v2.EmotionStateV2Service(
        clock=lambda: NOW,
        mirror_v1=False,
    )

    async def _run() -> dict[str, Any]:
        await asyncio.wait_for(
            asyncio.gather(
                service.apply_patch(
                    {
                        "scope": "user",
                        "scope_id": "10001",
                        "category": "开心",
                        "vad": {"valence": 0.8},
                    }
                ),
                service.apply_patch(
                    {
                        "scope": "user",
                        "scope_id": "10002",
                        "category": "好奇",
                        "vad": {"dominance": 0.6},
                    }
                ),
                service.apply_patch(
                    {
                        "scope": "group",
                        "scope_id": "20001",
                        "category": "放松",
                        "vad": {"arousal": 0.2},
                    }
                ),
            ),
            timeout=2.0,
        )
        return await service.load()

    state = asyncio.run(_run())

    assert state["per_user"]["10001"]["category"] == "开心"
    assert state["per_user"]["10001"]["vad"]["valence"] == 0.8
    assert state["per_user"]["10002"]["category"] == "好奇"
    assert state["per_user"]["10002"]["vad"]["dominance"] == 0.6
    assert state["per_group"]["20001"]["category"] == "放松"
    assert state["per_group"]["20001"]["vad"]["arousal"] == 0.2


def test_newer_schema_is_rejected_instead_of_silently_downgraded() -> None:
    with pytest.raises(
        emotion_v2.UnsupportedEmotionStateVersion,
        match="emotion_state_schema_newer:3",
    ):
        emotion_v2.normalize_emotion_state_v2(
            {"schema_version": 3},
            now=NOW,
        )

    store = MemoryStore({emotion_v2.STORE_NAME: {"schema_version": 3}})
    service = emotion_v2.EmotionStateV2Service(
        store=store,
        clock=lambda: NOW,
        mirror_v1=False,
    )
    with pytest.raises(emotion_v2.UnsupportedEmotionStateVersion):
        asyncio.run(service.load())


def test_record_turn_binds_structured_updates_to_trusted_current_scope() -> None:
    store = MemoryStore(
        {
            emotion_v2.V1_EMOTION_STORE_NAME: {"per_user": {}, "per_group": {}},
            emotion_v2.V1_INNER_STORE_NAME: {},
        }
    )
    service = emotion_v2.EmotionStateV2Service(
        store=store,
        clock=lambda: NOW,
        mirror_v1=False,
    )
    frame = SimpleNamespace(
        user_attitude="认真交流",
        bot_emotion="不能按这段自由文本猜类别",
        emotion_intensity="medium",
        expression_style="自然回应",
        tts_style_hint="自然",
        sticker_mood_hint="期待|日常交流",
        confidence=0.9,
    )

    state = asyncio.run(
        service.record_turn(
            user_id="trusted-user",
            group_id="trusted-group",
            semantic_frame=frame,
            assistant_text="收到",
            is_private=False,
            emotion_updates=[
                {
                    "scope": "user",
                    "scope_id": "attacker-selected-user",
                    "vad": {"valence": 0.7, "arousal": 0.6},
                    "category": "期待",
                    "confidence": 0.8,
                    "action_tendency": "approach",
                },
                {
                    "scope": "group",
                    "scope_id": "attacker-selected-group",
                    "vad": {"dominance": 0.3},
                    "category": "放松",
                },
            ],
        )
    )

    assert "attacker-selected-user" not in state["per_user"]
    assert "attacker-selected-group" not in state["per_group"]
    assert state["per_user"]["trusted-user"]["category"] == "期待"
    assert state["per_user"]["trusted-user"]["vad"]["valence"] == 0.7
    assert state["per_user"]["trusted-user"]["last_reply"] == "收到"
    assert state["per_group"]["trusted-group"]["category"] == "放松"
    assert state["per_group"]["trusted-group"]["last_user_id"] == "trusted-user"


def test_record_turn_private_session_ignores_group_update_and_exact_label_only() -> None:
    store = MemoryStore(
        {
            emotion_v2.V1_EMOTION_STORE_NAME: {"per_user": {}, "per_group": {}},
            emotion_v2.V1_INNER_STORE_NAME: {},
        }
    )
    service = emotion_v2.EmotionStateV2Service(
        store=store,
        clock=lambda: NOW,
        mirror_v1=False,
    )
    frame = SimpleNamespace(
        user_attitude="日常交流",
        bot_emotion="期待",
        emotion_intensity="low",
        expression_style="自然",
        tts_style_hint="自然",
        sticker_mood_hint="期待|日常交流",
        confidence=0.6,
    )
    state = asyncio.run(
        service.record_turn(
            user_id="u1",
            group_id="must-not-be-used",
            semantic_frame=frame,
            is_private=True,
            emotion_updates=[
                {"scope": "group", "category": "开心"},
                {"scope": "user", "category": "期待"},
            ],
        )
    )

    assert state["per_group"] == {}
    assert state["per_user"]["u1"]["category"] == "期待"
    assert state["per_user"]["u1"]["confidence"] == 0.6
