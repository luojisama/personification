from __future__ import annotations

import asyncio
import json
import logging
import threading
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module
from .test_webui_smoke import _runtime_context  # noqa: F401

store_mod = load_personification_module("plugin.personification.core.data_store")
db = load_personification_module("plugin.personification.core.db")


@pytest.fixture
def isolated_store(tmp_path: Path, monkeypatch):
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE kv_store (namespace TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, updated_at REAL, PRIMARY KEY(namespace,key))")

    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(store_mod, "connect_sync", connect)
    monkeypatch.setattr(store_mod, "_get_data_dir", lambda _cfg: tmp_path)
    return store_mod.DataStore(), connect


def _put(connect, name: str, value: str):
    with connect() as conn:
        conn.execute("INSERT OR REPLACE INTO kv_store(namespace,key,value) VALUES (?,?,?)", (name, "__root__", value))


def _raw(connect, name: str):
    with connect() as conn:
        row = conn.execute("SELECT value FROM kv_store WHERE namespace=? AND key='__root__'", (name,)).fetchone()
    return row[0] if row else None


def test_missing_namespace_initializes_normally(isolated_store):
    store, connect = isolated_store
    assert store.load_sync("missing") == {}
    assert store.update_sync("missing", {"key": 1}) == {"key": 1}
    assert json.loads(_raw(connect, "missing")) == {"key": 1}


@pytest.mark.parametrize("operation", ["load", "save", "mutate", "update"])
def test_corrupt_json_fails_closed_and_preserves_original_bytes(isolated_store, operation):
    store, connect = isolated_store
    _put(connect, "plugin_admins", "[broken-original-bytes")
    with pytest.raises(store_mod.CorruptDataError) as captured:
        if operation == "load":
            store.load_sync("plugin_admins")
        elif operation == "save":
            store.save_sync("plugin_admins", ["new-admin"])
        elif operation == "mutate":
            store.mutate_sync("plugin_admins", lambda value: ["new-admin"])
        else:
            store.update_sync("plugin_admins", {"new": True})
    assert captured.value.reason == "invalid_json"
    assert "[broken-original-bytes" not in str(captured.value)
    assert _raw(connect, "plugin_admins") == "[broken-original-bytes"


@pytest.mark.parametrize("existing", ['["admin"]', 'null', '42'])
def test_update_requires_existing_object(isolated_store, existing):
    store, connect = isolated_store
    _put(connect, "plugin_admins", existing)
    with pytest.raises(store_mod.CorruptDataError) as captured:
        store.update_sync("plugin_admins", {"new": True})
    assert captured.value.reason == "expected_object"
    assert _raw(connect, "plugin_admins") == existing


def test_failed_mutator_rolls_back(isolated_store):
    store, connect = isolated_store
    _put(connect, "state", '{"existing":1}')

    def broken(_value):
        raise TypeError("internal bug")

    with pytest.raises(TypeError):
        store.mutate_sync("state", broken)
    assert _raw(connect, "state") == '{"existing":1}'


def test_sync_connection_configuration_failure_closes_handle(tmp_path, monkeypatch):
    calls = []
    class Conn:
        def close(self):
            calls.append("close")

    monkeypatch.setattr(db.sqlite3, "connect", lambda *a, **kw: Conn())
    def configure(_conn):
        raise sqlite3.OperationalError("pragma failed")
    monkeypatch.setattr(db, "_configure_connection", configure)
    with pytest.raises(sqlite3.OperationalError):
        db.connect_sync(tmp_path / "state.db")
    assert calls == ["close"]


def test_async_connection_configuration_failure_closes_handle(tmp_path, monkeypatch):
    class Conn:
        def __init__(self):
            self.closed = 0
            self.row_factory = None
        async def execute(self, sql):
            raise sqlite3.OperationalError("pragma failed")
        async def close(self):
            self.closed += 1

    conn = Conn()
    async def connect(_path):
        return conn
    monkeypatch.setattr(db, "aiosqlite", SimpleNamespace(connect=connect))
    monkeypatch.setattr(db, "get_db_path", lambda: tmp_path / "state.db")
    monkeypatch.setattr(db, "_db", None)
    async def run():
        with pytest.raises(sqlite3.OperationalError):
            await db.get_db()
    asyncio.run(run())
    assert conn.closed == 1
    assert db._db is None


def test_single_config_write_failure_does_not_change_runtime_or_reload(_runtime_context, monkeypatch):
    from .test_webui_smoke import _build_client, _login_as_admin

    routes = load_personification_module("plugin.personification.webui.routes.config_routes")
    runtime = _runtime_context.app_module.get_runtime_context()
    field_name = "personification_agent_max_steps"
    original = 5
    setattr(runtime.plugin_config, field_name, original)
    calls = []
    runtime.runtime_bundle = SimpleNamespace(reload_runtime_services=lambda: calls.append("reload"))
    monkeypatch.setattr(routes.env_writer, "write_both", lambda *_args: {"errors": ["private-path"], "env_json_path": None, "dotenv_path": None})
    client = _build_client(_runtime_context)
    _login_as_admin(client, _runtime_context)
    result = client.post("/personification/api/config/value", json={"field_name": field_name, "value": "7"})
    assert result.status_code == 200
    payload = result.json()
    assert payload["success"] is False
    assert payload["diagnostic"]["code"] == "config_value_persist_failed"
    assert getattr(runtime.plugin_config, field_name) == original
    assert calls == []
    assert "private-path" not in result.text


