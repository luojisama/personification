from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module


supplement = load_personification_module("plugin.personification.core.supplement_controller")
relation = load_personification_module("plugin.personification.core.supplement_relation")
reply_buffer = load_personification_module("plugin.personification.handlers.reply_buffer")


def _item(identifier: str, text: str = "x") -> dict[str, str]:
    return {"dedupe_key": identifier, "message_id": identifier, "text": text}


def test_private_supplement_has_fixed_window_and_burst_does_not_extend_it() -> None:
    controller = supplement.SupplementController(
        supplement.SupplementSettings(window_seconds=30, quiet_seconds=1, max_quiet_seconds=3),
        clock=lambda: 10.0,
    )
    entry: dict[str, object] = {}
    controller.begin(entry, generation=8, originals=[_item("first")], now=10)
    first = controller.accept_private(entry, generation=8, candidate=_item("second"), safe_to_supersede=True, now=11)
    assert first.status == "related"
    assert entry["supplement"]["quiet_until"] == 12
    # Later related input only moves the quiet wait inside the original 3s cap.
    second = controller.accept_private(entry, generation=8, candidate=_item("third"), safe_to_supersede=True, now=12.8)
    assert second.accepted_ids == ("third",)
    assert entry["supplement"]["quiet_until"] == pytest.approx(13.8)
    assert controller.accept_private(entry, generation=8, candidate=_item("late"), safe_to_supersede=True, now=40.01).status == "expired"


def test_replacement_pending_accepts_three_private_burst_messages_after_old_fence() -> None:
    controller = supplement.SupplementController(
        supplement.SupplementSettings(window_seconds=30, quiet_seconds=1, max_quiet_seconds=3), clock=lambda: 0,
    )
    entry: dict = {}
    controller.begin(entry, generation=4, originals=[_item("first")], now=10)
    entry["supplement_replacement_generation"] = 4
    first = controller.accept_private(entry, generation=4, candidate=_item("two"), safe_to_supersede=False, now=11)
    second = controller.accept_private(entry, generation=4, candidate=_item("three"), safe_to_supersede=False, now=12)
    third = controller.accept_private(entry, generation=4, candidate=_item("four"), safe_to_supersede=False, now=13.5)
    assert first.status == second.status == third.status == "related"
    assert entry["supplement"]["accepted_ids"] == ("two", "three", "four")
    # Three seconds is measured from the first related supplement, not trigger.
    assert entry["supplement"]["quiet_until"] == 14


def test_private_supplement_never_replaces_after_delivery_or_action() -> None:
    controller = supplement.SupplementController(clock=lambda: 1.0)
    entry: dict[str, object] = {}
    controller.begin(entry, generation=1, originals=[_item("one")], now=1)
    decision = controller.accept_private(entry, generation=1, candidate=_item("two"), safe_to_supersede=False, now=2)
    assert decision.status == "unsafe"
    assert decision.reason == "delivery_or_action_started"


def test_legacy_random_preemption_also_respects_external_action_barrier() -> None:
    entry = {
        "processing": True,
        "current_is_random_chat": True,
        "active_state": {"_external_action_started": True},
    }
    assert not reply_buffer._should_preempt_current_batch(entry, immediate_flush=True)


def test_group_judge_only_accepts_local_candidate_ids_and_related_values() -> None:
    async def run() -> None:
        controller = supplement.SupplementController(clock=lambda: 1.0)
        entry: dict[str, object] = {}
        controller.begin(entry, generation=2, originals=[_item("a")], now=1)

        async def judge(_originals, _candidates):  # noqa: ANN001
            return {"results": [
                {"message_id": "b", "relation": "related"},
                {"message_id": "forged", "relation": "related"},
                {"message_id": "c", "relation": "invalid"},
            ]}

        decision = await controller.judge_group(
            entry, generation=2, originals=[_item("a")], candidates=[_item("b"), _item("c")],
            safe_to_supersede=True, judge=judge, now=1.5,
        )
        assert decision.status == "related"
        assert decision.accepted_ids == ("b",)
        assert entry["supplement"]["accepted_ids"] == ("b",)

    asyncio.run(run())


def test_group_timeout_and_uncertain_keep_candidates_for_next_turn() -> None:
    async def run() -> None:
        controller = supplement.SupplementController(
            supplement.SupplementSettings(relation_timeout_seconds=.01), clock=lambda: 1.0,
        )
        entry: dict[str, object] = {}
        controller.begin(entry, generation=2, originals=[_item("a")], now=1)

        async def slow(_originals, _candidates):  # noqa: ANN001
            await asyncio.sleep(.1)
            return {"results": []}

        decision = await controller.judge_group(
            entry, generation=2, originals=[_item("a")], candidates=[_item("b")],
            safe_to_supersede=True, judge=slow, now=1.5,
        )
        assert decision.status == "uncertain"
        assert decision.reason == "relation_judge_timeout"
        assert entry["supplement"]["accepted_ids"] == ()

    asyncio.run(run())


