from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from ._loader import load_personification_module


plugin_knowledge_routes = load_personification_module(
    "plugin.personification.webui.routes.plugin_knowledge_routes"
)


class _Store:
    def __init__(self) -> None:
        self.failures: dict[str, Exception] = {}

    def _fail(self, operation: str) -> None:
        if operation in self.failures:
            raise self.failures[operation]

    def load_index_sync(self):
        self._fail("list")
        return {
            "plugins": {
                "demo": {
                    "display_name": "Demo",
                    "summary": "safe summary",
                    "keywords": ["demo"],
                    "category": "local",
                }
            }
        }

    def load_plugin_entry_sync(self, plugin_name: str):
        self._fail("detail")
        if plugin_name == "missing":
            return None
        return {"display_name": "Demo", "summary": "safe detail"}

    def load_runtime_snapshot_sync(self, _plugin_name: str):
        self._fail("runtime_snapshot")
        return {"commands": ["demo"]}

    def load_source_snapshot_sync(self, _plugin_name: str):
        self._fail("source_snapshot")
        return {"source_chunk_count": 1, "source_coverage": {"full_input": True}}

    def search_plugins(self, _query: str, *, top_k: int):
        self._fail("search")
        return ["demo"][:top_k]


def _runtime(store: _Store | None):
    return SimpleNamespace(
        logger=SimpleNamespace(warning=lambda *_args, **_kwargs: None),
        runtime_bundle=SimpleNamespace(
            reply_processor_deps=SimpleNamespace(
                runtime=SimpleNamespace(knowledge_store=store),
            )
        ),
    )


def _endpoint(runtime, path: str):
    router = plugin_knowledge_routes.build_plugin_knowledge_router(runtime=runtime)
    for route in router.routes:
        if route.path == path:
            return route.endpoint
    raise AssertionError(f"route not found: {path}")


def _failure(call) -> tuple[int, dict]:
    with pytest.raises(HTTPException) as caught:
        asyncio.run(call)
    return caught.value.status_code, caught.value.detail


def _assert_contract(report: dict, *, ok: bool) -> None:
    required = {
        "ok",
        "code",
        "phase",
        "title",
        "message",
        "details",
        "steps",
        "retryable",
        "partial",
        "outcome_unknown",
    }
    assert required <= report.keys()
    assert report["ok"] is ok
    assert isinstance(report["details"], list)
    assert isinstance(report["steps"], list)


def test_plugin_knowledge_read_success_preserves_fields_and_adds_diagnostics() -> None:
    runtime = _runtime(_Store())
    list_all = _endpoint(runtime, "/api/plugin-knowledge/list")
    detail = _endpoint(runtime, "/api/plugin-knowledge/detail/{plugin_name}")
    search = _endpoint(runtime, "/api/plugin-knowledge/search")

    listed = asyncio.run(list_all(None))
    assert listed["available"] is True
    assert listed["total"] == 1
    assert listed["plugins"][0]["plugin_name"] == "demo"
    assert listed["diagnostic"]["code"] == "plugin_knowledge_list_loaded"
    _assert_contract(listed, ok=True)

    loaded = asyncio.run(detail("demo", None))
    assert loaded["plugin_name"] == "demo"
    assert loaded["entry"]["display_name"] == "Demo"
    assert "runtime_snapshot" not in loaded
    assert "source_snapshot" not in loaded
    assert "root_path" not in str(loaded)
    assert loaded["snapshot_status"]["runtime"]["status"] == "missing"
    assert loaded["diagnostic"]["code"] == "plugin_knowledge_detail_partial"
    _assert_contract(loaded, ok=True)

    found = asyncio.run(search("demo", 10, None))
    assert found["results"] == ["demo"]
    assert found["items"][0]["plugin_name"] == "demo"
    assert found["items"][0]["summary"] == "safe summary"
    assert found["query"] == "demo"
    assert found["available"] is True
    assert found["diagnostic"]["code"] == "plugin_knowledge_search_complete"
    _assert_contract(found, ok=True)


@pytest.mark.parametrize(
    ("operation", "path", "args", "expected_code"),
    [
        ("list", "/api/plugin-knowledge/list", (None,), "plugin_knowledge_index_read_failed"),
        ("detail", "/api/plugin-knowledge/detail/{plugin_name}", ("demo", None), "plugin_knowledge_entry_read_failed"),
        ("search", "/api/plugin-knowledge/search", ("demo", 10, None), "plugin_knowledge_search_failed"),
    ],
)
def test_plugin_knowledge_read_failures_are_structured_and_safe(
    operation: str,
    path: str,
    args: tuple,
    expected_code: str,
) -> None:
    store = _Store()
    store.failures[operation] = RuntimeError(
        "https://api.example.test/index?access_token=raw-plugin-knowledge-secret"
    )
    endpoint = _endpoint(_runtime(store), path)

    status, report = _failure(endpoint(*args))

    assert status == 500
    assert report["code"] == expected_code
    assert report["trace_id"]
    assert report["details"] == [
        {"label": "异常类型", "value": "RuntimeError", "status": "error"}
    ]
    assert "api.example.test" not in str(report)
    assert "raw-plugin-knowledge-secret" not in str(report)
    _assert_contract(report, ok=False)


