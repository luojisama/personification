from __future__ import annotations

import pytest

from ._loader import load_personification_module


turn_media = load_personification_module("plugin.personification.core.turn_media")
parts = load_personification_module("plugin.personification.core.message_parts")


def _ref(index: int, *, owner: str = "u1", role: str = "current", failed: bool = False):
    return turn_media.TurnMediaRef(
        media_id=f"m{index}", ref=f"https://cdn.test/{index}.png", origin="current",
        owner_user_id=owner, message_id=f"msg{index}", kind="image",
        reference_role=role, resolution_code="onebot_image_download_failed" if failed else "",
    )


@pytest.mark.parametrize("count", [1, 2, 4, 8])
def test_provider_image_parts_preserve_selected_occurrence_order(count):
    refs = [_ref(i, owner=f"u{i % 2}") for i in range(count)]
    projection = turn_media.project_visual_media_inputs(refs)
    content = parts.build_user_message_content(text="看图", image_urls=list(projection.transport_refs))
    actual = [item["image_url"]["url"] for item in content if item["type"] == "image_url"]
    assert actual == [f"https://cdn.test/{i}.png" for i in range(count)]
    assert [item.owner_user_id for item in projection.media] == [f"u{i % 2}" for i in range(count)]


def test_alias_dedup_keeps_occurrences_but_provider_sends_one_transport():
    refs = [_ref(1, owner="alice"), _ref(2, owner="bob")]
    projection = turn_media.project_visual_media_inputs(
        refs, transport_aliases={refs[0].ref: "data:image/png;base64,AA==", refs[1].ref: "data:image/png;base64,AA=="},
    )
    content = parts.build_user_message_content(text="x", image_urls=list(projection.transport_refs))
    assert len(projection.media) == 2
    assert projection.transport_refs == ("data:image/png;base64,AA==",)
    assert [item for item in content if item["type"] == "image_url"] == [{"type":"image_url", "image_url":{"url":"data:image/png;base64,AA==", "detail":"auto"}}]


def test_failed_and_unselected_history_media_never_reaches_provider():
    selected = _ref(1)
    failed = _ref(2, failed=True)
    historical = _ref(3, role="background")
    projection = turn_media.project_visual_media_inputs([selected, failed, historical])
    content = parts.build_user_message_content(text="x", image_urls=list(projection.transport_refs))
    actual = [item["image_url"]["url"] for item in content if item["type"] == "image_url"]
    assert actual == [selected.ref]
    assert [item.media_id for item in projection.media] == ["m1", "m2"]
