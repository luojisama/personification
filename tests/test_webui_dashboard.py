from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module


@pytest.fixture
def _runtime_with_data(tmp_path: Path, monkeypatch):
    data_store = load_personification_module("plugin.personification.core.data_store")
    paths = load_personification_module("plugin.personification.core.paths")
    monkeypatch.setattr(paths, "get_data_dir", lambda _cfg=None: tmp_path)
    cfg = SimpleNamespace(
        personification_data_dir=str(tmp_path),
        personification_quota_anthropic_monthly_tokens=0,
        personification_quota_openai_monthly_tokens=1000,
        personification_quota_gemini_cli_monthly_tokens=0,
        personification_quota_codex_monthly_tokens=0,
    )
    data_store.init_data_store(cfg)

    ledger = load_personification_module("plugin.personification.core.token_ledger")
    ledger.record_llm_call(model="gpt-x", prompt_tokens=100, completion_tokens=50, group_id="1")
    ledger.record_llm_call(model="gpt-x", prompt_tokens=200, completion_tokens=80, group_id="2")
    ledger.record_llm_call(model="gpt-y", prompt_tokens=30, completion_tokens=10, group_id="1")
    ledger.record_llm_call(model="gpt-z", prompt_tokens=70, completion_tokens=30, purpose="persona_template_synthesis")
    onebot_cache = load_personification_module("plugin.personification.core.onebot_cache")
    onebot_cache._clear_caches_for_testing()

    # 准备一份全局画像和一份群内画像
    memory_store_mod = load_personification_module("plugin.personification.core.memory_store")
    store = memory_store_mod.MemoryStore(plugin_config=cfg, logger=SimpleNamespace(warning=lambda *_a, **_k: None))
    store.initialize()
    store.upsert_core_profile(user_id="u_alpha", profile_text="全局画像 Alpha")
    store.upsert_local_profile(group_id="g1", user_id="u_alpha", profile_text="g1 中是常驻成员")

    profile_service_mod = load_personification_module("plugin.personification.core.profile_service")
    profile_service = profile_service_mod.ProfileService(memory_store=store)

    favorability_mod = load_personification_module("plugin.personification.core.favorability")
    favorability_service = favorability_mod.FavorabilityService(plugin_config=cfg)
    favorability_service.set_score("u_alpha", 66.0, actor="test")
    favorability_service.set_score("group_g1", 88.0, actor="test")

    class _InfoBot:
        def __init__(self) -> None:
            self.group_list_calls = 0
            self.group_info_calls = 0

        async def get_group_list(self):
            self.group_list_calls += 1
            return [
                {"group_id": 1, "group_name": "测试一群"},
                {"group_id": 2, "group_name": "测试二群"},
            ]

        async def get_group_info(self, group_id: int):
            self.group_info_calls += 1
            names = {1: "测试一群", 2: "测试二群"}
            return {"group_id": group_id, "group_name": names.get(int(group_id), "")}

    info_bot = _InfoBot()

    runtime_bundle = SimpleNamespace(
        profile_service=profile_service,
        memory_store=store,
        favorability_service=favorability_service,
        get_bots=lambda: {"1": info_bot},
    )

    app_module = load_personification_module("plugin.personification.webui.app")
    app_module.set_runtime_context(
        plugin_config=cfg,
        superusers={"10001"},
        get_bots=lambda: {"1": SimpleNamespace()},
        logger=SimpleNamespace(info=lambda *_a, **_k: None, warning=lambda *_a, **_k: None),
        runtime_bundle=runtime_bundle,
    )
    return SimpleNamespace(
        plugin_config=cfg,
        app_module=app_module,
        runtime_bundle=runtime_bundle,
        store=store,
        ledger=ledger,
        info_bot=info_bot,
    )


def _build_client(runtime_context):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(runtime_context.app_module.build_router())
    return TestClient(app)


