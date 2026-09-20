"""Isolated startup-registration smoke coverage for the matcher composition root.

The production startup hook delegates its matcher registration to
``setup_all_matchers``.  This test intentionally executes that real composition
function, but replaces only the NoneBot matcher factories.  Consequently no
driver is started and no network request, outbound QQ send, or background task
can escape the test process.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ._loader import load_personification_module


composition = load_personification_module("plugin.personification.handlers.setup.composition")


class _Matcher:
    def __init__(self, kind: str, options: dict[str, Any]) -> None:
        self.kind = kind
        self.options = options
        self.handlers: list[Any] = []

    def handle(self):  # noqa: ANN201
        def decorate(handler):  # noqa: ANN001, ANN202
            self.handlers.append(handler)
            return handler

        return decorate


def _noop(*_args: Any, **_kwargs: Any) -> None:
    """A registration-only dependency that must not be invoked by this smoke."""


def _matcher_deps(*, cleared: list[bool]) -> Any:
    """Build every declared composition dependency, failing on new required wiring."""
    values = {field.name: _noop for field in fields(composition.MatcherSetupDeps)}
    values.update(
        runtime_bundle=SimpleNamespace(
            peer_bot_coordinator=None,
            peer_bot_observer=None,
            favorability_service=None,
            scoped_profile_service=None,
            private_profile_refresh=None,
        ),
        reply_processor_deps=SimpleNamespace(runtime=SimpleNamespace()),
        plugin_config=SimpleNamespace(
            personification_whitelist=[],
            personification_sticker_probability=0.0,
            personification_sticker_path="",
        ),
        msg_buffer={},
        bot_statuses={},
        superusers=set(),
        clear_private_command_keywords=lambda: cleared.append(True),
        register_private_command_keywords=_noop,
        get_configured_api_providers=lambda: [],
    )
    return composition.MatcherSetupDeps(**values)


def test_startup_lifespan_registers_real_matcher_composition_without_side_effects(monkeypatch) -> None:  # noqa: ANN001
    """Exercise the startup registration path with real registrar implementations.

    The production matcher modules still build all their handler closures.  Only
    ``on_message``, ``on_notice``, ``on_command``, and ``Rule`` are replaced at
    the framework boundary, which makes this safe to run without a NoneBot
    lifespan, adapter connection, scheduler, or QQ bot.
    """
    registered: list[_Matcher] = []
    cleared: list[bool] = []

    def factory(kind: str):
        def register(*_args: Any, **kwargs: Any) -> _Matcher:
            matcher = _Matcher(kind, kwargs)
            registered.append(matcher)
            return matcher

        return register

    registrar_modules = {
        composition.register_reply_matchers.__module__,
        composition.register_chat_matchers.__module__,
        composition.register_whitelist_matchers.__module__,
        composition.register_admin_matchers.__module__,
        composition.register_tts_matchers.__module__,
        composition.register_perm_blacklist_matchers.__module__,
        composition.register_diary_matchers.__module__,
        composition.register_runtime_switch_matchers.__module__,
        composition.register_style_context_matchers.__module__,
        composition.register_persona_admin_matchers.__module__,
        load_personification_module(
            "plugin.personification.handlers.login_approval_matchers"
        ).__name__,
    }
    for name in registrar_modules:
        module = sys.modules[name]
        for factory_name, kind in (
            ("on_message", "message"),
            ("on_notice", "notice"),
            ("on_command", "command"),
        ):
            if hasattr(module, factory_name):
                monkeypatch.setattr(module, factory_name, factory(kind))
        if hasattr(module, "Rule"):
            monkeypatch.setattr(module, "Rule", lambda predicate: predicate)

    handles = composition.setup_all_matchers(deps=_matcher_deps(cleared=cleared))

    assert cleared == [True]
    assert {matcher.kind for matcher in registered} == {"message", "notice", "command"}
    assert len(registered) >= 40
    assert all(matcher.handlers for matcher in registered)
    assert {
        "reply_matcher",
        "record_msg_matcher",
        "sticker_chat_matcher",
        "approve_login",
        "persona_admin_cmd",
        "plugin_knowledge_status_cmd",
    } <= handles.keys()


def test_real_nonebot_lifespan_runs_plugin_startup_with_isolated_runtime(tmp_path) -> None:
    """Run the actual plugin startup-hook list in a fresh NoneBot driver process.

    This is deliberately broader than the registrar test above: the production
    ``__init__`` module decorates the real driver and its ``Lifespan.startup``
    invokes every registered startup hook.  Runtime construction, scheduler,
    background services, web UI, skill loading, and outbound-facing services
    are replaced before that lifespan starts.  The matcher composition itself
    remains real, including every registrar and handler closure.
    """
    source_root = Path(__file__).resolve().parents[3]
    isolated_data_dir = tmp_path / "personification-data"
    isolated_env_file = tmp_path / "empty.env"
    isolated_env_file.write_text("", encoding="utf-8")
    script = r'''
import asyncio
import importlib
import importlib.util
import os
import sys
import types
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace

source_root = Path(os.environ["PERSONIFICATION_LIFESPAN_SOURCE_ROOT"])
data_dir = Path(os.environ["PERSONIFICATION_LIFESPAN_DATA_DIR"])
empty_env = Path(os.environ["PERSONIFICATION_LIFESPAN_EMPTY_ENV"])
assert Path.cwd() != source_root
assert str(source_root) not in {str(parent) for parent in Path.cwd().parents}
sys.path.insert(0, str(source_root))

# Load just runtime_config through namespace packages before the real plugin
# entrypoint.  The production helper otherwise deliberately walks from the
# source checkout back to its parent bot project and finds .env.prod.  This
# process-local guard proves the isolated lifespan never reads that file.
plugin_root = source_root / "plugin" / "personification"
plugin_ns = types.ModuleType("plugin"); plugin_ns.__path__ = [str(source_root / "plugin")]
personification_ns = types.ModuleType("plugin.personification"); personification_ns.__path__ = [str(plugin_root)]
core_ns = types.ModuleType("plugin.personification.core"); core_ns.__path__ = [str(plugin_root / "core")]
sys.modules.update({"plugin": plugin_ns, "plugin.personification": personification_ns, "plugin.personification.core": core_ns})
runtime_config_spec = importlib.util.spec_from_file_location(
    "plugin.personification.core.runtime_config", plugin_root / "core" / "runtime_config.py"
)
runtime_config = importlib.util.module_from_spec(runtime_config_spec)
sys.modules[runtime_config_spec.name] = runtime_config
runtime_config_spec.loader.exec_module(runtime_config)
env_candidate_calls = []
runtime_config._iter_env_file_candidates = lambda: env_candidate_calls.append(True) or []

import nonebot
nonebot.init(
    _env_file=empty_env,
    driver="~fastapi",
    superusers=set(),
    personification_data_dir=str(data_dir),
)

# The plugin requires APScheduler at import time and opportunistically imports
# HTMLRender.  Supply minimal modules before the real entrypoint so their own
# third-party lifespan hooks cannot start a scheduler or browser runtime.
fake_scheduler = SimpleNamespace(add_job=lambda *args, **kwargs: None)
apscheduler = types.ModuleType("nonebot_plugin_apscheduler"); apscheduler.scheduler = fake_scheduler
htmlrender = types.ModuleType("nonebot_plugin_htmlrender"); htmlrender.md_to_pic = None
sys.modules.update({apscheduler.__name__: apscheduler, htmlrender.__name__: htmlrender})
nonebot.require = lambda name: sys.modules[name]

plugin_spec = importlib.util.spec_from_file_location(
    "plugin.personification", plugin_root / "__init__.py", submodule_search_locations=[str(plugin_root)]
)
plugin = importlib.util.module_from_spec(plugin_spec)
sys.modules[plugin_spec.name] = plugin
setattr(plugin_ns, "personification", plugin)
plugin_spec.loader.exec_module(plugin)
entry_config = plugin.plugin_config
composition = importlib.import_module("plugin.personification.handlers.setup.composition")

events = []
class Matcher:
    def __init__(self, kind, kwargs): self.kind, self.kwargs, self.handlers = kind, kwargs, []
    def handle(self):
        def decorate(handler): self.handlers.append(handler); return handler
        return decorate

def factory(kind):
    def register(*args, **kwargs):
        matcher = Matcher(kind, kwargs); events.append(matcher); return matcher
    return register

registrar_names = {
    composition.register_reply_matchers.__module__, composition.register_chat_matchers.__module__,
    composition.register_whitelist_matchers.__module__, composition.register_admin_matchers.__module__,
    composition.register_tts_matchers.__module__, composition.register_perm_blacklist_matchers.__module__,
    composition.register_diary_matchers.__module__, composition.register_runtime_switch_matchers.__module__,
    composition.register_style_context_matchers.__module__, composition.register_persona_admin_matchers.__module__,
    "plugin.personification.handlers.login_approval_matchers",
}
for name in registrar_names:
    module = importlib.import_module(name)
    for attr, kind in (("on_message", "message"), ("on_notice", "notice"), ("on_command", "command")):
        if hasattr(module, attr): setattr(module, attr, factory(kind))
    if hasattr(module, "Rule"): module.Rule = lambda predicate: predicate

def noop(*args, **kwargs): return None
config = SimpleNamespace(
    personification_whitelist=[], personification_sticker_probability=0.0,
    personification_sticker_path="", personification_git_auto_update=False,
    personification_labeler_enabled=False, personification_qzone_enabled=False,
    personification_route_probe_daily_enabled=False, personification_skills_path=None,
    personification_skill_sources=[], personification_plugin_knowledge_build_enabled=False,
    personification_data_dir=str(data_dir),
)
runtime = SimpleNamespace(agent_tool_caller=None, lite_tool_caller=None, vision_caller=None, knowledge_store=None, tool_registry=None)
bundle = SimpleNamespace(
    personification_rule=noop, poke_rule=noop, poke_notice_rule=noop, msg_buffer={},
    persona_store=None, private_profile_refresh=None, scoped_profile_service=None,
    tool_registry=None, memory_store=None, profile_service=None, memory_curator=None,
    background_intelligence=None, qq_outbound_ledger=None, peer_bot_coordinator=None,
    peer_bot_observer=None, favorability_service=None, reply_processor_deps=SimpleNamespace(runtime=runtime),
    _get_whitelisted_groups=lambda: [], load_prompt=noop,
)
def matcher_deps(**ignored):
    values = {field.name: noop for field in fields(composition.MatcherSetupDeps)}
    values.update(runtime_bundle=bundle, reply_processor_deps=bundle.reply_processor_deps, plugin_config=config,
        msg_buffer={}, bot_statuses={}, superusers=set(), get_configured_api_providers=lambda: [],
        clear_private_command_keywords=noop, register_private_command_keywords=noop)
    return composition.MatcherSetupDeps(**values)
bundle.make_matcher_setup_deps = matcher_deps
bundle.make_flow_setup_deps = lambda: SimpleNamespace()
bundle.make_job_setup_deps = lambda **kwargs: SimpleNamespace()

plugin.plugin_config = config
plugin.superusers = set()
plugin.build_plugin_runtime = lambda **kwargs: bundle
plugin.setup_flows = lambda **kwargs: {"check_proactive_messaging": noop, "check_group_idle_topic": noop,
    "apply_global_switch": noop, "apply_tts_global_switch": noop, "apply_web_search_switch": noop, "apply_proactive_switch": noop}
plugin.setup_jobs = lambda **kwargs: {"generate_ai_diary": noop, "qzone_social_scan": None, "qzone_inbound_poll": None}
plugin.runtime_task_supervisor.configure = noop
plugin.runtime_task_supervisor.start = noop
plugin.runtime_task_supervisor.shutdown = lambda **kwargs: asyncio.sleep(0)
plugin._start_visual_probe_background = noop
plugin._start_llm_warmup_background = noop
plugin._start_qzone_cookie_refresh_background = noop
plugin.set_image_host_allowlist = noop
plugin.StickerLabeler = type("Labeler", (), {"__init__": lambda self, *a, **k: None, "legacy_scan": lambda self: asyncio.sleep(0)})

webui = types.ModuleType("plugin.personification.webui"); webui.install_webui = lambda **kwargs: False
sys.modules[webui.__name__] = webui
tasks = importlib.import_module("plugin.personification.core.tasks_service"); tasks.restore_tasks_on_startup = noop
social = importlib.import_module("plugin.personification.flows.social_intelligence")
social.setup_social_intelligence_jobs = lambda **kwargs: 0

route_service = importlib.import_module("plugin.personification.core.route_probe_service")
route_service.get_route_probe_service = lambda runtime: SimpleNamespace(shutdown=lambda: asyncio.sleep(0))
qzone_auth = importlib.import_module("plugin.personification.core.qzone_auth")
qzone_auth.qzone_login_manager.shutdown = lambda: asyncio.sleep(0)
mcp = importlib.import_module("plugin.personification.core.mcp_management")
mcp.shutdown_mcp_managers = lambda: asyncio.sleep(0)
gemini = importlib.import_module("plugin.personification.core.gemini_web_service")
gemini.shutdown_gemini_web_services = lambda: asyncio.sleep(0)
mimo = importlib.import_module("plugin.personification.core.mimo_web_asr_service")
mimo.shutdown_mimo_web_asr_services = lambda: asyncio.sleep(0)
plugin.close_shared_http_client = lambda **kwargs: asyncio.sleep(0)

async def main():
    driver = nonebot.get_driver()
    await driver._lifespan.startup()
    assert plugin.runtime_bundle is bundle
    assert Path(entry_config.personification_data_dir).resolve() == data_dir.resolve()
    assert env_candidate_calls, "the env guard was not exercised"
    config_info = getattr(entry_config, "_personification_env_config_info", {})
    assert Path(config_info["path"]).resolve() == (data_dir / "env.json").resolve()
    assert set(config_info["imported_fields"]) <= {"personification_data_dir", "personification_favorability_attitudes"}
    assert len(events) >= 40 and {item.kind for item in events} == {"message", "notice", "command"}
    assert {"reply_matcher", "record_msg_matcher", "approve_login"} <= plugin.matcher_handles.keys()
    await driver._lifespan.shutdown()
asyncio.run(main())
print("lifespan-ok", len(events))
'''
    environment = dict(os.environ)
    for key in list(environment):
        if key.lower().startswith("personification_"):
            environment.pop(key)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONPYCACHEPREFIX"] = str(tmp_path / "pycache")
    environment["PERSONIFICATION_LIFESPAN_SOURCE_ROOT"] = str(source_root)
    environment["PERSONIFICATION_LIFESPAN_DATA_DIR"] = str(isolated_data_dir)
    environment["PERSONIFICATION_LIFESPAN_EMPTY_ENV"] = str(isolated_env_file)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-6000:]
    assert "lifespan-ok" in completed.stdout
    assert ".env.prod" not in completed.stdout
