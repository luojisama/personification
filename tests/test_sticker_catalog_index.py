from __future__ import annotations

import json

from ._loader import load_personification_module


catalog = load_personification_module(
    "plugin.personification.core.sticker_catalog_index"
)
sticker_library = load_personification_module(
    "plugin.personification.core.sticker_library"
)


class _Store:
    def __init__(self) -> None:
        self.data = {}

    def load_sync(self, name):  # noqa: ANN001
        return self.data.get(name, {})

    def save_sync(self, name, value):  # noqa: ANN001
        self.data[name] = value


def test_sticker_catalog_rebuild_persists_request_time_index(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    store = _Store()
    monkeypatch.setattr(catalog, "get_data_store", lambda: store)
    (tmp_path / "a.png").write_bytes(b"png")
    manifest_path = sticker_library.sticker_metadata_path(tmp_path)
    manifest_path.write_text(
        json.dumps(
            {
                "a.png": {
                    "description": "开心挥手",
                    "mood_tags": ["开心"],
                    "scene_tags": ["问候"],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    rebuilt = catalog.rebuild_sticker_catalog_index(tmp_path)
    loaded = catalog.load_sticker_catalog_index(tmp_path)

    assert rebuilt["items"][0]["filename"] == "a.png"
    assert loaded["items"][0]["description"] == "开心挥手"
    assert loaded["items"][0]["labeled"] is True
    assert loaded["stale"] is False


def test_sticker_catalog_marks_directory_change_stale_without_discarding_known_good(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    store = _Store()
    monkeypatch.setattr(catalog, "get_data_store", lambda: store)
    (tmp_path / "a.png").write_bytes(b"png")
    catalog.rebuild_sticker_catalog_index(tmp_path)
    (tmp_path / "b.png").write_bytes(b"new")

    loaded = catalog.load_sticker_catalog_index(tmp_path)

    assert loaded["stale"] is True
    assert [item["filename"] for item in loaded["items"]] == ["a.png"]