def _login(client) -> None:
    sent: list = []

    class _Bot:
        async def call_api(self, _name: str, **kwargs):
            sent.append(kwargs)
            return {"message_id": 1}

    runtime = client.app  # not used
    # 重新绑定 get_bots 让 verify code 能送达
    app_module = load_personification_module("plugin.personification.webui.app")
    ctx = app_module.get_runtime_context()
    ctx.get_bots = lambda: {"1": _Bot()}
    res = client.post("/personification/api/auth/login", json={"qq": "10001"})
    assert res.status_code == 200, res.text
    code = re.search(r"\b(\d{6})\b", str(sent[-1].get("message", ""))).group(1)
    res2 = client.post(
        "/personification/api/auth/verify",
        json={"qq": "10001", "code": code, "device_label": "测试"},
    )
    assert res2.status_code == 200, res2.text


def test_dashboard_metrics_returns_token_summary(_runtime_with_data) -> None:
    client = _build_client(_runtime_with_data)
    _login(client)
    res = client.get("/personification/api/metrics/summary?window=30d")
    assert res.status_code == 200
    body = res.json()
    assert body["window"] == "month"
    assert body["total"]["total_tokens"] == 100 + 50 + 200 + 80 + 30 + 10 + 70 + 30
    assert body["total"]["call_count"] == 4
    assert len(body["series"]) == 30
    models = {row["model"]: row for row in body["by_model"]}
    assert models["gpt-x"]["total_tokens"] == 430
    assert models["gpt-y"]["total_tokens"] == 40
    assert models["gpt-z"]["total_tokens"] == 100
    distribution = {row["model"]: row for row in body["model_distribution"]}
    assert distribution["gpt-x"]["relative_width"] == 1.0
    groups = {row["group_id"]: row for row in body["by_group"]}
    assert groups["1"]["total_tokens"] == 100 + 50 + 30 + 10
    assert groups["2"]["total_tokens"] == 280
    assert sum(row["total_tokens"] for row in groups.values()) < body["total"]["total_tokens"]
    assert groups["1"]["group_name"] == "测试一群"
    assert groups["1"]["group_label"] == "测试一群"
    assert all("未命名群" not in row["group_label"] for row in groups.values())
    assert _runtime_with_data.info_bot.group_list_calls == 1
    assert _runtime_with_data.info_bot.group_info_calls == 0
    providers = {row["provider"]: row for row in body["provider_usage"]}
    assert providers["openai"]["total_tokens"] == body["total"]["total_tokens"]
    assert providers["openai"]["monthly_limit"] == 1000
    assert body["billing"]["cost_configured"] is False
    assert body["billing"]["quota"]["limit_tokens"] == 1000
    assert body["total_consumption"]["total"]["total_tokens"] == body["total"]["total_tokens"]
    assert body["total_consumption"]["total"]["call_count"] == body["total"]["call_count"]
    assert body["total_consumption"]["first_day"]
    assert body["total_consumption"]["last_day"]
    assert body["total_consumption"]["series"][-1]["cumulative_total_tokens"] == body["total"]["total_tokens"]
    assert {chart["key"] for chart in body["dashboard_overview"]["charts"]} == {"day", "week", "month", "total"}
    assert len(body["dashboard_overview"]["charts"]) == 4
    day_chart = next(chart for chart in body["dashboard_overview"]["charts"] if chart["key"] == "day")
    assert len(day_chart["series"]) == 24
    assert all("bucket_hour" in point for point in day_chart["series"])
    assert day_chart["series"][-1]["total_tokens"] == body["total"]["total_tokens"]
    purposes = {row["purpose"]: row for row in body["by_purpose"]}
    assert purposes["persona_template_synthesis"]["purpose_label"] == "人设构建：模板生成"
    assert purposes["persona_template_synthesis"]["total_tokens"] == 100
    overview_models = {row["model"]: row for row in body["dashboard_overview"]["model_usage"]}
    assert overview_models["gpt-x"]["call_count"] == 2
    assert overview_models["gpt-x"]["total_tokens"] == 430
    overview_groups = {row["group_id"]: row for row in body["dashboard_overview"]["group_usage"]}
    assert overview_groups["1"]["group_label"] == "测试一群"
    assert overview_groups["1"]["percent"] > 0
    overview_purposes = {row["purpose"]: row for row in body["dashboard_overview"]["purpose_usage"]}
    assert overview_purposes["persona_template_synthesis"]["purpose_label"] == "人设构建：模板生成"
    assert overview_purposes["persona_template_synthesis"]["percent"] > 0


