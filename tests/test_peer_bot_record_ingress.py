from __future__ import annotations

import asyncio

from ._loader import load_personification_module


handler_module = load_personification_module("plugin.personification.handlers.record_message_handler")


class _Event:
    group_id = "g"
    user_id = "peer"
    _personification_peer_bot_source_kind = "peer_bot_reply"


class _Observer:
    def __init__(self) -> None:
        self.calls = 0

    def enqueue_event(self, *_args, **_kwargs):
        self.calls += 1


class _Logger:
    def debug(self, *_args, **_kwargs):
        return None

    def info(self, *_args, **_kwargs):
        return None


def test_peer_bot_reply_is_recorded_but_does_not_feed_human_profiles() -> None:
    favorability = _Observer()
    peer_observer = _Observer()
    profile_calls: list[tuple[str, str]] = []
    summary_calls: list[str] = []

    async def run():
        await handler_module.handle_record_message_event(
            _Event(),
            resolve_record_message=lambda *_args, **_kwargs: ("g", False),
            record_group_msg=lambda *_args, **_kwargs: 1,
            logger=_Logger(),
            create_background_task=lambda _group_id: None,
            create_summary_task=summary_calls.append,
            create_scoped_profile_task=lambda group_id, user_id: profile_calls.append((group_id, user_id)),
            favorability_observer=favorability,
            peer_bot_observer=peer_observer,
        )

    asyncio.run(run())
    assert favorability.calls == 0
    assert peer_observer.calls == 0
    assert profile_calls == []
    assert summary_calls == ["g"]
