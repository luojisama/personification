from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from dotenv import dotenv_values

from ._loader import load_personification_module

env_writer = load_personification_module("plugin.personification.core.env_writer")


def _make_plugin_config(tmp_path: Path, **extra) -> SimpleNamespace:
    cfg = SimpleNamespace(personification_data_dir=str(tmp_path), **extra)
    return cfg


def test_serialize_value_handles_basic_types() -> None:
    s = env_writer._serialize_value
    assert s(True) == "true"
    assert s(False) == "false"
    assert s(42) == "42"
    assert s(0.5) == "0.5"
    assert s(None) == ""
    assert s("hello") == "hello"
    assert s({"a": 1}) == '{"a":1}'
    assert s([1, 2]) == "[1,2]"


def test_write_dotenv_updates_existing_key_and_creates_backup(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.prod"
    env_file.write_text(
        "# 顶部注释\n"
        "personification_probability=0.3\n"
        "personification_global_enabled=true\n"
        "# 中间注释\n"
        "personification_agent_max_steps=5\n",
        encoding="utf-8",
    )
    result = env_writer.write_dotenv("personification_probability", 0.5, target=env_file)
    assert result == env_file
    text = env_file.read_text(encoding="utf-8")
    parsed = dotenv_values(str(env_file))
    assert parsed["personification_probability"] == "0.5"
    assert parsed["personification_global_enabled"] == "true"
    assert parsed["personification_agent_max_steps"] == "5"
    assert "# 顶部注释" in text
    assert "# 中间注释" in text
    backups = list(tmp_path.glob(".env.prod.bak.*"))
    assert backups, "backup file should exist after write"


def test_write_dotenv_appends_when_key_missing(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.prod"
    env_file.write_text("personification_global_enabled=true\n", encoding="utf-8")
    env_writer.write_dotenv("personification_probability", 0.42, target=env_file, backup=False)
    parsed = dotenv_values(str(env_file))
    assert parsed["personification_probability"] == "0.42"
    assert parsed["personification_global_enabled"] == "true"


def test_write_env_json_creates_and_merges(tmp_path: Path) -> None:
    cfg = _make_plugin_config(tmp_path)
    path = env_writer.write_env_json("personification_probability", 0.5, cfg)
    assert Path(path).exists()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data["personification_probability"] == 0.5

    env_writer.write_env_json("personification_agent_max_steps", 7, cfg)
    data2 = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data2["personification_probability"] == 0.5
    assert data2["personification_agent_max_steps"] == 7


def test_write_both_emits_paths_and_errors(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.prod"
    env_file.write_text("# header\n", encoding="utf-8")
    cfg = _make_plugin_config(tmp_path)
    # monkey-patch target resolver to point at tmp file
    original = env_writer._resolve_dotenv_target
    env_writer._resolve_dotenv_target = lambda: env_file
    try:
        result = env_writer.write_both("personification_probability", 0.6, cfg)
    finally:
        env_writer._resolve_dotenv_target = original
    assert result["errors"] == []
    assert result["dotenv_path"] == str(env_file)
    assert Path(result["env_json_path"]).exists()
    parsed = dotenv_values(str(env_file))
    assert parsed["personification_probability"] == "0.6"
    json_data = json.loads(Path(result["env_json_path"]).read_text(encoding="utf-8"))
    assert json_data["personification_probability"] == 0.6


def test_resolve_value_sources_reports_env_json_when_no_env_file(tmp_path: Path, monkeypatch) -> None:
    cfg = _make_plugin_config(tmp_path, personification_probability=0.5)
    # 屏蔽真实 .env 扫描
    monkeypatch.setattr(env_writer, "read_env_file_value", lambda _key: "")
    env_writer.write_env_json("personification_probability", 0.5, cfg)
    sources = env_writer.resolve_value_sources("personification_probability", cfg)
    assert sources["env_file"] is None
    assert sources["env_json"] == 0.5
    assert sources["active_source"] == "env_json"
    assert sources["current"] == 0.5


def test_resolve_value_sources_env_file_wins(tmp_path: Path, monkeypatch) -> None:
    cfg = _make_plugin_config(tmp_path, personification_probability=0.7)
    monkeypatch.setattr(env_writer, "read_env_file_value", lambda key: "0.7" if key == "personification_probability" else "")
    env_writer.write_env_json("personification_probability", 0.3, cfg)
    sources = env_writer.resolve_value_sources("personification_probability", cfg)
    assert sources["env_file"] == "0.7"
    assert sources["env_json"] == 0.3
    assert sources["active_source"] == "env_file"