def test_group_timeout_does_not_wait_for_a_judge_that_ignores_cancellation() -> None:
    async def run() -> None:
        controller = supplement.SupplementController(
            supplement.SupplementSettings(relation_timeout_seconds=.01), clock=lambda: 1.0,
        )
        entry: dict[str, object] = {}
        controller.begin(entry, generation=2, originals=[_item("a")], now=1)
        release = asyncio.Event()

        async def ignores_cancel(_originals, _candidates):  # noqa: ANN001
            try:
                await release.wait()
            except asyncio.CancelledError:
                # A provider client may absorb cancellation while its HTTP
                # request continues.  It must not pin group ingress forever.
                await release.wait()
            return {"results": [{"message_id": "b", "relation": "related"}]}

        decision = await asyncio.wait_for(
            controller.judge_group(
                entry, generation=2, originals=[_item("a")], candidates=[_item("b")],
                safe_to_supersede=True, judge=ignores_cancel, now=1.5,
            ),
            timeout=.25,
        )
        assert (decision.status, decision.reason) == ("uncertain", "relation_judge_timeout")
        assert entry["supplement_detached_judge_tasks"]
        release.set()
        await asyncio.sleep(.01)
        assert not entry["supplement_detached_judge_tasks"]

    asyncio.run(run())


def test_empty_supplement_snapshot_keeps_legacy_buffer_behaviour_disabled() -> None:
    assert not reply_buffer._supplement_settings_from_state({"_supplement_settings": {}}).enabled


def test_generation_mismatch_does_not_accept_late_judgement() -> None:
    async def run() -> None:
        controller = supplement.SupplementController(clock=lambda: 1.0)
        entry: dict[str, object] = {}
        controller.begin(entry, generation=3, originals=[_item("a")], now=1)
        decision = await controller.judge_group(
            entry, generation=2, originals=[_item("a")], candidates=[_item("b")],
            safe_to_supersede=True, judge=lambda *_: {"results": [{"message_id": "b", "relation": "related"}]}, now=1.5,
        )
        assert decision.status == "expired"
        assert decision.reason == "generation_mismatch"

    asyncio.run(run())


def test_private_pre_send_generation_is_cancelled_and_rebuilt_with_both_messages() -> None:
    """The replacement uses the ordinary buffer path, not a second sender."""
    class Seg:
        type = "text"
        def __init__(self, text: str) -> None: self.data = {"text": text}
    class Message(list): pass
    class Segment:
        @staticmethod
        def text(value: str): return Seg(value)
    class Sender: nickname = "u"; card = ""
    class Event:
        def __init__(self, message_id: int, text: str) -> None:
            self.message_id, self.user_id, self.sender = message_id, 7, Sender()
            self.message = Message([Seg(text)])
    class Bot: self_id = "bot"
    class Logger:
        def debug(self, *a, **kw): pass
        info = warning = error = debug

    async def run() -> None:
        buffer: dict = {}
        controller = reply_buffer.ReplyConcurrencyController(session_limit=2, global_limit=2)
        first_started, release = asyncio.Event(), asyncio.Event()
        seen: list[list[str]] = []
        first_snapshot = SimpleNamespace(personification_model="first-model")
        timers: list[asyncio.Task] = []

        async def process(_bot, event, state):
            if event.message_id == 1 and not state.get("batched_events"):
                first_started.set()
                await release.wait()
                return
            seen.append([str(row["message_id"]) for row in state.get("batched_events", [])])
            assert state["_route_config_snapshot"] is first_snapshot

        def start(key, bot, wait):
            task = asyncio.create_task(reply_buffer.run_buffer_timer(
                key, bot, msg_buffer=buffer, process_response_logic=process,
                message_event_cls=Event, message_cls=Message, message_segment_cls=Segment,
                logger=Logger(), delay=wait, concurrency_controller=controller,
                batch_base_wait_seconds=.01, batch_min_wait_seconds=.01, batch_max_wait_seconds=.1,
            ))
            timers.append(task)
            return task

        common = dict(poke_event_cls=type("P", (), {}), message_event_cls=Event,
            group_message_event_cls=type("Group", (), {}), process_response_logic=process,
            msg_buffer=buffer, start_buffer_timer=start, logger=Logger(), concurrency_controller=controller,
            batch_base_wait_seconds=.01, batch_min_wait_seconds=.01, batch_max_wait_seconds=.1,
            supplement_settings={"enabled": True, "window_seconds": 30, "quiet_seconds": .01, "max_quiet_seconds": .03},
            route_config_snapshot=first_snapshot,
        )
        first = asyncio.create_task(reply_buffer.handle_reply_event(Bot(), Event(1, "first"), {}, **common))
        await asyncio.wait_for(first_started.wait(), timeout=1)
        await reply_buffer.handle_reply_event(Bot(), Event(2, "also"), {}, **common)
        await asyncio.wait_for(first, timeout=1)
        await asyncio.gather(*timers, return_exceptions=True)
        assert seen == [["1", "2"]]

    asyncio.run(run())


