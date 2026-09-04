from __future__ import annotations

import asyncio
import os
import stat
import subprocess
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module


qzone_service = load_personification_module("plugin.personification.core.qzone_service")


class _Logger:
    def info(self, *_args, **_kwargs) -> None:  # noqa: ANN002, ANN003
        return None

    def warning(self, *_args, **_kwargs) -> None:  # noqa: ANN002, ANN003
        return None

    def error(self, *_args, **_kwargs) -> None:  # noqa: ANN002, ANN003
        return None


@pytest.fixture(autouse=True)
def _reset_auth_states():  # noqa: ANN202
    with qzone_service._AUTH_STATE_LOCK:
        qzone_service._AUTH_STATES.clear()
    yield
    with qzone_service._AUTH_STATE_LOCK:
        qzone_service._AUTH_STATES.clear()


async def _probe_ok(_cookie: str, _qq: str, _p_skey: str) -> tuple[bool, str]:
    return True, "ok"


def _config(tmp_path, legacy_cookie: str = "") -> SimpleNamespace:  # noqa: ANN001
    return SimpleNamespace(
        personification_data_dir=str(tmp_path),
        personification_qzone_cookie=legacy_cookie,
        qzone_cookie="",
    )


def test_credentials_are_isolated_per_exact_bot_and_empty_status_is_aggregate(tmp_path) -> None:  # noqa: ANN001
    config = _config(tmp_path)
    first = "uin=o10001; p_skey=first-secret;"
    second = "uin=o10002; p_skey=second-secret;"

    assert asyncio.run(
        qzone_service.install_qzone_cookie(
            cookie=first,
            expected_bot_id="10001",
            plugin_config=config,
            logger=_Logger(),
            source="onebot",
            probe=_probe_ok,
        )
    ) == (True, "ok")
    assert asyncio.run(
        qzone_service.install_qzone_cookie(
            cookie=second,
            expected_bot_id="10002",
            plugin_config=config,
            logger=_Logger(),
            source="qrcode",
            probe=_probe_ok,
        )
    ) == (True, "ok")

    first_context = qzone_service._resolve_qzone_context(config, "10001")
    second_context = qzone_service._resolve_qzone_context(config, "10002")
    assert first_context[0] is second_context[0] is True
    assert first_context[2]["qq"] == "10001"
    assert second_context[2]["qq"] == "10002"
    assert first_context[2]["cookie"] != second_context[2]["cookie"]
    store = qzone_service.QzoneCredentialStore(config)
    assert store.path == tmp_path / "qzone" / "credentials.secret.json"
    if os.name == "nt":
        acl = subprocess.run(
            ["icacls", str(store.path)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout
        assert "(I)" not in acl
        assert "SYSTEM" not in acl
        assert "Administrators" not in acl
    else:
        assert stat.S_IMODE(store.path.stat().st_mode) & 0o077 == 0
    assert not list(store.path.parent.glob("*.tmp"))
    assert store.describe("10001")["identity_verification"] == "verified"
    assert qzone_service.get_qzone_auth_status("10003", plugin_config=config)["status"] == "unknown"

    aggregate = qzone_service.get_qzone_auth_status(plugin_config=config)
    assert aggregate["status"] == "aggregate"
    assert aggregate["aggregate"] is True
    assert aggregate["bot_count"] == 2
    assert set(aggregate["by_bot"]) == {"10001", "10002"}
    assert "p_skey" not in str(aggregate)
    assert "first-secret" not in str(aggregate)
    assert "second-secret" not in str(aggregate)


def test_legacy_cookie_migrates_only_for_one_matching_connected_bot(tmp_path) -> None:  # noqa: ANN001
    config = _config(tmp_path, "uin=o10001; p_skey=legacy-secret;")

    migrated = asyncio.run(
        qzone_service.migrate_legacy_qzone_cookie(
            plugin_config=config,
            connected_bot_ids=("10001",),
            logger=_Logger(),
            probe=_probe_ok,
        )
    )

    store = qzone_service.QzoneCredentialStore(config)
    assert migrated == (True, "ok")
    assert store.describe("10001")["configured"] is True
    assert store.describe("10001")["source"] == "legacy_config"
    assert store.describe("10001")["identity_verification"] == "verified"
    assert config.personification_qzone_cookie == "uin=o10001; p_skey=legacy-secret;"


def test_legacy_cookie_rejects_multi_bot_or_identity_mismatch_without_copying(tmp_path) -> None:  # noqa: ANN001
    config = _config(tmp_path, "uin=o10001; p_skey=legacy-secret;")
    store = qzone_service.QzoneCredentialStore(config)

    multi = asyncio.run(
        qzone_service.migrate_legacy_qzone_cookie(
            plugin_config=config,
            connected_bot_ids=("10001", "10002"),
            logger=_Logger(),
            probe=_probe_ok,
        )
    )
    mismatch = asyncio.run(
        qzone_service.migrate_legacy_qzone_cookie(
            plugin_config=config,
            connected_bot_ids=("10002",),
            logger=_Logger(),
            probe=_probe_ok,
        )
    )

    assert multi == (False, "legacy_cookie_migration_requires_single_connected_bot")
    assert mismatch == (False, "legacy_cookie_migration_account_mismatch")
    assert store.bot_ids() == ()


def test_failed_probe_never_replaces_existing_credential(tmp_path) -> None:  # noqa: ANN001
    config = _config(tmp_path)
    store = qzone_service.QzoneCredentialStore(config)
    store.replace(
        bot_id="10001",
        cookie="uin=o10001; p_skey=old-secret;",
        source="onebot",
    )

    async def failing_probe(_cookie: str, _qq: str, _p_skey: str) -> tuple[bool, str]:
        return False, "probe_failed"

    result = asyncio.run(
        qzone_service.install_qzone_cookie(
            cookie="uin=o10001; p_skey=new-secret;",
            expected_bot_id="10001",
            plugin_config=config,
            logger=_Logger(),
            source="onebot",
            probe=failing_probe,
        )
    )

    assert result == (False, "probe_failed")
    assert store.get("10001") == "uin=o10001; p_skey=old-secret;"


def test_mixed_cookie_uins_fail_identity_validation_before_probe(tmp_path) -> None:  # noqa: ANN001
    config = _config(tmp_path)
    observed = False

    async def should_not_probe(_cookie: str, _qq: str, _p_skey: str) -> tuple[bool, str]:
        nonlocal observed
        observed = True
        return True, "ok"

    result = asyncio.run(
        qzone_service.install_qzone_cookie(
            cookie="uin=o10001; p_uin=o10002; p_skey=secret;",
            expected_bot_id="10001",
            plugin_config=config,
            logger=_Logger(),
            source="onebot",
            probe=should_not_probe,
        )
    )

    assert result == (False, "mixed_uin")
    assert observed is False
    assert qzone_service.QzoneCredentialStore(config).bot_ids() == ()


def test_cookie_carrying_read_probes_disable_environment_proxy(monkeypatch) -> None:  # noqa: ANN001
    client_options: list[dict] = []

    class _Response:
        status_code = 200
        text = '_Callback({"code": 0, "msglist": []})'

    class _Client:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            client_options.append(kwargs)

        async def __aenter__(self):  # noqa: ANN201
            return self

        async def __aexit__(self, *_args) -> None:  # noqa: ANN002, ANN003
            return None

        async def get(self, *_args, **_kwargs):  # noqa: ANN002, ANN003, ANN201
            return _Response()

    monkeypatch.setattr(qzone_service.httpx, "AsyncClient", _Client)

    assert asyncio.run(
        qzone_service._probe_qzone_cookie(
            "uin=o10001; p_skey=fixture-secret;",
            "10001",
            "fixture-secret",
        )
    ) == (True, "ok")
    assert asyncio.run(
        qzone_service._read_qzone_feed_probe(
            cookie="uin=o10001; p_skey=fixture-secret;",
            qq="10001",
            p_skey="fixture-secret",
            target_uin="10001",
        )
    ) == (True, "qzone_feed_read_ok", 0)
    assert len(client_options) == 2
    assert all(options["trust_env"] is False for options in client_options)
    assert all(options["follow_redirects"] is False for options in client_options)