@pytest.mark.parametrize("path", ["explicit_close", "headless_switch", "idle_evict"])
def test_browser_close_failure_keeps_handle_and_can_retry(tmp_path, monkeypatch, path):
    browser = load_personification_module("plugin.personification.native_mcp.social_research.browser")
    pool = browser.BrowserPool(tmp_path / "browser", platforms=("example",), idle_timeout_seconds=.01)

    class Context:
        def __init__(self):
            self.calls = 0
        async def close(self):
            self.calls += 1
            if self.calls == 1:
                raise OSError("private-profile-path")

    context = Context()
    pool._contexts["example"] = context
    pool._context_headless["example"] = True

    async def run():
        if path == "explicit_close":
            with pytest.raises(OSError):
                await pool.close_platform("example")
        elif path == "headless_switch":
            with pytest.raises(OSError):
                await pool.context("example", headless=False)
        else:
            pool._last_activity["example"] = 0
            with pytest.raises(OSError):
                await pool._idle_evict_after("example")
        assert pool.runtime_status()["open_contexts"] == ["example"]
        assert pool._context_headless["example"] is True
        await pool.close_platform("example")
        assert pool.runtime_status()["open_contexts"] == []
        assert context.calls == 2

    asyncio.run(run())


def test_browser_idle_task_failure_is_diagnosed_without_profile_text(tmp_path):
    browser = load_personification_module("plugin.personification.native_mcp.social_research.browser")
    pool = browser.BrowserPool(tmp_path / "browser", platforms=("example",), idle_timeout_seconds=.01)

    class Context:
        async def close(self):
            raise OSError("private-profile-path")

    pool._contexts["example"] = Context()
    pool._context_headless["example"] = True
    pool._last_activity["example"] = 0

    async def run():
        pool._schedule_idle_eviction("example")
        await asyncio.sleep(.05)
        assert pool.runtime_status()["open_contexts"] == ["example"]
        assert {item["code"] for item in pool.runtime_status()["diagnostics"]} == {"browser_context_idle_task_failed"}
        assert "private-profile-path" not in str(pool.runtime_status())

    asyncio.run(run())


def test_cancelled_data_store_worker_failure_is_diagnosed_and_cancel_propagates(isolated_store, caplog):
    store, _ = isolated_store
    entered, release = threading.Event(), threading.Event()

    def worker():
        entered.set()
        assert release.wait(3)
        raise sqlite3.OperationalError("private-data-path")

    async def run():
        task = asyncio.create_task(store._run_locked_thread(worker))
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()

    with caplog.at_level(logging.WARNING, logger=store_mod.__name__):
        asyncio.run(run())
    assert "data_store_cancelled_worker_failed error_type=OperationalError" in caplog.text
    assert "private-data-path" not in caplog.text


