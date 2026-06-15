from __future__ import annotations

import json
from types import SimpleNamespace

from ._loader import load_personification_module

config_module = load_personification_module("plugin.personification.config")
config_manager = load_personification_module("plugin.personification.core.config_manager")
env_writer = load_personification_module("plugin.personification.core.env_writer")
runtime_config = load_personification_module("plugin.personification.core.runtime_config")
data_store = load_personification_module("plugin.personification.core.data_store")


def _setup(tmp_path, monkeypatch):
    # 无 .env 文件，env.json 落在 tmp_path
    monkeypatch.setattr(runtime_config, "_iter_env_file_candidates", lambda: [])
    monkeypatch.setattr(env_writer, "_iter_env_file_candidates", lambda: [])
    data_store.init_data_store(SimpleNamespace(personification_data_dir=str(tmp_path)))
    return config_module.Config(personification_data_dir=str(tmp_path))


def test_webui_change_persists_across_restart(tmp_path, monkeypatch) -> None:
    """改一个不在 .env 的字段 → 模拟重启后值应保留（不回退到默认）。"""
    cfg = _setup(tmp_path, monkeypatch)
    res = env_writer.write_both("personification_probability", 0.66, cfg)
    assert res["dotenv_path"] is None  # 新字段不写 .env，避免重启回退
    assert res["env_json_path"]

    fresh = config_module.Config(personification_data_dir=str(tmp_path))
    assert fresh.personification_probability != 0.66
    config_manager.ConfigManager(plugin_config=fresh, logger=None).load()
    assert fresh.personification_probability == 0.66


def test_env_json_retains_webui_field_after_load(tmp_path, monkeypatch) -> None:
    cfg = _setup(tmp_path, monkeypatch)
    env_writer.write_both("personification_probability", 0.42, cfg)
    fresh = config_module.Config(personification_data_dir=str(tmp_path))
    config_manager.ConfigManager(plugin_config=fresh, logger=None).load()

    env_json = json.loads((tmp_path / "env.json").read_text(encoding="utf-8"))
    assert env_json.get("personification_probability") == 0.42
