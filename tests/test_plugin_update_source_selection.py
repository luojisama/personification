from __future__ import annotations

import asyncio
from types import SimpleNamespace

from ._loader import load_personification_module


manager = load_personification_module(
    "plugin.personification.core.plugin_update_manager"
)


def _config(*mirrors: str) -> SimpleNamespace:
    return SimpleNamespace(
        personification_git_mirror_prefixes=list(mirrors),
        personification_git_mirror_prefix="",
        personification_git_probe_timeout_seconds=8,
    )


def _snapshot() -> dict:
    return {
        "available": True,
        "update_supported": True,
        "repo_root": "C:/repo",
        "source": {
            "normalized_remote_url": "https://github.com/example/project.git",
            "remote_name": "origin",
            "branch": "main",
            "upstream": "origin/main",
        },
        "local": {"hash": "a" * 40},
        "dirty": False,
    }


def test_five_source_benchmark_ranks_official_with_real_git_result_contract(
    monkeypatch,
) -> None:  # noqa: ANN001
    manager._BENCHMARK_CACHE.clear()
    cfg = _config(
        "https://ghproxy.com",
        "https://gh-proxy.com",
        "https://mirror.ghproxy.com",
        "https://hub.gitmirror.com",
    )
    latencies = {
        "mirror_1": 80,
        "mirror_2": 30,
        "mirror_3": 110,
        "mirror_4": 55,
        "official": 12,
    }

    async def _local(*_args, **_kwargs):
        return _snapshot()

    async def _probe(source, *, cwd, timeout):  # noqa: ANN001
        assert cwd == "C:/repo"
        assert timeout == 8
        return {
            **source,
            "state": "succeeded",
            "latency_ms": latencies[source["source_id"]],
            "rank": None,
            "checked_at": 1,
            "expires_at": 61,
            "diagnostic_code": "git_source_probe_succeeded",
            "_order": source["order"],
            "_repo_url": source["repo_url"],
        }

    monkeypatch.setattr(manager, "_local_snapshot", _local)
    monkeypatch.setattr(manager, "_probe_source", _probe)
    result = asyncio.run(
        manager.benchmark_update_sources(plugin_config=cfg, allow_cached=False)
    )

    assert len(result["probes"]) == 5
    assert result["ranked_source_ids"] == [
        "official",
        "mirror_2",
        "mirror_4",
        "mirror_1",
        "mirror_3",
    ]
    assert next(item for item in result["probes"] if item["source_id"] == "official")["rank"] == 1


def test_benchmark_cache_is_reused_for_sixty_seconds_then_expires(
    monkeypatch,
) -> None:  # noqa: ANN001
    manager._BENCHMARK_CACHE.clear()
    clock = [1_000.0]
    calls = 0

    async def _local(*_args, **_kwargs):
        return _snapshot()

    async def _probe(source, **_kwargs):  # noqa: ANN001
        nonlocal calls
        calls += 1
        return {
            **source,
            "state": "succeeded",
            "latency_ms": source["order"] + 1,
            "rank": None,
            "checked_at": clock[0],
            "expires_at": clock[0] + 60,
            "diagnostic_code": "git_source_probe_succeeded",
            "_order": source["order"],
            "_repo_url": source["repo_url"],
        }

    monkeypatch.setattr(manager, "_local_snapshot", _local)
    monkeypatch.setattr(manager, "_probe_source", _probe)
    monkeypatch.setattr(manager.time, "time", lambda: clock[0])
    cfg = _config("https://mirror.example")

    first = asyncio.run(manager.benchmark_update_sources(plugin_config=cfg))
    second = asyncio.run(
        manager.benchmark_update_sources(plugin_config=cfg, allow_cached=True)
    )
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert calls == 2

    clock[0] += 61
    third = asyncio.run(
        manager.benchmark_update_sources(plugin_config=cfg, allow_cached=True)
    )
    assert third["cache_hit"] is False
    assert calls == 4