def test_plugin_knowledge_detail_never_reads_or_returns_raw_snapshots() -> None:
    class _NoSnapshotStore(_Store):
        def load_runtime_snapshot_sync(self, _plugin_name: str):
            raise AssertionError("detail must not load runtime snapshot")

        def load_source_snapshot_sync(self, _plugin_name: str):
            raise AssertionError("detail must not load source snapshot")

    store = _NoSnapshotStore()
    endpoint = _endpoint(_runtime(store), "/api/plugin-knowledge/detail/{plugin_name}")

    body = asyncio.run(endpoint("demo", None))

    assert body["entry"]["display_name"] == "Demo"
    assert "runtime_snapshot" not in body
    assert "source_snapshot" not in body
    assert body["snapshot_status"]["source"]["status"] == "missing"
    assert body["code"] == "plugin_knowledge_detail_partial"
    assert body["partial"] is True
    _assert_contract(body, ok=True)


def test_plugin_knowledge_store_reports_missing_corrupt_and_too_large(tmp_path: Path) -> None:
    store_mod = load_personification_module("plugin.personification.core.knowledge_store")
    store = store_mod.PluginKnowledgeStore(tmp_path)

    assert store.inspect_index_sync()["status"] == "missing"
    store.index_path.write_text("{broken", encoding="utf-8")
    assert store.inspect_index_sync()["status"] == "corrupt"
    store.index_path.write_text(
        json.dumps({"plugins": {"demo": {"file": "local/demo.json"}}}),
        encoding="utf-8",
    )
    target = store.local_dir / "demo.json"
    target.write_bytes(b"x" * (store.WEBUI_ENTRY_MAX_BYTES + 1))
    assets = store.inspect_plugin_assets_sync("demo")
    assert assets["entry"]["status"] == "too_large"
    assert assets["entry"]["data"] is None


def test_plugin_knowledge_unavailable_and_not_found_are_structured() -> None:
    unavailable_list = _endpoint(_runtime(None), "/api/plugin-knowledge/list")
    unavailable_search = _endpoint(_runtime(None), "/api/plugin-knowledge/search")
    detail = _endpoint(_runtime(_Store()), "/api/plugin-knowledge/detail/{plugin_name}")

    listed = asyncio.run(unavailable_list(None))
    assert listed["plugins"] == []
    assert listed["total"] == 0
    assert listed["available"] is False
    assert listed["code"] == "plugin_knowledge_store_unavailable"
    _assert_contract(listed, ok=False)

    searched = asyncio.run(unavailable_search("demo", 10, None))
    assert searched["results"] == []
    assert searched["query"] == "demo"
    assert searched["available"] is False
    assert searched["code"] == "plugin_knowledge_store_unavailable"

    status, report = _failure(detail("missing", None))
    assert status == 404
    assert report["code"] == "plugin_knowledge_not_found"
    assert report["details"][0]["value"] == "missing"
    _assert_contract(report, ok=False)


def test_plugin_knowledge_section_projection_has_a_hard_response_budget() -> None:
    oversized = {
        "name": "插件功能" * 2_000,
        "summary": "摘要" * 5_000,
        "description": "说明" * 5_000,
        "detail": "详情" * 5_000,
        "keywords": ["关键词" * 1_000 for _ in range(100)],
        "files": [f"module/{index}/" + ("x" * 1_000) for index in range(100)],
        "unknown_large_object": {"secret": "must-not-be-stringified" * 1_000},
    }

    items = [plugin_knowledge_routes._safe_section_item(oversized) for _ in range(100)]
    payload = {
        "plugin_name": "demo",
        "section": "features",
        "items": items,
        "page": 1,
        "page_size": 100,
        "total": 100,
        "has_more": False,
        "truncated": False,
    }
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    assert len(encoded) < 1024 * 1024
    assert "must-not-be-stringified" not in encoded.decode("utf-8")
    assert all(len(json.dumps(item, ensure_ascii=False).encode("utf-8")) < 16 * 1024 for item in items)


@pytest.mark.parametrize(
    ("entry_status", "expected_status", "expected_code"),
    [
        ("missing", 404, "plugin_knowledge_not_found"),
        ("corrupt", 503, "plugin_knowledge_entry_corrupt"),
        ("too_large", 413, "plugin_knowledge_entry_too_large"),
        ("unavailable", 503, "plugin_knowledge_entry_unavailable"),
    ],
)
def test_plugin_knowledge_sections_preserve_entry_diagnostic_status(
    entry_status: str,
    expected_status: int,
    expected_code: str,
) -> None:
    class _AssetStore(_Store):
        async def inspect_plugin_assets(self, _plugin_name: str) -> dict:
            return {
                "entry": {"status": entry_status, "data": None, "size_bytes": 0},
                "runtime": {"status": "missing", "data": None, "size_bytes": 0},
                "source": {"status": "missing", "data": None, "size_bytes": 0},
                "meta": {},
            }

    endpoint = _endpoint(
        _runtime(_AssetStore()),
        "/api/plugin-knowledge/detail/{plugin_name}/sections/{section}",
    )

    status, report = _failure(endpoint("demo", "features", 1, 20, None))

    assert status == expected_status
    assert report["code"] == expected_code
    _assert_contract(report, ok=False)


def test_plugin_knowledge_section_projection_redacts_secrets_and_absolute_paths() -> None:
    projected = plugin_knowledge_routes._safe_section_item(
        {
            "name": "client_secret",
            "default": "raw-client-secret",
            "description": (
                "Authorization: Bearer raw-bearer-token "
                "access_token=raw-query-secret C:\\Users\\tester\\private\\config.env"
            ),
            "location": "D:\\private\\plugin.py",
        }
    )
    rendered = json.dumps(projected, ensure_ascii=False)

    assert "default" not in projected
    assert "raw-client-secret" not in rendered
    assert "raw-bearer-token" not in rendered
    assert "raw-query-secret" not in rendered
    assert "C:\\Users" not in rendered
    assert "D:\\private" not in rendered
    assert "<redacted-path>" in rendered
