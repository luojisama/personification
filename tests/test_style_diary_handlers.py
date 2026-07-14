from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from ._loader import load_personification_module


style_handlers = load_personification_module(
    "plugin.personification.handlers.style_diary_handlers"
)
periodic_jobs = load_personification_module(
    "plugin.personification.jobs.periodic_jobs"
)
task_builders = load_personification_module(
    "plugin.personification.jobs.task_builders"
)
diary_flow = load_personification_module(
    "plugin.personification.flows.diary_flow"
)


class _Finished(Exception):
    pass


class _Matcher:
    async def send(self, _message: str) -> None:
        return None

    async def finish(self, message: str) -> None:
        raise _Finished(message)


def test_manual_diary_publish_records_shared_qzone_state(monkeypatch) -> None:  # noqa: ANN001
    coordinated_calls: list[dict] = []
    state_updates: list[str] = []

    async def _update_cookie(_bot, *, force=False):  # noqa: ANN001
        assert force is True
        return True, "ok"

    async def _generate(_bot):  # noqa: ANN001
        return "手柄刚充上电，结果现在又有点困了"

    setattr(_generate, "mark_published", lambda content: state_updates.append(content))

    async def _publish(_content, _bot_id):  # noqa: ANN001
        return True, "ok"

    async def _coordinated(**kwargs):  # noqa: ANN003
        coordinated_calls.append(kwargs)
        result = await kwargs["publish"]()
        assert result == (True, "ok")
        return {"success": True, "status": "succeeded", "newly_committed": True}

    monkeypatch.setattr(periodic_jobs, "coordinated_qzone_publish", _coordinated)

    with pytest.raises(_Finished, match="发布成功"):
        asyncio.run(style_handlers.handle_manual_diary_command(
            _Matcher(),
            bot=type("Bot", (), {"self_id": "10001"})(),
            qzone_publish_available=True,
            update_qzone_cookie=_update_cookie,
            generate_ai_diary=_generate,
            publish_qzone_shuo=_publish,
        ))

    assert coordinated_calls[0]["bot_id"] == "10001"
    assert coordinated_calls[0]["content"] == "手柄刚充上电，结果现在又有点困了"
    assert state_updates == ["手柄刚充上电，结果现在又有点困了"]


def test_diary_task_binds_post_success_state_callback(monkeypatch) -> None:  # noqa: ANN001
    updates: list[dict] = []

    async def _flow(_bot, **_kwargs):  # noqa: ANN001
        return "刚把游戏存档整理完，终于不用怕按错了"

    monkeypatch.setattr(
        diary_flow,
        "schedule_diary_state_update",
        lambda **kwargs: updates.append(kwargs),
    )
    task = task_builders.build_generate_ai_diary_task(
        plugin_config=object(),
        generate_ai_diary_flow=_flow,
        load_prompt=lambda: "persona",
        call_ai_api=lambda *_args, **_kwargs: None,
        logger=object(),
        agent_tool_caller="caller",
        agent_data_dir="data-dir",
    )

    assert asyncio.run(task(object())) == "刚把游戏存档整理完，终于不用怕按错了"
    task.mark_published("刚把游戏存档整理完，终于不用怕按错了")

    assert updates[0]["diary_text"] == "刚把游戏存档整理完，终于不用怕按错了"
    assert updates[0]["tool_caller"] == "caller"
    assert updates[0]["data_dir"] == "data-dir"


def test_diary_task_resolves_current_tool_caller_for_each_run(monkeypatch) -> None:  # noqa: ANN001
    callers = ["startup-caller", "reloaded-caller"]
    seen: list[str] = []

    async def _flow(_bot, **kwargs):  # noqa: ANN001
        seen.append(kwargs["tool_caller"])
        return "刚切完模型，顺手记一笔"

    monkeypatch.setattr(
        diary_flow,
        "schedule_diary_state_update",
        lambda **kwargs: seen.append(kwargs["tool_caller"]),
    )
    task = task_builders.build_generate_ai_diary_task(
        plugin_config=object(),
        generate_ai_diary_flow=_flow,
        load_prompt=lambda: "persona",
        call_ai_api=lambda *_args, **_kwargs: None,
        logger=object(),
        agent_tool_caller=callers[0],
        get_agent_tool_caller=lambda: callers[-1],
        agent_data_dir="data-dir",
    )

    assert asyncio.run(task(object())) == "刚切完模型，顺手记一笔"
    task.mark_published("刚切完模型，顺手记一笔")

    assert seen == ["reloaded-caller", "reloaded-caller"]


def test_runtime_bundle_wires_current_caller_only_to_job_deps() -> None:
    source_path = Path(__file__).resolve().parents[1] / "core" / "runtime_assembly.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    keywords_by_factory: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in {"FlowSetupDeps", "JobSetupDeps"}:
            continue
        keywords_by_factory[node.func.id] = {
            str(keyword.arg) for keyword in node.keywords if keyword.arg
        }

    assert "get_agent_tool_caller" not in keywords_by_factory["FlowSetupDeps"]
    assert "get_agent_tool_caller" in keywords_by_factory["JobSetupDeps"]


def test_manual_diary_unknown_does_not_mark_published(monkeypatch) -> None:  # noqa: ANN001
    updates: list[str] = []

    async def _update_cookie(_bot, *, force=False):  # noqa: ANN001
        assert force is True
        return True, "ok"

    async def _generate(_bot):  # noqa: ANN001
        return "结果未知的正文"

    setattr(_generate, "mark_published", lambda content: updates.append(content))

    async def _publish(_content, _bot_id):  # noqa: ANN001
        return False, "outcome_unknown"

    async def _coordinated(**_kwargs):  # noqa: ANN003
        return {"success": False, "status": "unknown", "newly_committed": False}

    monkeypatch.setattr(periodic_jobs, "coordinated_qzone_publish", _coordinated)

    with pytest.raises(_Finished, match="结果未知"):
        asyncio.run(
            style_handlers.handle_manual_diary_command(
                _Matcher(),
                bot=type("Bot", (), {"self_id": "10001"})(),
                qzone_publish_available=True,
                update_qzone_cookie=_update_cookie,
                generate_ai_diary=_generate,
                publish_qzone_shuo=_publish,
            )
        )

    assert updates == []
