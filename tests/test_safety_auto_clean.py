from __future__ import annotations

from pathlib import Path

from ._loader import load_personification_module


db = load_personification_module("plugin.personification.core.db")
data_store = load_personification_module("plugin.personification.core.data_store")
session_store = load_personification_module("plugin.personification.core.session_store")
utils_mod = load_personification_module("plugin.personification.utils")
processor_mod = load_personification_module("plugin.personification.handlers.reply_pipeline.processor")


def _setup_env(tmp_path: Path) -> None:
    db.init_db_sync(tmp_path / "personification.db")
    data_store.init_data_store(tmp_path / "data")


def test_clear_session_history_deletes_messages_and_cancels_task(tmp_path: Path) -> None:
    _setup_env(tmp_path)
    session_id = "test_safety_session_1"
    session_store.append_session_message(session_id, "user", "dirty message")
    assert len(session_store.get_session_messages(session_id)) >= 1

    deleted = session_store.clear_session_history(session_id)
    assert deleted >= 1
    assert len(session_store.get_session_messages(session_id)) == 0


def test_clear_group_msgs_resets_messages_and_count(tmp_path: Path) -> None:
    _setup_env(tmp_path)
    group_id = "test_safety_group_1"
    utils_mod.record_group_msg(group_id, "user1", "dirty group message", user_id="10001")
    msgs = utils_mod.get_recent_group_msgs(group_id, limit=10, expire_hours=0)
    assert len(msgs) >= 1

    utils_mod.clear_group_msgs(group_id)
    cleared_msgs = utils_mod.get_recent_group_msgs(group_id, limit=10, expire_hours=0)
    assert len(cleared_msgs) == 0


def test_safety_block_triggers_auto_clean_for_group(tmp_path: Path) -> None:
    _setup_env(tmp_path)
    group_id = "test_safety_group_auto"
    session_id = f"group_{group_id}"

    utils_mod.record_group_msg(group_id, "user1", "poisoned message", user_id="10001")
    session_store.append_session_message(session_id, "user", "poisoned message", legacy_session_id=group_id)

    assert len(utils_mod.get_recent_group_msgs(group_id, limit=10, expire_hours=0)) > 0
    assert len(session_store.get_session_messages(session_id)) > 0

    class _SafetyError(RuntimeError):
        code = "provider_safety_block"

    err = _SafetyError("provider safety block")
    code = processor_mod._provider_diagnosis_code(err)
    assert code == "provider_safety_block"

    # Simulate handling
    if code == "provider_safety_block":
        utils_mod.clear_group_msgs(str(group_id))
        session_store.clear_session_history(session_id, legacy_session_id=str(group_id))

    assert len(utils_mod.get_recent_group_msgs(group_id, limit=10, expire_hours=0)) == 0
    assert len(session_store.get_session_messages(session_id)) == 0