def test_replacement_starts_before_a_provider_that_ignores_cancellation_finishes() -> None:
    class Seg:
        type = "text"
        def __init__(self, text: str) -> None: self.data = {"text": text}
    class Message(list): pass
    class Segment:
        @staticmethod
        def text(value: str): return Seg(value)
    class Sender: nickname = "u"; card = ""
    class Event:
        def __init__(self, message_id: int) -> None:
            self.message_id, self.user_id, self.sender = message_id, 8, Sender()
            self.message = Message([Seg(str(message_id))])
    class Bot: self_id = "bot"
    class Logger:
        def debug(self, *a, **kw): pass
        info = warning = error = debug

    async def run() -> None:
        buffer: dict = {}
        controller = reply_buffer.ReplyConcurrencyController(session_limit=2, global_limit=2)
        started, replacement_started, old_release, old_finished = (asyncio.Event() for _ in range(4))
        timers: list[asyncio.Task] = []

        async def process(_bot, event, state):
            if event.message_id == 1 and not state.get("batched_events"):
                started.set()
                try:
                    await old_release.wait()
                except asyncio.CancelledError:
                    # Representative misbehaving upstream: cancellation is
                    # noticed but it keeps consuming its already-open request.
                    await old_release.wait()
                old_finished.set()
            else:
                replacement_started.set()

        def start(key, bot, wait):
            task = asyncio.create_task(reply_buffer.run_buffer_timer(
                key, bot, msg_buffer=buffer, process_response_logic=process,
                message_event_cls=Event, message_cls=Message, message_segment_cls=Segment,
                logger=Logger(), delay=wait, concurrency_controller=controller,
                batch_base_wait_seconds=.01, batch_min_wait_seconds=.01, batch_max_wait_seconds=.1,
            ))
            timers.append(task)
            return task
        common = dict(poke_event_cls=type("P", (), {}), message_event_cls=Event,
            group_message_event_cls=type("Group", (), {}), process_response_logic=process,
            msg_buffer=buffer, start_buffer_timer=start, logger=Logger(), concurrency_controller=controller,
            supplement_settings={"enabled": True, "quiet_seconds": .01, "max_quiet_seconds": .03},
        )
        first = asyncio.create_task(reply_buffer.handle_reply_event(Bot(), Event(1), {}, **common))
        await asyncio.wait_for(started.wait(), 1)
        await reply_buffer.handle_reply_event(Bot(), Event(2), {}, **common)
        await asyncio.wait_for(replacement_started.wait(), 1)
        assert not old_finished.is_set()
        old_release.set()
        await asyncio.wait_for(first, 1)
        await asyncio.wait_for(old_finished.wait(), 1)
        await asyncio.gather(*timers, return_exceptions=True)

    asyncio.run(run())


def test_group_relation_batch_drains_arrivals_received_while_judge_is_running() -> None:
    async def run() -> None:
        class Event:
            def __init__(self, message_id: int) -> None:
                self.message_id, self.user_id = message_id, 2
                self.message = []
        controller = supplement.SupplementController(
            supplement.SupplementSettings(group_batch_seconds=.001, relation_timeout_seconds=.5),
        )
        entry: dict = {"current_generation": 1, "active_generation_token": 1}
        controller.begin(entry, generation=1, originals=[_item("one")])
        entered, release = asyncio.Event(), asyncio.Event()
        calls: list[list[str]] = []
        lock = asyncio.Lock()
        state = {
            "reply_commit_lock": lock,
            "batched_events": [_item("one")],
            "supplement_relation_judge": None,
        }
        entry["active_state"] = state
        entry["supplement_controller"] = controller
        entry["active_task"] = asyncio.current_task()

        async def judge(_original, candidates):
            calls.append([str(item["message_id"]) for item in candidates])
            entered.set()
            if len(calls) == 1:
                await release.wait()
            return {"results": [{"message_id": str(item["message_id"]), "relation": "unrelated"} for item in candidates]}
        state["supplement_relation_judge"] = judge
        first = {"event": Event(2), "dedupe_key": "id:2"}
        assert reply_buffer._queue_group_supplement(entry, first)
        await asyncio.wait_for(entered.wait(), 1)
        second = {"event": Event(3), "dedupe_key": "id:3"}
        assert reply_buffer._queue_group_supplement(entry, second)
        release.set()
        for _ in range(50):
            if len(calls) >= 2:
                break
            await asyncio.sleep(.01)
        assert calls == [["2"], ["3"]]

    asyncio.run(run())


