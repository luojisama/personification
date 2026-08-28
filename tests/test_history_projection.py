from ._loader import load_personification_module


projection = load_personification_module("plugin.personification.core.history_projection")
session_store = load_personification_module("plugin.personification.core.session_store")


def test_group_batch_preserves_each_speaker_and_marks_untrusted() -> None:
    content, metadata = projection.build_group_batch_history([
        {"message_id": "1", "user_id": "11", "sender_name": "白咲雫", "text": "丢数据了"},
        {"message_id": "2", "user_id": "22", "sender_name": "流影", "text": "后续消息", "is_direct_mention": True},
    ])
    assert "不可信群聊数据" in content
    assert "白咲雫|uid=11" in content and "流影|uid=22|@Bot" in content
    assert metadata["speaker"] == "多人群聊批次"
    assert metadata["message_ids"] == ["1", "2"]
    assert metadata["source_kind"] == "user_batch"


def test_group_batch_hard_limits_include_visible_markers() -> None:
    content, metadata = projection.build_group_batch_history([
        {"message_id": str(index), "user_id": str(index), "sender_name": "n", "text": "x" * 10_000}
        for index in range(10)
    ])
    assert len(content) <= 12_000
    assert metadata["truncation"]["truncated_events"] >= 1
    assert "original_chars" in metadata["truncation"]["events"][0]


def test_confirmed_sticker_projection_never_leaks_path() -> None:
    rendered = projection.build_confirmed_outbound_history(
        "", sticker_metadata={"scene": "角色淋雨", "emotion": "低落", "path": "D:/secret/a.gif"}, sticker_confirmed=True
    )
    assert rendered == "[发送表情包：低落，角色淋雨]"
    assert "D:/" not in rendered


def test_part_receipt_confirmation_distinguishes_sent_unknown_failed_and_legacy() -> None:
    class Receipt:
        def __init__(self, status: str, message_id: str | None = None) -> None:
            self.status, self.message_id = status, message_id
    assert projection.is_confirmed_send_result(Receipt("sent", None))
    assert not projection.is_confirmed_send_result(Receipt("unknown", "text-first"))
    assert not projection.is_confirmed_send_result(Receipt("failed", "x"))
    assert projection.is_confirmed_send_result({"message_id": "legacy-ok"})
    assert not projection.is_confirmed_send_result(None)


def test_sticker_metadata_uses_semantics_but_not_name_or_url() -> None:
    rendered = projection.build_confirmed_outbound_history(
        "文字", sticker_metadata={"action": "趴键盘", "ocr": "好累", "emotion": "低落", "filename": "secret.gif", "url": "https://secret"}, sticker_confirmed=True
    )
    assert "趴键盘" in rendered and "低落" in rendered
    assert "secret" not in rendered


def test_compress_retry_uses_injectable_nonblocking_sleep(monkeypatch) -> None:
    import asyncio
    waits: list[float] = []
    scheduled: list[str] = []
    async def fake_sleep(value: float) -> None:
        waits.append(value)
    monkeypatch.setattr(session_store, "_compress_sleep", fake_sleep)
    monkeypatch.setattr(session_store, "_schedule_compress", lambda key: scheduled.append(key))
    async def run() -> None:
        session_store._schedule_compress_retry("group_test", 1)
        await asyncio.sleep(0)
    asyncio.run(run())
    assert waits == [30.0]
    assert scheduled == ["group_test"]


def test_catalog_metadata_adapter_excludes_file_identity() -> None:
    meta = projection.sticker_history_metadata({"description": "角色淋雨", "ocr_text": "好冷", "mood_tags": ["低落"], "scene_tags": ["下雨"], "file_path": "D:/x.gif"})
    assert meta == {"action": "角色淋雨", "ocr": "好冷", "emotion": "低落", "scene": "下雨"}


def test_confirmed_history_never_invents_image_and_keeps_each_confirmed_sticker() -> None:
    assert projection.build_confirmed_outbound_history("", image_confirmed=False) == ""
    assert projection.build_confirmed_outbound_history("", image_confirmed=True) == "[发送了一张图片]"
    projected = projection.build_confirmed_outbound_history(
        "文字", confirmed_sticker_metadata=[{"action": "淋雨"}, {"emotion": "趴键盘"}],
    )
    assert projected == "文字 [发送表情包：淋雨] [发送表情包：趴键盘]"


