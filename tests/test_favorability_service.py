from __future__ import annotations

from types import SimpleNamespace

from ._loader import load_personification_module

favorability = load_personification_module("plugin.personification.core.favorability")


class _FakeStore:
    def __init__(self) -> None:
        self.payload: dict[str, dict] = {}

    def load_sync(self, name: str):  # noqa: ANN001
        assert name == "favorability_profiles"
        return self.payload

    def save_sync(self, name: str, data):  # noqa: ANN001
        assert name == "favorability_profiles"
        self.payload = data


def test_favorability_service_migrates_external_data_and_writes_internal(monkeypatch) -> None:  # noqa: ANN001
    store = _FakeStore()
    monkeypatch.setattr(favorability, "get_data_store", lambda: store)
    external_updates: list[tuple[str, dict]] = []
    external = favorability.ExternalFavorabilityAdapter(
        available=True,
        get_user_data=lambda uid: {"favorability": 42.5, "custom_title": "熟人"} if uid == "10001" else {},
        update_user_data=lambda uid, **patch: external_updates.append((uid, patch)),
        load_data=lambda: {"10001": {"favorability": 42.5, "custom_title": "熟人"}},
        get_level_name=lambda value: f"external:{value}",
    )
    service = favorability.FavorabilityService(
        plugin_config=SimpleNamespace(
            personification_favorability_enabled=True,
            personification_favorability_default_score=5.0,
            personification_favorability_group_default_score=95.0,
            personification_favorability_levels={"初见": 0, "普通": 35, "挚友": 90},
        ),
        external=external,
    )

    profile = service.get_user_data("10001")

    assert profile["favorability"] == 42.5
    assert profile["custom_title"] == "熟人"
    assert store.payload["10001"]["source"] == "external_sign_in"

    service.update_user_data("10001", favorability=93, daily_interesting_count=1.2)

    assert service.get_user_data("10001")["favorability"] == 93
    assert service.get_level_name(93) == "挚友"
    assert external_updates[-1] == ("10001", {"favorability": 93, "daily_interesting_count": 1.2})


def test_favorability_service_uses_internal_defaults(monkeypatch) -> None:  # noqa: ANN001
    store = _FakeStore()
    monkeypatch.setattr(favorability, "get_data_store", lambda: store)
    service = favorability.FavorabilityService(
        plugin_config=SimpleNamespace(
            personification_favorability_default_score=7.0,
            personification_favorability_group_default_score=88.0,
        )
    )

    assert service.get_user_data("20002")["favorability"] == 7.0
    assert service.get_user_data("group_123")["favorability"] == 88.0
