from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

from ._loader import load_personification_module


processor = load_personification_module("plugin.personification.handlers.reply_pipeline.processor")


class _Text:
    type = "text"
    def __init__(self, text: str) -> None:
        self.data = {"text": text}


class _At:
    type = "at"
    def __init__(self, qq: str) -> None:
        self.data = {"qq": qq}


class _GroupEvent:
    group_id = "g"
    user_id = "trigger"
    message_id = "trigger-msg"
    sender = SimpleNamespace(nickname="触发者", card="", role="member")
    def __init__(self) -> None:
        self.message = [_At("bot"), _Text("触发")]
    def get_plaintext(self) -> str:
        return "触发"


def test_normal_outer_prepares_batch_record_without_fake_trigger_user() -> None:
    events = [
        {"message_id": "m1", "user_id": "u1", "sender_name": "甲", "text": "第一句"},
        {"message_id": "m2", "user_id": "u2", "sender_name": "乙", "text": "第二句", "is_direct_mention": True},
    ]
    content, speaker, metadata = processor.prepare_incoming_history_record(
        is_private_session=False, batched_events=events, fallback_content="trigger-only",
        fallback_speaker="触发者", image_urls=[], image_detail="auto",
        trigger_user_id="trigger", trigger_message_id="trigger-msg", trigger_group_id="g",
    )
    assert metadata["source_kind"] == "user_batch"
    assert speaker == "多人群聊批次"
    assert "user_id" not in metadata
    assert metadata["trigger_user_id"] == "trigger"
    assert metadata["trigger_message_id"] == "trigger-msg"
    assert "不可信群聊数据" in str(content)
    assert "甲|uid=u1" in str(content)
    assert "乙|uid=u2" in str(content)


def test_agent_preparation_shares_group_envelope_and_keeps_single_private_shape() -> None:
    events = [
        {"message_id": "m1", "user_id": "u1", "sender_name": "甲", "text": "第一", "reply_to_user_id": "old"},
        {"message_id": "m2", "user_id": "u2", "sender_name": "乙", "text": "第二"},
    ]
    kwargs = dict(is_private_session=False, batched_events=events, fallback_content="trigger", fallback_speaker="触发", image_urls=[], image_detail="auto", trigger_user_id="u2", trigger_message_id="m2", trigger_group_id="g")
    normal, _, _ = processor.prepare_incoming_history_record(**kwargs)
    agent = processor.prepare_agent_incoming_content(**kwargs)
    assert str(agent) == str(normal)
    assert str(agent).find("甲|uid=u1|回复用户=old") < str(agent).find("乙|uid=u2")
    private = processor.prepare_agent_incoming_content(**{**kwargs, "is_private_session": True, "batched_events": events[:1]})
    assert private == "trigger"


def test_process_response_logic_starts_trace_before_flushing_buffer_diagnostics(monkeypatch) -> None:
    events: list[tuple[str, dict]] = []
    class Trace:
        started = False
        @staticmethod
        def current_trace_id(): return ""
        @staticmethod
        def start_trace(**kwargs): Trace.started = True; events.append(("start", kwargs)); return "trace-fixed"
        @staticmethod
        def set_current_trace_id(value): events.append(("set", {"trace_id": value})); return object()
        @staticmethod
        def record_stage(**kwargs): assert Trace.started; events.append((kwargs["key"], kwargs))
        @staticmethod
        def get_trace(_trace_id): return {"outcome": "ok"}
        @staticmethod
        def reset_current_trace_id(_token): events.append(("reset", {}))
        @staticmethod
        def finish_trace(**_kwargs): return None
    monkeypatch.setitem(sys.modules, "plugin.personification.core.reply_turn_trace", Trace)
    core = sys.modules.get("plugin.personification.core")
    if core is not None: monkeypatch.setattr(core, "reply_turn_trace", Trace, raising=False)
    async def noop(*_args, **_kwargs): return None
    monkeypatch.setattr(processor, "_process_response_logic_impl", noop)
    runtime = SimpleNamespace(user_policy_gate=None, plugin_config=SimpleNamespace(personification_turn_trace_enabled=True), logger=SimpleNamespace())
    deps = processor.ReplyProcessorDeps(session=SimpleNamespace(), persona=SimpleNamespace(), runtime=runtime, types=SimpleNamespace())
    event = _GroupEvent(); event.group_id = "456"; event.user_id = "123"; event.message_id = "789"
    state = {"buffer_trace_diagnostics": [{"code": "dequeue", "count": 2, "generation": 3, "wait_ms": 4}], "sensitive": "正文 session QQ 456 789 D:\\secret token"}
    asyncio.run(processor.process_response_logic(SimpleNamespace(), event, state, deps))
    names = [name for name, _ in events]
    assert names.index("start") < names.index("buffer_diagnostic") < names.index("ingress")
    buffer = next(value for name, value in events if name == "buffer_diagnostic")
    assert buffer["trace_id"] == "trace-fixed" and "code=dequeue count=2 generation=3 wait_ms=4" == buffer["detail"]
    assert "buffer_trace_diagnostics" not in state
    assert all(marker not in repr(buffer) for marker in ("正文", "session", "456", "789", "D:\\secret", "token"))


def test_buffer_failure_trace_uses_existing_trace_id_only(monkeypatch) -> None:
    stages = []
    trace = SimpleNamespace(record_stage=lambda **kwargs: stages.append(kwargs))
    monkeypatch.setitem(sys.modules, "plugin.personification.core.reply_turn_trace", trace)
    core = sys.modules.get("plugin.personification.core")
    if core is not None: monkeypatch.setattr(core, "reply_turn_trace", trace, raising=False)
    buffer = load_personification_module("plugin.personification.handlers.reply_buffer")
    buffer._record_buffer_failure_trace({"reply_trace_id": "trace-fixed"}, "processing_failure", count=2, generation=3, wait_ms=4)
    buffer._record_buffer_failure_trace({}, "processing_failure", count=2, generation=3, wait_ms=4)
    assert len(stages) == 1 and stages[0]["trace_id"] == "trace-fixed"


def test_normal_and_yaml_block_markers_are_silent_without_fixed_refusal() -> None:
    root = Path(__file__).resolve().parents[1]
    normal = (root / "handlers" / "reply_pipeline" / "processor.py").read_text(encoding="utf-8")
    yaml = (root / "handlers" / "yaml_pipeline" / "processor.py").read_text(encoding="utf-8")

    assert 'reply_content = "这个我不能接。"' not in normal
    assert 'reply_content = "这个我不能接。"' not in yaml
    assert "当前静默结束本轮" in normal
    assert "当前静默结束本轮" in yaml