def test_ranked_fetch_falls_back_only_for_network_errors(monkeypatch) -> None:  # noqa: ANN001
    calls: list[str] = []

    async def _git(args, *, extra_config=None, **_kwargs):  # noqa: ANN001
        source = "official" if not extra_config else "mirror_1" if "one" in extra_config[0] else "mirror_2"
        calls.append(source)
        if source == "mirror_1":
            return 1, "", "could not resolve host: mirror-one"
        return 0, "fetched", ""

    monkeypatch.setattr(manager, "_run_git_command", _git)
    cfg = _config("https://one.example", "https://two.example")
    result = asyncio.run(
        manager._fetch_ranked(
            snapshot=_snapshot(),
            benchmark={"ranked_source_ids": ["mirror_1", "mirror_2", "official"]},
            plugin_config=cfg,
        )
    )
    assert result["ok"] is True
    assert result["selected_source_id"] == "mirror_2"
    assert calls == ["mirror_1", "mirror_2"]


def test_ranked_fetch_stops_on_deterministic_permission_error(monkeypatch) -> None:  # noqa: ANN001
    calls = 0

    async def _git(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return 1, "", "repository not found or permission denied"

    monkeypatch.setattr(manager, "_run_git_command", _git)
    result = asyncio.run(
        manager._fetch_ranked(
            snapshot=_snapshot(),
            benchmark={"ranked_source_ids": ["official", "mirror_1"]},
            plugin_config=_config("https://mirror.example"),
        )
    )
    assert result["ok"] is False
    assert result["deterministic"] is True
    assert result["diagnostic_code"] == "git_source_permission_failed"
    assert calls == 1


def test_official_source_remains_when_admin_clears_all_mirrors() -> None:
    candidates = manager._source_candidates(
        _config(), "https://github.com/example/project.git"
    )
    assert [(item["source_id"], item["kind"]) for item in candidates] == [
        ("official", "official")
    ]


def test_github_mirrors_are_inapplicable_for_non_github_origin(monkeypatch) -> None:  # noqa: ANN001
    calls = 0

    async def _git(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return 0, "a" * 40 + "\tHEAD", ""

    monkeypatch.setattr(manager, "_run_git_command", _git)
    candidates = manager._source_candidates(
        _config("https://ghproxy.com"), "https://gitlab.example/team/project.git"
    )
    mirror = asyncio.run(manager._probe_source(candidates[0], cwd="C:/repo", timeout=8))
    official = asyncio.run(manager._probe_source(candidates[1], cwd="C:/repo", timeout=8))

    assert mirror["state"] == "inapplicable"
    assert mirror["diagnostic_code"] == "git_source_mirror_inapplicable"
    assert official["state"] == "succeeded"
    assert calls == 1


def test_public_update_status_drops_credentials_tokens_and_local_paths(
    monkeypatch,
) -> None:  # noqa: ANN001
    snapshot = _snapshot()
    snapshot.update(
        {
            "repo_root": "D:/secret/worktree",
            "plugin_root": "D:/secret/worktree/plugin",
            "dirty_preview": ["?? private-key.txt"],
        }
    )
    snapshot["source"]["normalized_remote_url"] = (
        "https://deploy:password@github.com/example/project.git?token=secret"
    )
    snapshot["source"]["remote_url"] = manager._redact_remote_url(
        snapshot["source"]["normalized_remote_url"]
    )

    async def _local(*_args, **_kwargs):
        return snapshot

    async def _remote(*_args, **_kwargs):
        return {
            "remote": {"hash": "a" * 40, "short_hash": "a" * 7},
            "ahead": 0,
            "behind": 0,
            "pending_history": [],
            "update_available": False,
        }

    monkeypatch.setattr(manager, "_local_snapshot", _local)
    monkeypatch.setattr(manager, "_remote_snapshot", _remote)
    status = asyncio.run(manager.get_plugin_update_status(refresh=False))
    serialized = repr(status)

    assert "password" not in serialized
    assert "token=secret" not in serialized
    assert "D:/secret" not in serialized
    assert "private-key" not in serialized
    assert "normalized_remote_url" not in serialized