def test_malformed_legacy_qzone_state_skips_derived_migration_without_changing_source(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE kv_store(namespace TEXT, key TEXT, value TEXT)")
        conn.execute("CREATE TABLE qzone_monthly_usage(period TEXT, confirmed_count INTEGER, forward_count INTEGER, updated_at REAL)")
        conn.execute("INSERT INTO kv_store VALUES ('qzone_post_state','__root__','{broken')")
        db._migrate_qzone_monthly_usage(conn)
        assert conn.execute("SELECT COUNT(*) FROM qzone_monthly_usage").fetchone()[0] == 0
        assert conn.execute("SELECT value FROM kv_store").fetchone()[0] == "{broken"


def test_video_probe_cleanup_failure_is_diagnosed_without_media_path(_runtime_context, monkeypatch, tmp_path, caplog):
    from .test_webui_smoke import _build_client, _login_as_admin

    health = load_personification_module("plugin.personification.webui.routes.health_routes")
    diagnostics = load_personification_module("plugin.personification.core.diagnostics")
    _runtime_context.plugin_config.personification_health_probe_dir = str(tmp_path / "health-probes")

    async def fake_probe(**_kwargs):
        return {"generated_at": 1, "overall": "ok", "summary": {"ok": 1, "warn": 0, "error": 0, "disabled": 0, "info": 0}, "categories": []}

    def failed_cleanup(_path):
        raise OSError("private-media-path")

    monkeypatch.setattr(diagnostics, "run_diagnostics", fake_probe)
    monkeypatch.setattr(health.shutil, "rmtree", failed_cleanup)
    client = _build_client(_runtime_context)
    _login_as_admin(client, _runtime_context)
    with caplog.at_level(logging.WARNING, logger=health.__name__):
        response = client.post(
            "/personification/api/health/video-probe",
            content=b"private-media-content",
            headers={"content-type": "video/mp4", "x-personification-video-filename": "upload.mp4"},
        )
    assert response.status_code == 200
    assert "video_probe_cleanup_failed phase=upload_dir error_type=OSError" in caplog.text
    assert "private-media" not in caplog.text + response.text


def test_sync_cleanup_failure_does_not_replace_setup_error(tmp_path, monkeypatch, caplog):
    class Conn:
        def close(self):
            raise OSError("private-close-path")

    monkeypatch.setattr(db.sqlite3, "connect", lambda *a, **kw: Conn())
    def configure(_conn):
        raise sqlite3.OperationalError("setup failed")
    monkeypatch.setattr(db, "_configure_connection", configure)
    with caplog.at_level(logging.WARNING, logger=db.__name__):
        with pytest.raises(sqlite3.OperationalError, match="setup failed"):
            db.connect_sync(tmp_path / "state.db")
    assert "mode=sync error_type=OSError" in caplog.text
    assert "private-close-path" not in caplog.text


def test_async_cleanup_failure_does_not_replace_setup_error(tmp_path, monkeypatch, caplog):
    class Conn:
        row_factory = None
        async def execute(self, _sql):
            raise sqlite3.OperationalError("setup failed")
        async def close(self):
            raise OSError("private-close-path")

    async def connect(_path):
        return Conn()
    monkeypatch.setattr(db, "aiosqlite", SimpleNamespace(connect=connect))
    monkeypatch.setattr(db, "get_db_path", lambda: tmp_path / "state.db")
    monkeypatch.setattr(db, "_db", None)
    async def run():
        with pytest.raises(sqlite3.OperationalError, match="setup failed"):
            await db.get_db()
    with caplog.at_level(logging.WARNING, logger=db.__name__):
        asyncio.run(run())
    assert "mode=async error_type=OSError" in caplog.text
    assert "private-close-path" not in caplog.text
    assert db._db is None


def test_group_mute_member_info_internal_type_error_runs_once(monkeypatch):
    group_mute = load_personification_module("plugin.personification.core.group_mute")
    monkeypatch.setattr(group_mute, "get_data_store", lambda: SimpleNamespace(load_sync=lambda _name: {}))
    monkeypatch.setattr(group_mute, "_LOCAL_CACHE", {})

    class Bot:
        self_id = "123"
        def __init__(self):
            self.calls = 0
        async def get_group_member_info(self, *, group_id, user_id, no_cache=True):
            self.calls += 1
            raise TypeError("internal provider bug")

    bot = Bot()
    assert asyncio.run(group_mute.refresh_bot_group_mute_state(bot, "456")) is False
    assert bot.calls == 1


def test_group_mute_legacy_member_info_signature_runs_once(monkeypatch):
    group_mute = load_personification_module("plugin.personification.core.group_mute")
    monkeypatch.setattr(group_mute, "get_data_store", lambda: SimpleNamespace(load_sync=lambda _name: {}))
    monkeypatch.setattr(group_mute, "_LOCAL_CACHE", {})

    class Bot:
        self_id = "123"
        def __init__(self):
            self.calls = 0
        async def get_group_member_info(self, *, group_id, user_id):
            self.calls += 1
            return {"shut_up_timestamp": 0}

    bot = Bot()
    assert asyncio.run(group_mute.refresh_bot_group_mute_state(bot, "456")) is False
    assert bot.calls == 1


def test_single_config_runtime_sync_failure_skips_reload_after_persist(_runtime_context, monkeypatch):
    from .test_webui_smoke import _build_client, _login_as_admin

    routes = load_personification_module("plugin.personification.webui.routes.config_routes")
    runtime = _runtime_context.app_module.get_runtime_context()
    field_name = "personification_agent_max_steps"
    original = 5

    class RejectingConfig(SimpleNamespace):
        def __setattr__(self, name, value):
            if name == field_name:
                raise RuntimeError("private-runtime-sync-detail")
            super().__setattr__(name, value)

    runtime.plugin_config = RejectingConfig(**vars(runtime.plugin_config))
    runtime.plugin_config.__dict__[field_name] = original
    operations = []

    def persist(name, value, _config):
        operations.append(("persist", name, value))
        return {"env_json_path": "isolated-env.json", "dotenv_path": None, "errors": []}

    monkeypatch.setattr(routes.env_writer, "write_both", persist)
    runtime.runtime_bundle = SimpleNamespace(reload_runtime_services=lambda: operations.append(("reload",)))
    client = _build_client(_runtime_context)
    _login_as_admin(client, _runtime_context)
    response = client.post(
        "/personification/api/config/value",
        json={"field_name": field_name, "value": "7"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is False
    assert payload["diagnostic"]["code"] == "config_value_runtime_partial"
    assert payload["diagnostic"]["partial"] is True
    steps = {item["key"]: item for item in payload["diagnostic"]["steps"]}
    assert steps["persist_config"]["status"] == "ok"
    assert steps["runtime_config_sync"]["status"] == "error"
    assert steps["runtime_reload"]["status"] != "ok"
    assert operations == [("persist", field_name, 7)]
    assert runtime.plugin_config.personification_agent_max_steps == original
    assert "private-runtime-sync-detail" not in response.text
