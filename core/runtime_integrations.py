from __future__ import annotations

from typing import Any, Callable

from .favorability import ExternalFavorabilityAdapter, FavorabilityService, build_external_sign_in_adapter, level_name_for_score


class FavorabilityEnabledGate:
    def __init__(self, service: FavorabilityService) -> None:
        self.service = service

    def __bool__(self) -> bool:
        return bool(self.service.enabled)


def build_sign_in_fallbacks(plugin_config: Any = None, logger: Any = None) -> tuple[Any, Any, Any, Any, Any, bool, Any]:
    """Build the favorability adapter used by legacy runtime fields.

    The public runtime still calls these values sign_in_* for compatibility,
    but relationship policy is plugin-owned. An explicitly configured shared
    backend owns storage; otherwise the old sign-in plugin remains a legacy
    migration/mirroring source.
    """

    profile_backend = None
    if getattr(plugin_config, 'personification_favorability_shared_enabled', False) is True:
        db = getattr(plugin_config, 'personification_favorability_shared_db', None)
        app = getattr(plugin_config, 'personification_favorability_shared_app', None)
        if not db or not app:
            raise ValueError('共享好感度必须配置数据库与应用标识')
        # Deployment-owned bridge is optional for ordinary installations. When
        # explicitly enabled, a missing bridge/marker must fail, never fall back
        # to an independent local balance.
        from shared_accounts import SharedAccounts
        from shared_favorability_profiles import SharedFavorabilityProfiles
        profile_backend = SharedFavorabilityProfiles(
            SharedAccounts(db),
            platform=getattr(plugin_config, 'personification_favorability_shared_platform', 'onebot'),
            app=app,
        )
        profile_backend.load_document()
    # Importing sign_in registers its runtime jobs. Shared/official deployments
    # must never import it, even when the adapter would later be ignored.
    if profile_backend is not None or bool(getattr(plugin_config, 'personification_qq_official_enabled', False)):
        external = ExternalFavorabilityAdapter(
            available=False,
            get_user_data=lambda _uid: {},
            update_user_data=lambda *_a, **_k: None,
            load_data=lambda: {},
            get_level_name=level_name_for_score,
        )
    else:
        external = build_external_sign_in_adapter()
    service = FavorabilityService(
        plugin_config=plugin_config,
        external=external,
        logger=logger,
        profile_backend=profile_backend,
    )
    return (
        FavorabilityEnabledGate(service),
        service.get_user_data,
        service.update_user_data,
        service.load_data,
        service.get_level_name,
        external.available,
        service,
    )


def get_scheduler() -> Any:
    try:
        from nonebot_plugin_apscheduler import scheduler

        return scheduler
    except Exception:
        return None


def extract_default_bot_nickname(load_prompt: Callable[[str | None], Any], logger: Any) -> str:
    try:
        prompt_data = load_prompt(None)
        if isinstance(prompt_data, dict):
            name = str(prompt_data.get("name", "")).strip()
            if name:
                return name
    except Exception as exc:
        logger.debug(f"拟人插件：提取默认昵称失败，使用回退昵称。{exc}")
    return ""


__all__ = [
    "FavorabilityEnabledGate",
    "build_sign_in_fallbacks",
    "extract_default_bot_nickname",
    "get_scheduler",
]
