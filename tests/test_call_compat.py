from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module

compat = load_personification_module("plugin.personification.core.call_compat")
templates = load_personification_module("plugin.personification.webui.routes.persona_template_routes")
evolves = load_personification_module("plugin.personification.core.evolves")
commands = load_personification_module("plugin.personification.handlers.persona_admin_commands")


def test_internal_type_error_never_repeats_model_call():
    calls = []
    async def caller(*args, **kwargs):
        calls.append((args, kwargs))
        raise TypeError("internal error after request")
    with pytest.raises(TypeError, match="internal error"):
        asyncio.run(templates._call_main_model(caller, []))
    assert len(calls) == 1


@pytest.mark.parametrize("legacy", ["single", "positional"])
def test_model_legacy_signature_is_selected_before_invocation(legacy):
    calls = []
    async def single(messages):
        calls.append(messages)
        return "legacy result"
    async def positional(messages, second, third, temp, /):
        calls.append((messages, second, third, temp))
        return "legacy result"
    assert asyncio.run(templates._call_main_model(single if legacy == "single" else positional, [])) == "legacy result"
    assert len(calls) == 1


def test_incompatible_signature_fails_without_invocation():
    calls = []
    def caller(*, required):
        calls.append(required)
    with pytest.raises(TypeError):
        compat.select_call_shape(caller, [(([],), {}), ((), {"messages": []})])
    assert calls == []


def test_opaque_callable_uses_canonical_shape_once():
    class Opaque:
        __signature__ = object()
        def __call__(self, *args, **kwargs):
            raise TypeError("internal")
    args, kwargs = compat.select_call_shape(Opaque(), [((1,), {"flag": True}), ((2,), {})])
    assert args == (1,)
    assert kwargs == {"flag": True}


def test_evolves_internal_error_is_not_replayed():
    calls = []
    async def caller(*args, **kwargs):
        calls.append(1)
        raise TypeError("inside evolves")
    engine = object.__new__(evolves.EvolvesEngine)
    engine.call_ai_api = caller
    with pytest.raises(TypeError, match="inside evolves"):
        asyncio.run(engine._call_evolves_llm([]))
    assert calls == [1]


def test_qzone_scanner_internal_error_is_not_replayed():
    calls = []
    async def scanner(*args, **kwargs):
        calls.append(1)
        raise TypeError("inside scanner after action")
    with pytest.raises(TypeError, match="inside scanner"):
        asyncio.run(commands.handle_qzone_command(
            SimpleNamespace(qzone_social_scan=scanner), event=SimpleNamespace(), tokens=["run"],
        ))
    assert calls == [1]
