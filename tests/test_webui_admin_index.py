from __future__ import annotations

import asyncio
import time

import pytest

from ._loader import load_personification_module


admin_index_mod = load_personification_module(
    "plugin.personification.core.webui_admin_index"
)
v2_routes = load_personification_module(
    "plugin.personification.webui.routes.v2_routes"
)


def test_admin_projection_pages_large_datasets_in_sql_under_local_p95_target(
    tmp_path, monkeypatch
) -> None:  # noqa: ANN001
    monkeypatch.setattr(admin_index_mod, "get_data_dir", lambda _config=None: tmp_path)
    index = admin_index_mod.WebUIAdminIndex()
    personas = [
        {
            "user_id": str(10_000_000 + number),
            "nickname": f"用户 {number}",
            "recent_group_id": str(20_000 + number % 500),
            "group_ids": [str(20_000 + number % 500)],
            "favorability_score": number % 101,
            "favorability_level": "亲近" if number % 2 else "普通",
            "updated_at": float(number),
            "source": "cache",
            # 投影必须忽略完整画像正文。
            "profile_text": f"secret-profile-{number}",
        }
        for number in range(10_000)
    ]
    groups = [
        {
            "group_id": str(20_000 + number),
            "group_name": f"群 {number}",
            "enabled": number % 2 == 0,
            "membership_state": "confirmed" if number % 3 else "configured",
            "bot_ids": ["2534316454"],
            "member_count": number + 20,
            "last_active_at": float(number),
            "source": "group_config",
        }
        for number in range(500)
    ]
    stickers = [
        {
            "filename": f"sticker-{number:05d}.webp",
            "description": f"表情 {number}",
            "mood_tags": ["开心" if number % 2 else "平静"],
            "scene_tags": ["日常"],
            "size_bytes": number + 1024,
            "modified_at": float(number),
            "labeled": True,
        }
        for number in range(10_000)
    ]
    status = index.rebuild(personas=personas, groups=groups, stickers=stickers)
    assert status["counts"] == {
        "persona_summary": 10_000,
        "group_summary": 500,
        "sticker_summary": 10_000,
    }

    durations: list[float] = []
    for page in range(1, 31):
        started = time.perf_counter()
        result = index.personas_page(
            page=page,
            page_size=20,
            group_id=str(20_000 + page % 500),
            sort_by="favorability",
            direction="desc",
        )
        durations.append(time.perf_counter() - started)
        assert len(result["items"]) <= 20
        assert "profile_text" not in repr(result)
    p95 = sorted(durations)[int(len(durations) * 0.95) - 1]
    assert p95 < 0.300

    sticker_page = index.stickers_page(
        page=250,
        page_size=20,
        search="表情",
        sort_by="modified_at",
        direction="desc",
    )
    assert sticker_page["total"] == 10_000
    assert len(sticker_page["items"]) == 20
    assert sticker_page["items"][0]["filename"].endswith(".webp")


def test_admin_projection_rejects_untrusted_sort_fields(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(admin_index_mod, "get_data_dir", lambda _config=None: tmp_path)
    index = admin_index_mod.WebUIAdminIndex()
    with pytest.raises(ValueError, match="persona_sort_invalid"):
        index.personas_page(
            page=1,
            page_size=20,
            sort_by="updated_at; DROP TABLE persona_summary",
        )
    with pytest.raises(ValueError, match="group_sort_invalid"):
        index.groups_page(
            page=1,
            page_size=20,
            sort_by="group_id DESC; SELECT 1",
        )


def test_admin_projection_incrementally_tracks_sticker_writes(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(admin_index_mod, "get_data_dir", lambda _config=None: tmp_path)
    index = admin_index_mod.WebUIAdminIndex()

    index.upsert_sticker({
        "filename": "hello.webp",
        "description": "挥手",
        "mood_tags": ["开心"],
        "scene_tags": ["问候"],
        "size_bytes": 123,
        "modified_at": 42,
        "labeled": True,
    })
    page = index.stickers_page(page=1, page_size=20, search="挥手")
    assert page["total"] == 1
    assert page["items"][0]["filename"] == "hello.webp"
    assert index.status()["detail_code"] == "admin_index_incremental_sticker"

    index.delete_sticker("hello.webp")
    assert index.stickers_page(page=1, page_size=20)["total"] == 0
    assert index.status()["detail_code"] == "admin_index_incremental_sticker_delete"

    index.mark_stale("sticker_manifest_rescan_pending")
    assert index.status()["state"] == "stale"


def test_admin_index_first_page_queues_rebuild_without_waiting_for_full_scan(
    monkeypatch,
) -> None:  # noqa: ANN001
    class _Index:
        def status(self):
            return {
                "state": "empty",
                "indexed_at": 0,
                "detail_code": "admin_index_empty",
            }

    queued = 0

    async def _queue(_runtime, *, force=False):  # noqa: ANN001
        nonlocal queued
        queued += 1
        return {"state": "queued"}

    monkeypatch.setattr(v2_routes, "_get_admin_index", lambda _runtime: _Index())
    monkeypatch.setattr(v2_routes, "_queue_admin_index_rebuild", _queue)
    result = asyncio.run(v2_routes._admin_index(object()))

    assert isinstance(result, _Index)
    assert queued == 1