def test_cancelled_group_judge_detaches_provider_task_without_accepting_late_result() -> None:
    async def run() -> None:
        controller = supplement.SupplementController(
            supplement.SupplementSettings(relation_timeout_seconds=1),
        )
        entry: dict[str, object] = {}
        controller.begin(entry, generation=1, originals=[_item("a")], now=1)
        entered, release = asyncio.Event(), asyncio.Event()

        async def judge(_originals, _candidates):  # noqa: ANN001
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                # Simulate a client which keeps its request alive after local
                # cancellation.  Its late answer must remain detached.
                await release.wait()
            return {"results": [{"message_id": "b", "relation": "related"}]}

        task = asyncio.create_task(controller.judge_group(
            entry, generation=1, originals=[_item("a")], candidates=[_item("b")],
            safe_to_supersede=True, judge=judge, now=1.1,
        ))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        detached = entry.get("supplement_detached_judge_tasks")
        assert detached and len(detached) == 1
        assert entry["supplement"]["accepted_ids"] == ()
        release.set()
        await asyncio.sleep(.01)
        assert not entry["supplement_detached_judge_tasks"]

    asyncio.run(run())


def test_relation_judge_disables_search_without_requiring_tools_parameter() -> None:
    async def run() -> None:
        seen: dict[str, object] = {}

        async def api(messages, *, use_builtin_search=True):  # noqa: ANN001
            seen["search"] = use_builtin_search
            return '{"results":[{"message_id":"b","relation":"related"}]}'

        result = await relation.build_relation_judge(api)([_item("a")], [_item("b")])
        assert result["results"] == [{"message_id": "b", "relation": "related"}]
        assert seen["search"] is False

    asyncio.run(run())


@pytest.mark.parametrize("barrier", ["_external_action_started", "reply_delivery_started", "delivery_unknown"])
@pytest.mark.parametrize("cancel_owner", [False, True])
def test_private_direct_followup_after_external_action_is_promoted_fifo(barrier: str, cancel_owner: bool) -> None:
    class Seg:
        type = "text"
        def __init__(self, text: str) -> None: self.data = {"text": text}
    class Message(list): pass
    class Segment:
        @staticmethod
        def text(value: str): return Seg(value)
    class Sender: nickname = "u"; card = ""
    class Event:
        def __init__(self, message_id: int) -> None:
            self.message_id, self.user_id, self.sender = message_id, 9, Sender()
            self.message = Message([Seg(str(message_id))])
    class Bot: self_id = "bot"
    class Logger:
        def debug(self, *a, **kw): pass
        info = warning = error = debug

    async def run() -> None:
        buffer: dict = {}
        controller = reply_buffer.ReplyConcurrencyController(session_limit=2, global_limit=2)
        started, release = asyncio.Event(), asyncio.Event()
        seen: list[str] = []
        timers: list[asyncio.Task] = []

        async def process(_bot, event, state):
            if event.message_id == 1:
                state[barrier] = True
                started.set()
                await release.wait()
            else:
                seen.append(str(event.message_id))

        def start(key, bot, wait):
            task = asyncio.create_task(reply_buffer.run_buffer_timer(
                key, bot, msg_buffer=buffer, process_response_logic=process,
                message_event_cls=Event, message_cls=Message, message_segment_cls=Segment,
                logger=Logger(), delay=wait, concurrency_controller=controller,
                batch_base_wait_seconds=.01, batch_min_wait_seconds=.01, batch_max_wait_seconds=.1,
            ))
            timers.append(task)
            return task

        common = dict(
            poke_event_cls=type("P", (), {}), message_event_cls=Event,
            group_message_event_cls=type("Group", (), {}), process_response_logic=process,
            msg_buffer=buffer, start_buffer_timer=start, logger=Logger(),
            concurrency_controller=controller, batch_base_wait_seconds=.01,
            batch_min_wait_seconds=.01, batch_max_wait_seconds=.1,
            supplement_settings={"enabled": True, "window_seconds": 30,
                                 "quiet_seconds": .01, "max_quiet_seconds": .03},
        )
        first = asyncio.create_task(reply_buffer.handle_reply_event(Bot(), Event(1), {}, **common))
        await asyncio.wait_for(started.wait(), 1)
        await reply_buffer.handle_reply_event(Bot(), Event(2), {}, **common)
        if cancel_owner:
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            entry = next(iter(buffer.values()))
            assert entry["processing"] is False
            assert [item["event"].message_id for item in entry["items"]] == [2]
            assert "supplement_chain_route_snapshot" not in entry
            assert seen == []
            return
        release.set()
        await asyncio.wait_for(first, 1)
        await asyncio.gather(*timers, return_exceptions=True)
        assert seen == ["2"]

    asyncio.run(run())