def test_dashboard_group_rows_never_emit_fake_unnamed_label() -> None:
    metrics_routes = load_personification_module("plugin.personification.webui.routes.metrics_routes")
    runtime = SimpleNamespace(runtime_bundle=SimpleNamespace(get_bots=lambda: {"1": SimpleNamespace()}))
    rows = [{"group_id": "999001", "total_tokens": 1, "call_count": 1}]
    annotated = asyncio.run(metrics_routes._annotate_group_rows(runtime, rows))
    assert annotated[0]["group_name"] == ""
    assert annotated[0]["group_label"] == "群 999001"
    assert annotated[0]["group_name_missing"] is True
    assert annotated[0]["group_lookup_status"] == "missing"
    assert "未命名群" not in annotated[0]["group_label"]


def test_dashboard_window_validation(_runtime_with_data) -> None:
    client = _build_client(_runtime_with_data)
    _login(client)
    res = client.get("/personification/api/metrics/summary?window=year")
    assert res.status_code == 422


def test_personas_list_and_detail(_runtime_with_data) -> None:
    client = _build_client(_runtime_with_data)
    _login(client)
    res = client.get("/personification/api/personas")
    assert res.status_code == 200
    body = res.json()
    assert body["available"] is True
    listed = next(p for p in body["profiles"] if p["user_id"] == "u_alpha")
    assert listed["favorability"]["available"] is True
    assert listed["favorability"]["score"] == 66.0

    res2 = client.get("/personification/api/personas/u_alpha")
    assert res2.status_code == 200
    detail = res2.json()
    assert detail["core_profile"]["profile_text"] == "全局画像 Alpha"
    assert detail["favorability"]["score"] == 66.0
    assert detail["favorability"]["events"][0]["label"] == "管理员手动调整"
    assert len(detail["local_profiles"]) == 1
    assert detail["local_profiles"][0]["group_id"] == "g1"


def test_groups_list_and_detail(_runtime_with_data) -> None:
    client = _build_client(_runtime_with_data)
    _login(client)
    res = client.get("/personification/api/groups")
    assert res.status_code == 200
    body = res.json()
    assert any(group["group_id"] == "g1" for group in body["groups"])

    res2 = client.get("/personification/api/groups/g1/personas")
    assert res2.status_code == 200
    detail = res2.json()
    assert any(p["user_id"] == "u_alpha" for p in detail["profiles"])
    assert detail["group_favorability"]["score"] == 88.0
    member = next(p for p in detail["profiles"] if p["user_id"] == "u_alpha")
    assert member["favorability"]["score"] == 66.0

    res3 = client.get("/personification/api/groups/g1/style")
    assert res3.status_code == 200
    style = res3.json()
    assert style["group_id"] == "g1"
    assert style["style_text"] == ""  # 暂未写入


def test_profile_service_prompt_block(_runtime_with_data) -> None:
    svc = _runtime_with_data.runtime_bundle.profile_service
    block = svc.build_prompt_block(user_id="u_alpha", group_id="g1")
    assert "## 用户档案" in block
    assert "全局画像 Alpha" in block
    assert "g1 中是常驻成员" in block

    block2 = svc.build_prompt_block(user_id="u_missing")
    assert block2 == ""