def test_single_event_truncation_is_counted_and_safe_fields_are_visible() -> None:
    content, metadata = projection.build_group_batch_history([{
        "message_id": "m", "user_id": "u", "sender_name": "甲", "text": "x" * 2100,
        "sender_role": "admin", "is_current_trigger": True,
        "media": [{"kind": "image", "url": "file:///secret/path"}],
    }])
    assert metadata["truncation"]["truncated_events"] == 1
    assert "群身份=admin" in content and "当前触发" in content and "媒体=image" in content
    assert "secret/path" not in content


def test_batch_id_fallback_includes_event_time_and_normalized_body_digest() -> None:
    first = [{"user_id": "1", "event_time": "42", "sender_name": "甲", "text": "第一句\n正文"}]
    same = [{"user_id": "1", "event_time": "42", "sender_name": "甲", "text": "第一句 正文"}]
    changed = [{"user_id": "1", "event_time": "42", "sender_name": "甲", "text": "另一句"}]
    _, first_meta = projection.build_group_batch_history(first)
    _, same_meta = projection.build_group_batch_history(same)
    _, changed_meta = projection.build_group_batch_history(changed)
    assert first_meta["batch_id"].startswith("batch:")
    assert first_meta["batch_id"] == same_meta["batch_id"]
    assert first_meta["batch_id"] != changed_meta["batch_id"]


def test_batch_truncation_reports_total_and_safe_unknown_media_kind() -> None:
    events = [
        {"user_id": "u", "sender_name": "恶意[昵称]\n", "text": "x" * 2000,
         "media": [{"kind": "evil|inject://secret"}]},
    ] + [{"user_id": str(index), "sender_name": "乙", "text": "y" * 2000} for index in range(6)]
    content, metadata = projection.build_group_batch_history(events)
    truncation = metadata["truncation"]
    assert len(content) <= 12000
    assert truncation["original_chars"] == 14000
    assert truncation["rendered_chars"] == len(content)
    assert truncation["batch_truncated"] is True
    assert "evil" not in content and "媒体=媒体" in content
    assert "[整批已截断" in content


def test_catalog_lookup_accepts_filename_or_stem_without_identity_leak() -> None:
    catalog = {
        "rain.gif": {"description": "角色淋雨", "mood_tags": ["低落"]},
        "keyboard.webp": {"description": "趴键盘", "ocr_text": "好累"},
    }
    rain = projection.lookup_sticker_history_metadata(catalog, "rain")
    keyboard = projection.lookup_sticker_history_metadata(catalog, "keyboard.webp")
    rendered = projection.build_confirmed_outbound_history("", confirmed_sticker_metadata=[rain, keyboard])
    assert "角色淋雨" in rendered and "趴键盘" in rendered
    assert ".gif" not in rendered and ".webp" not in rendered


def test_sticker_semantics_reject_paths_urls_and_filename_only_values() -> None:
    meta = projection.sticker_history_metadata({
        "description": r"C:\secret\x.gif", "ocr_text": "file:///private/a.png",
        "mood_tags": ["https://example.invalid/sticker.webp"], "scene_tags": ["rain.gif"],
    })
    assert meta == {"action": "", "ocr": "", "emotion": "", "scene": ""}
    assert projection.build_confirmed_outbound_history("", confirmed_sticker_metadata=[meta]) == "[发送了一个表情包]"
    assert projection.build_confirmed_outbound_history("", confirmed_sticker_metadata=[{"action": "角色淋雨", "emotion": "低落"}]) == "[发送表情包：角色淋雨，低落]"
    assert projection.build_confirmed_outbound_history("", confirmed_sticker_metadata=[{"action": "趴下/无力"}]) == "[发送表情包：趴下/无力]"
    assert projection.build_confirmed_outbound_history("", confirmed_sticker_metadata=[{"action": "动作 rain.gif"}]) == "[发送了一个表情包]"
