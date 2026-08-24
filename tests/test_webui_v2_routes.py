from __future__ import annotations

from types import SimpleNamespace

from ._loader import load_personification_module


v2_routes = load_personification_module(
    "plugin.personification.webui.routes.v2_routes"
)
webui_app = load_personification_module("plugin.personification.webui.app")
recovery = load_personification_module(
    "plugin.personification.core.reply_recovery_queue"
)


def test_v2_router_exposes_paged_trace_recovery_capability_and_sse_routes() -> None:
    router = v2_routes.build_v2_router(runtime=SimpleNamespace())
    paths = {route.path for route in router.routes}
    assert {
        "/api/v2/traces",
        "/api/v2/traces/{trace_id}",
        "/api/v2/recovery",
        "/api/v2/recovery/counts",
        "/api/v2/model-routes/capabilities",
        "/api/v2/events",
    } <= paths


def test_react_production_build_is_served_with_spa_fallback() -> None:
    index = webui_app._serve_frontend_asset("")
    nested = webui_app._serve_frontend_asset("traces/example")
    assert str(index.path).replace("\\", "/").endswith("frontend_dist/index.html")
    assert nested.path == index.path
    assert index.headers["cache-control"] == "no-store, max-age=0"


def test_recovery_summary_does_not_expose_claims_or_media_references(tmp_path) -> None:
    queue = recovery.ReplyRecoveryQueue(tmp_path / "state.sqlite3")
    item = queue.record_failure(
        bot_id="bot",
        conversation_kind="private",
        conversation_id="user",
        original_message_id="message",
        normalized_text="x" * 400,
        media_refs=[
            {
                "kind": "image",
                "origin": "current",
                "ref": "https://example.test/token-secret",
                "media_id": "media-id",
                "safe_summary": "公开封面",
            }
        ],
        failure_stage="generation",
        failure_class="generation_failed_before_send",
    )
    summary = v2_routes._recovery_summary(item)

    assert len(summary["text_summary"]) == 240
    assert summary["media"] == [
        {"kind": "image", "origin": "current", "safe_summary": "公开封面"}
    ]
    assert "claim_token" not in summary
    assert "claimed_by" not in summary
    assert "ref" not in summary["media"][0]
