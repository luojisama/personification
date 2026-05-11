from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from ._loader import load_personification_module

config_manager = load_personification_module("plugin.personification.core.config_manager")


def _build_config(tmp_path, *, fields_set: set[str] | None = None):  # noqa: ANN001
    return SimpleNamespace(
        personification_data_dir=str(tmp_path),
        personification_global_enabled=True,
        personification_tts_global_enabled=True,
        personification_lite_model="gpt-5.4-mini",
        personification_plugin_knowledge_build_enabled=False,
        __pydantic_fields_set__=set(fields_set or set()),
    )


def _make_workspace_temp_dir(prefix: str) -> Path:
    base_dir = Path(__file__).resolve().parent / ".tmp"
    base_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = base_dir / f"{prefix}{uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=False)
    return temp_dir


def test_config_manager_save_and_load_round_trip() -> None:
    temp_dir = _make_workspace_temp_dir("config-manager-")
    try:
        cfg = _build_config(temp_dir)
        manager = config_manager.ConfigManager(plugin_config=cfg, logger=None)

        manager.save()
        cfg.personification_global_enabled = False
        cfg.personification_lite_model = ""
        manager.load()

        payload = json.loads((temp_dir / "env.json").read_text(encoding="utf-8"))
        assert payload["personification_global_enabled"] is True
        assert cfg.personification_global_enabled is True
        assert cfg.personification_lite_model == "gpt-5.4-mini"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_config_manager_skips_explicit_env_fields() -> None:
    temp_dir = _make_workspace_temp_dir("config-manager-")
    try:
        cfg = _build_config(temp_dir, fields_set={"personification_global_enabled"})
        (temp_dir / "env.json").write_text(
            json.dumps(
                {
                    "personification_global_enabled": False,
                    "personification_lite_model": "lite-from-file",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        manager = config_manager.ConfigManager(plugin_config=cfg, logger=None)

        manager.load()
        info = config_manager.get_env_config_load_info(cfg)

        assert cfg.personification_global_enabled is True
        assert cfg.personification_lite_model == "lite-from-file"
        assert "personification_global_enabled" in info["skipped_fields"]
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_config_manager_save_uses_atomic_replace(monkeypatch) -> None:
    temp_dir = _make_workspace_temp_dir("config-manager-")
    try:
        cfg = _build_config(temp_dir)
        manager = config_manager.ConfigManager(plugin_config=cfg, logger=None)
        replace_calls: list[tuple[str, str]] = []
        real_replace = config_manager.os.replace

        def _wrapped_replace(src, dst):  # noqa: ANN001
            replace_calls.append((str(src), str(dst)))
            return real_replace(src, dst)

        monkeypatch.setattr(config_manager.os, "replace", _wrapped_replace)

        manager.save()

        assert replace_calls
        assert replace_calls[0][1] == str(temp_dir / "env.json")
        assert not list(temp_dir.glob("*.tmp"))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_config_manager_load_respects_env_explicit_fields(monkeypatch) -> None:
    """回归测试：env.json 中残留的字段被 .env / pydantic_fields_set 显式声明时不应覆盖。

    场景：历史上 admin 命令 `/拟人 模型 路由 codex_primary` 把 codex 持久化到 env.json，
    随后 .env.prod 改为 gemini_cli_primary。pydantic 加载 .env.prod 时把字段加入
    fields_set，ConfigManager.load() 必须跳过 env.json 的旧值。
    """
    temp_dir = _make_workspace_temp_dir("config-manager-bug-")
    try:
        # 1. 模拟 env.json 中有旧 api_pools（codex_primary）
        env_json_path = temp_dir / "env.json"
        env_json_path.write_text(
            json.dumps({"personification_api_pools": [{"name": "codex_primary", "api_type": "openai_codex"}]}),
            encoding="utf-8",
        )

        # 2. plugin_config 已经被 pydantic 用 .env.prod 加载好 gemini_cli_primary，
        #    并且 fields_set 含 personification_api_pools
        cfg = _build_config(temp_dir, fields_set={"personification_api_pools"})
        cfg.personification_api_pools = [{"name": "gemini_cli_primary", "api_type": "gemini_cli"}]

        manager = config_manager.ConfigManager(plugin_config=cfg, logger=None)
        manager.load()

        # 3. 必须仍是 gemini_cli_primary（env.json 的 codex_primary 不能复活）
        assert cfg.personification_api_pools[0]["name"] == "gemini_cli_primary"
        # 4. env.json 应该被自动清理，不再持久化 .env 接管的字段
        cleaned = json.loads(env_json_path.read_text(encoding="utf-8"))
        assert "personification_api_pools" not in cleaned
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_config_manager_save_skips_env_explicit_fields(monkeypatch) -> None:
    """回归测试：admin 命令热切时 ConfigManager.save() 不能把 .env 接管的字段写进 env.json。"""
    temp_dir = _make_workspace_temp_dir("config-manager-bug-")
    try:
        cfg = _build_config(temp_dir, fields_set={"personification_api_pools"})
        cfg.personification_api_pools = [{"name": "user_hot_switch", "api_type": "openai_codex"}]

        manager = config_manager.ConfigManager(plugin_config=cfg, logger=None)
        manager.save()

        payload = json.loads((temp_dir / "env.json").read_text(encoding="utf-8"))
        # api_pools 来自 .env，热切结果不能持久化进 env.json
        assert "personification_api_pools" not in payload
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_config_manager_async_update_persists_changes() -> None:
    temp_dir = _make_workspace_temp_dir("config-manager-")
    try:
        cfg = _build_config(temp_dir)
        manager = config_manager.ConfigManager(plugin_config=cfg, logger=None)

        asyncio.run(
            manager.update(
                {
                    "personification_global_enabled": False,
                    "personification_lite_model": "lite-from-update",
                }
            )
        )

        payload = json.loads((temp_dir / "env.json").read_text(encoding="utf-8"))
        assert payload["personification_global_enabled"] is False
        assert payload["personification_lite_model"] == "lite-from-update"
        assert cfg.personification_global_enabled is False
        assert cfg.personification_lite_model == "lite-from-update"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
