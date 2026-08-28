from __future__ import annotations

import asyncio

import pytest

from ._loader import load_personification_module


admin_commands = load_personification_module("plugin.personification.handlers.admin_commands")
admin_helpers = load_personification_module("plugin.personification.handlers.admin_helpers")


class _Finished(Exception):
    pass


class _Matcher:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def finish(self, message):  # noqa: ANN001
        self.messages.append(str(message))
        raise _Finished(str(message))


@pytest.mark.parametrize("value", [-100, -80, -0.01, 0, 100])
def test_parse_group_favorability_accepts_full_negative_range(value: float) -> None:
    assert admin_helpers.parse_group_fav_update_args(str(value), "100") == ("100", float(value), None)
    assert admin_helpers.parse_group_fav_update_args(f"200 {value}", None) == ("200", float(value), None)


@pytest.mark.parametrize("value", ["-100.01", "100.01", "nan", "inf", "-inf", "oops"])
def test_parse_group_favorability_rejects_invalid_values(value: str) -> None:
    target, score, error = admin_helpers.parse_group_fav_update_args(value, "100")
    assert target is None and score is None
    assert error and ("-100 到 100" in error or "数字" in error)


def test_set_group_favorability_preserves_negative_score_without_fallback_clamp() -> None:
    asyncio.run(_set_group_favorability_preserves_negative_score_without_fallback_clamp())


async def _set_group_favorability_preserves_negative_score_without_fallback_clamp() -> None:
    matcher = _Matcher()
    calls: list[tuple[str, float]] = []
    with pytest.raises(_Finished, match="-80.00"):
        await admin_commands.handle_set_group_fav_command(
            matcher,
            sign_in_available=True,
            arg_str="-80",
            event_group_id="123",
            operator_user_id="admin",
            parse_group_fav_update_args=admin_helpers.parse_group_fav_update_args,
            update_user_data=lambda key, **data: calls.append((key, data["favorability"])),
            logger=type("L", (), {"info": lambda *_: None})(),
        )
    assert calls == [("group_123", -80.0)]


def test_set_group_favorability_passes_signed_score_to_service() -> None:
    asyncio.run(_set_group_favorability_passes_signed_score_to_service())


async def _set_group_favorability_passes_signed_score_to_service() -> None:
    matcher = _Matcher()
    calls: list[tuple] = []
    service = type("S", (), {"set_score": lambda _self, *args, **kwargs: calls.append((args, kwargs))})()
    with pytest.raises(_Finished, match="-0.01"):
        await admin_commands.handle_set_group_fav_command(
            matcher, sign_in_available=True, arg_str="-0.01", event_group_id="123", operator_user_id="admin",
            parse_group_fav_update_args=admin_helpers.parse_group_fav_update_args,
            update_user_data=lambda *_args, **_kwargs: pytest.fail("fallback must not run"),
            logger=type("L", (), {"info": lambda *_: None})(), favorability_service=service,
        )
    assert calls[0][0][:2] == ("group_123", -0.01)


def test_invalid_set_group_favorability_never_calls_service() -> None:
    asyncio.run(_invalid_set_group_favorability_never_calls_service())


async def _invalid_set_group_favorability_never_calls_service() -> None:
    matcher = _Matcher()
    calls: list[tuple] = []
    service = type("S", (), {"set_score": lambda *args, **kwargs: calls.append(args)})()
    with pytest.raises(_Finished, match="-100 到 100"):
        await admin_commands.handle_set_group_fav_command(
            matcher,
            sign_in_available=True,
            arg_str="101",
            event_group_id="123",
            operator_user_id="admin",
            parse_group_fav_update_args=admin_helpers.parse_group_fav_update_args,
            update_user_data=lambda *_args, **_kwargs: calls.append(("fallback",)),
            logger=type("L", (), {"info": lambda *_: None})(),
            favorability_service=service,
        )
    assert calls == []


def test_group_query_uses_today_positive_negative_ledger_and_explains_must_reply() -> None:
    asyncio.run(_group_query_uses_today_positive_negative_ledger_and_explains_must_reply())


async def _group_query_uses_today_positive_negative_ledger_and_explains_must_reply() -> None:
    matcher = _Matcher()
    data = {
        "favorability": -80,
        "daily_positive_count": 2,
        "daily_positive_date": "2026-08-28",
        "daily_negative_count": 3,
        "daily_negative_date": "2026-08-28",
        "daily_fav_count": 99,
    }
    with pytest.raises(_Finished) as finished:
        await admin_commands.handle_group_fav_query_command(
            matcher,
            sign_in_available=True,
            group_id="100",
            get_user_data=lambda _: data,
            get_level_name=lambda _: "厌恶",
            build_group_fav_markdown=admin_helpers.build_group_fav_markdown,
            build_group_fav_text=admin_helpers.build_group_fav_text,
            md_to_pic=None,
            message_segment_cls=object,
            finished_exception_cls=_Finished,
            logger=type("L", (), {"error": lambda *_: None})(),
            current_date=lambda: "2026-08-28",
        )
    text = str(finished.value)
    assert "今日加分：+2.00" in text and "今日扣分：-3.00" in text and "今日净变化：-1.00" in text
    assert "明确提问仍正常回应" in text
