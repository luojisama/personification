from __future__ import annotations

from ._loader import load_personification_module

config_registry = load_personification_module("plugin.personification.core.config_registry")


def test_all_entries_have_non_empty_group_and_kind() -> None:
    entries = config_registry.get_config_entries()
    assert entries, "registry must not be empty"
    for entry in entries:
        assert entry.group, f"{entry.key} missing group"
        assert entry.group != "其他", f"{entry.key} falls back to 其他 group"
        assert entry.kind, f"{entry.key} missing kind"


def test_kind_inference_matches_value_type() -> None:
    entries = {entry.key: entry for entry in config_registry.get_config_entries()}
    # bool 全部映射成 toggle
    for entry in entries.values():
        if entry.value_type == "bool":
            assert entry.kind == "toggle", f"{entry.key} bool should be toggle"
    # secret 优先级高于 path/select
    assert entries["fallback_api_key"].kind == "secret"
    assert entries["fallback_api_key"].secret is True
    assert entries["fallback_auth_path"].secret is True
    # tts_voice_clone_path 不是密钥但是文件路径
    assert entries["tts_voice_clone_path"].kind == "path"
    # JSON 列表
    assert entries["api_pools"].kind == "json"
    # 整数
    assert entries["agent_max_steps"].kind == "int"


def test_required_fields_marked() -> None:
    entries = {entry.key: entry for entry in config_registry.get_config_entries()}
    assert entries["global_enabled"].required is True
    assert entries["api_pools"].required is True
    # 非核心字段默认 False
    assert entries["tts_global_enabled"].required is False


def test_group_routing() -> None:
    entries = {entry.key: entry for entry in config_registry.get_config_entries()}
    assert entries["tts_global_enabled"].group == "TTS 语音"
    assert entries["qzone_social_enabled"].group == "QQ 空间"
    assert entries["image_gen_enabled"].group == "图像生成"
    assert entries["memory_enabled"].group == "记忆"
    assert entries["fallback_enabled"].group == "模型回退"
    assert entries["proactive_enabled"].group == "主动私聊"
    assert entries["api_pools"].group == "模型路由"
    assert entries["global_enabled"].group == "核心开关"
    assert entries["response_review_enabled"].group == "回复审阅"
    assert entries["turn_planner_enabled"].group == "意图规划"


def test_secret_inference_for_key_and_auth_path_fields() -> None:
    entries = {entry.key: entry for entry in config_registry.get_config_entries()}
    # api_key 后缀字段必须 secret
    assert entries["fallback_api_key"].secret is True
    assert entries["video_fallback_api_key"].secret is True
    # auth_path 字段也是密钥
    assert entries["fallback_auth_path"].secret is True
    assert entries["video_fallback_auth_path"].secret is True
    # URL 字段不是 secret
    assert entries["fallback_api_url"].secret is False
    # 普通 toggle 不 secret
    assert entries["tts_global_enabled"].secret is False
