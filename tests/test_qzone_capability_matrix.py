from __future__ import annotations

from personification.core.qzone_capability_matrix import QzoneCapabilityMatrix


def _by_action(snapshot: dict) -> dict[str, dict]:
    return {item["action"]: item for item in snapshot["items"]}


def test_local_methods_and_coarse_write_do_not_claim_support() -> None:
    matrix = QzoneCapabilityMatrix(time_source=lambda: 100.0)
    snapshot = matrix.snapshot(
        "10001",
        auth_status={"status": "ready", "last_success_at": 90},
        aggregate_status={
            "qzone.web_read": {"state": "available", "updated_at": 80},
            "qzone.web_write": {"state": "available", "updated_at": 81},
        },
    )
    rows = _by_action(snapshot)

    assert rows["login_state"]["state"] == "available"
    assert rows["friend_feed_read"]["state"] == "unknown"
    assert rows["publish"]["state"] == "unknown"
    assert rows["child_comment_reply"]["source"] == "local_method_unverified"
    assert snapshot["production_verified"] is False


def test_runtime_observation_is_action_specific_and_sanitized() -> None:
    matrix = QzoneCapabilityMatrix(time_source=lambda: 123.0)
    matrix.observe(
        "10001",
        "child_comment_reply",
        state="available",
        interface="https://example.invalid/re_feeds?g_tk=secret",
        http_status=200,
        business_code="0",
        auth_state="ready",
        detail_code="child reply succeeded",
    )
    rows = _by_action(matrix.snapshot("10001", auth_status={"status": "ready"}))

    child = rows["child_comment_reply"]
    assert child["state"] == "available"
    assert child["interface"] == "https://example.invalid/re_feeds"
    assert child["detail_code"] == "child_reply_succeeded"
    assert rows["top_level_comment"]["state"] == "unknown"


def test_missing_fields_are_definite_preflight_evidence_only() -> None:
    matrix = QzoneCapabilityMatrix(time_source=lambda: 200.0)
    matrix.observe(
        "10001",
        "forward",
        state="unavailable",
        missing_fields=["owner", "topicId", "topicId"],
        detail_code="preflight_missing_fields",
    )
    forward = _by_action(matrix.snapshot("10001"))["forward"]

    assert forward["state"] == "unavailable"
    assert forward["missing_fields"] == ["owner", "topicId"]
    assert forward["http_status"] is None


def test_disabled_overrides_observation_without_deleting_it() -> None:
    matrix = QzoneCapabilityMatrix(time_source=lambda: 300.0)
    matrix.observe("1", "like", state="available", detail_code="like_succeeded")

    assert _by_action(matrix.snapshot("1", enabled=False))["like"]["state"] == "disabled"
    assert _by_action(matrix.snapshot("1", enabled=True))["like"]["state"] == "available"
