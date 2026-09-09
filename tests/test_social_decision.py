from __future__ import annotations

import concurrent.futures
import math
import threading

from plugin.personification.core import social_decision
from plugin.personification.core.social_decision import SocialDecision, claim_social_decision, parse_social_decision, settle_social_decision


class _Store:
    def __init__(self) -> None: self.data = {}; self.lock = threading.Lock()
    def mutate_sync(self, name, mutator):  # noqa: ANN001
        with self.lock:
            self.data[name] = mutator(self.data.get(name, {})); return self.data[name]


def test_social_decision_requires_real_json_boolean_and_motivation() -> None:
    assert parse_social_decision('{"send":"true","action":"contact"}') is None
    assert parse_social_decision('{"send":true,"action":"contact","target_id":"1","content":"hi","motivation":""}') is None
    decision = parse_social_decision(
        '{"send":true,"action":"contact","target_id":"1","content":"hi","motivation":"pending trip","source_event_ids":["m1"],"expression":"text"}'
    )
    assert decision is not None
    assert decision.should_send is True
    assert decision.source_event_ids == ("m1",)


def test_social_decision_silence_is_not_outbound() -> None:
    decision = parse_social_decision(
        '{"send":false,"action":"silent","content":"","motivation":"no reason","source_event_ids":[]}'
    )
    assert decision is not None
    assert decision.should_send is False


def test_social_decision_rejects_untrusted_source_and_nonfinite_next_at() -> None:
    raw = '{"send":true,"action":"contact","target_id":"1","content":"hi","motivation":"m","source_event_ids":["invented"],"next_consider_at":1}'
    assert parse_social_decision(raw, allowed_source_event_ids={"actual"}) is None
    assert parse_social_decision(raw.replace('"next_consider_at":1', '"next_consider_at":NaN')) is None


def test_claim_is_atomic_and_wording_or_action_cannot_replay(monkeypatch) -> None:  # noqa: ANN001
    store = _Store()
    monkeypatch.setattr("plugin.personification.core.data_store.get_data_store", lambda: store)
    first = SocialDecision("contact", "u", "first wording", "first reason", ("actual",))
    rewritten = SocialDecision("join", "u", "second wording", "other reason", ("actual",))
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: claim_social_decision(first, channel="private", scope="bot"), range(8)))
    assert sum(results) == 1
    settle_social_decision(first, status="unknown", scope="bot")
    assert claim_social_decision(rewritten, channel="qzone", scope="bot") is False


def test_no_event_claim_uses_target_cooldown_and_store_failure_is_closed(monkeypatch) -> None:  # noqa: ANN001
    store = _Store(); monkeypatch.setattr("plugin.personification.core.data_store.get_data_store", lambda: store)
    decision = SocialDecision("contact", "u", "x", "m")
    assert claim_social_decision(decision, channel="private", scope="bot", now=100, target_cooldown_seconds=60)
    assert not claim_social_decision(SocialDecision("contact", "u", "rewritten", "other"), channel="qzone", scope="bot", now=120, target_cooldown_seconds=60)
    assert claim_social_decision(SocialDecision("contact", "u", "new attempt", "other"), channel="private", scope="bot", now=161, target_cooldown_seconds=60)
    monkeypatch.setattr("plugin.personification.core.data_store.get_data_store", lambda: (_ for _ in ()).throw(RuntimeError()))
    assert not claim_social_decision(decision, channel="private", scope="bot")


def test_unknown_event_attempt_never_reopens_after_cooldown(monkeypatch) -> None:  # noqa: ANN001
    store = _Store(); monkeypatch.setattr("plugin.personification.core.data_store.get_data_store", lambda: store)
    original = SocialDecision("contact", "u", "hello", "m", ("message-99",))
    rewritten = SocialDecision("join", "u", "different words", "different reason", ("message-99",))
    assert claim_social_decision(original, channel="private", scope="onebot:b", now=100, target_cooldown_seconds=1)
    settle_social_decision(original, status="unknown", scope="onebot:b")
    # A source-backed delivery uncertainty is permanent for that event, even
    # after the ordinary target cooldown would permit a later motivation.
    assert not claim_social_decision(rewritten, channel="group", scope="onebot:b", now=10_000, target_cooldown_seconds=1)


def test_distinct_events_share_target_cooldown_except_composite_children(monkeypatch) -> None:  # noqa: ANN001
    store = _Store(); monkeypatch.setattr("plugin.personification.core.data_store.get_data_store", lambda: store)
    first = SocialDecision("contact", "u", "x", "m", ("event-a",))
    other = SocialDecision("qzone_interact", "u", "y", "m", ("event-b",))
    assert claim_social_decision(first, channel="private", scope="onebot:b", now=100, target_cooldown_seconds=60)
    assert not claim_social_decision(other, channel="qzone", scope="onebot:b", now=120, target_cooldown_seconds=60)
    assert not claim_social_decision(SocialDecision("qzone_interact", "u", "like", "m", ("event-c", "qzone:like")), channel="qzone", scope="onebot:b", now=120, target_cooldown_seconds=60)


def test_same_bot_cross_surface_blocks_same_event_but_allows_composite_child_actions(monkeypatch) -> None:  # noqa: ANN001
    store = _Store(); monkeypatch.setattr("plugin.personification.core.data_store.get_data_store", lambda: store)
    private = SocialDecision("contact", "u", "hello", "m", ("feed-1",))
    qzone_same = SocialDecision("qzone_interact", "u", "comment", "rewritten", ("feed-1",))
    like = SocialDecision("qzone_interact", "u", "[like]", "m", ("feed-2", "qzone:like"))
    comment = SocialDecision("qzone_interact", "u", "comment", "m", ("feed-2", "qzone:comment"))
    assert claim_social_decision(private, channel="private", scope="onebot:90001", now=100)
    settle_social_decision(private, status="unknown", scope="onebot:90001")
    assert not claim_social_decision(qzone_same, channel="qzone", scope="onebot:90001", now=22000)
    assert claim_social_decision(like, channel="qzone", scope="onebot:90001", now=22000)
    assert claim_social_decision(comment, channel="qzone", scope="onebot:90001", now=22001)
