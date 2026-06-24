from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from typing import Any, Callable


DEFAULT_FAVORABILITY_LEVELS: dict[str, float] = {
    "初见": 0.0,
    "面熟": 10.0,
    "初识": 20.0,
    "普通": 35.0,
    "熟悉": 50.0,
    "信赖": 65.0,
    "知心": 75.0,
    "深厚": 85.0,
    "挚友": 92.0,
    "亲密": 98.0,
}

_STORE_NAME = "favorability_profiles"


def get_data_store() -> Any:
    from .data_store import get_data_store as _get_data_store

    return _get_data_store()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp_score(value: Any, default: float = 0.0) -> float:
    return round(max(0.0, min(100.0, _safe_float(value, default))), 2)


def _is_group_key(user_id: str) -> bool:
    text = str(user_id or "").strip()
    return text.startswith("group_") and not text.startswith("group_private_")


def default_favorability_for_id(user_id: str, plugin_config: Any = None) -> float:
    if _is_group_key(user_id):
        return _clamp_score(
            getattr(plugin_config, "personification_favorability_group_default_score", 100.0),
            100.0,
        )
    return _clamp_score(
        getattr(plugin_config, "personification_favorability_default_score", 0.0),
        0.0,
    )


def normalize_favorability_profile(
    user_id: str,
    value: Any,
    *,
    plugin_config: Any = None,
) -> dict[str, Any]:
    profile = dict(value) if isinstance(value, dict) else {}
    default_score = default_favorability_for_id(user_id, plugin_config)
    profile["favorability"] = _clamp_score(profile.get("favorability", default_score), default_score)

    for key in ("daily_fav_count", "daily_interesting_count"):
        profile[key] = round(max(0.0, _safe_float(profile.get(key, 0.0), 0.0)), 2)
    for key in ("last_update", "last_interesting_date", "custom_title", "nickname"):
        profile[key] = str(profile.get(key, "") or "").strip()
    for key in ("blacklist_count",):
        try:
            profile[key] = max(0, int(profile.get(key, 0) or 0))
        except (TypeError, ValueError):
            profile[key] = 0
    profile["is_perm_blacklisted"] = bool(profile.get("is_perm_blacklisted", False))
    profile.setdefault("created_at", int(time.time()))
    profile["updated_at"] = int(_safe_float(profile.get("updated_at", time.time()), time.time()))
    profile["schema_version"] = 1
    return profile


def normalize_favorability_levels(value: Any) -> dict[str, float]:
    raw = value if isinstance(value, dict) and value else DEFAULT_FAVORABILITY_LEVELS
    levels: dict[str, float] = {}
    for name, threshold in raw.items():
        label = str(name or "").strip()
        if not label:
            continue
        levels[label] = _clamp_score(threshold, 0.0)
    if not levels:
        levels = dict(DEFAULT_FAVORABILITY_LEVELS)
    return dict(sorted(levels.items(), key=lambda item: item[1]))


def level_name_for_score(score: Any, levels: Any = None) -> str:
    value = _clamp_score(score, 0.0)
    resolved = normalize_favorability_levels(levels)
    selected = next(iter(resolved), "普通")
    for name, threshold in resolved.items():
        if value >= threshold:
            selected = name
        else:
            break
    return selected or "普通"


@dataclass
class ExternalFavorabilityAdapter:
    available: bool
    get_user_data: Callable[[str], dict[str, Any]]
    update_user_data: Callable[..., None]
    load_data: Callable[[], dict[str, dict[str, Any]]]
    get_level_name: Callable[[float], str]


class FavorabilityService:
    """Plugin-owned favorability store with one-time compatibility migration.

    The old sign-in plugin remains a best-effort import source, but the
    personification plugin writes and reads its own profile document.
    """

    def __init__(
        self,
        *,
        plugin_config: Any = None,
        external: ExternalFavorabilityAdapter | None = None,
        logger: Any = None,
    ) -> None:
        self.plugin_config = plugin_config
        self.external = external or ExternalFavorabilityAdapter(
            available=False,
            get_user_data=lambda _uid: {},
            update_user_data=lambda *_a, **_k: None,
            load_data=lambda: {},
            get_level_name=lambda value: level_name_for_score(value),
        )
        self.logger = logger

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.plugin_config, "personification_favorability_enabled", True))

    @property
    def external_available(self) -> bool:
        return bool(self.external.available)

    def _load_store(self) -> dict[str, dict[str, Any]]:
        raw = get_data_store().load_sync(_STORE_NAME)
        data = raw if isinstance(raw, dict) else {}
        changed = False
        normalized: dict[str, dict[str, Any]] = {}
        for user_id, profile in data.items():
            key = str(user_id or "").strip()
            if not key:
                changed = True
                continue
            normalized_profile = normalize_favorability_profile(
                key,
                profile,
                plugin_config=self.plugin_config,
            )
            normalized[key] = normalized_profile
            if normalized_profile != profile:
                changed = True
        if changed:
            get_data_store().save_sync(_STORE_NAME, normalized)
        return normalized

    def _save_store(self, data: dict[str, dict[str, Any]]) -> None:
        normalized = {
            str(user_id): normalize_favorability_profile(
                str(user_id),
                profile,
                plugin_config=self.plugin_config,
            )
            for user_id, profile in (data or {}).items()
            if str(user_id or "").strip()
        }
        get_data_store().save_sync(_STORE_NAME, normalized)

    def _external_user_data(self, user_id: str) -> dict[str, Any]:
        if not self.external.available:
            return {}
        try:
            data = self.external.get_user_data(str(user_id))
        except Exception as exc:
            if self.logger is not None:
                try:
                    self.logger.debug(f"拟人插件：读取外部签到好感度失败 user={user_id}: {exc}")
                except Exception:
                    pass
            return {}
        return dict(data) if isinstance(data, dict) else {}

    def _merge_external_fields(self, local: dict[str, Any], external: dict[str, Any]) -> dict[str, Any]:
        if not external:
            return dict(local)
        merged = dict(local)
        for key in (
            "custom_title",
            "nickname",
            "is_perm_blacklisted",
            "blacklist_count",
            "last_update",
            "daily_fav_count",
            "last_interesting_date",
            "daily_interesting_count",
        ):
            if key not in merged or merged.get(key) in ("", None, 0, 0.0, False):
                if key in external:
                    merged[key] = external.get(key)
        return merged

    def get_user_data(self, user_id: str) -> dict[str, Any]:
        key = str(user_id or "").strip()
        if not key:
            return {}
        data = self._load_store()
        existing = data.get(key)
        external = self._external_user_data(key)
        if existing is None:
            seed = external or {"favorability": default_favorability_for_id(key, self.plugin_config)}
            profile = normalize_favorability_profile(key, seed, plugin_config=self.plugin_config)
            profile["source"] = "external_sign_in" if external else "personification"
            data[key] = profile
            self._save_store(data)
            return copy.deepcopy(profile)
        merged = self._merge_external_fields(existing, external)
        profile = normalize_favorability_profile(key, merged, plugin_config=self.plugin_config)
        if profile != existing:
            data[key] = profile
            self._save_store(data)
        return copy.deepcopy(profile)

    def update_user_data(self, user_id: str, **patch: Any) -> None:
        key = str(user_id or "").strip()
        if not key:
            return
        data = self._load_store()
        current = data.get(key)
        if current is None:
            current = self.get_user_data(key)
            data = self._load_store()
        updated = dict(current or {})
        updated.update(dict(patch or {}))
        updated["updated_at"] = int(time.time())
        data[key] = normalize_favorability_profile(key, updated, plugin_config=self.plugin_config)
        self._save_store(data)
        if self.external.available:
            try:
                self.external.update_user_data(key, **patch)
            except Exception as exc:
                if self.logger is not None:
                    try:
                        self.logger.debug(f"拟人插件：同步外部签到好感度失败 user={key}: {exc}")
                    except Exception:
                        pass

    def load_data(self) -> dict[str, dict[str, Any]]:
        local = self._load_store()
        merged: dict[str, dict[str, Any]] = {}
        if self.external.available:
            try:
                external = self.external.load_data() or {}
            except Exception:
                external = {}
            if isinstance(external, dict):
                for user_id, profile in external.items():
                    key = str(user_id or "").strip()
                    if key:
                        merged[key] = normalize_favorability_profile(
                            key,
                            profile,
                            plugin_config=self.plugin_config,
                        )
        for user_id, profile in local.items():
            merged[str(user_id)] = normalize_favorability_profile(
                str(user_id),
                profile,
                plugin_config=self.plugin_config,
            )
        if merged != local:
            self._save_store(merged)
        return copy.deepcopy(merged)

    def get_level_name(self, value: float) -> str:
        levels = getattr(self.plugin_config, "personification_favorability_levels", None)
        return level_name_for_score(value, levels)


def build_external_sign_in_adapter() -> ExternalFavorabilityAdapter:
    try:
        try:
            from plugin.sign_in.utils import get_user_data, load_data, update_user_data  # type: ignore
            from plugin.sign_in.config import get_level_name  # type: ignore
        except ImportError:
            from ...sign_in.utils import get_user_data, load_data, update_user_data  # type: ignore
            from ...sign_in.config import get_level_name  # type: ignore
        return ExternalFavorabilityAdapter(
            available=True,
            get_user_data=get_user_data,
            update_user_data=update_user_data,
            load_data=load_data,
            get_level_name=get_level_name,
        )
    except ImportError:
        return ExternalFavorabilityAdapter(
            available=False,
            get_user_data=lambda _uid: {},
            update_user_data=lambda *_a, **_k: None,
            load_data=lambda: {},
            get_level_name=lambda value: level_name_for_score(value),
        )


__all__ = [
    "DEFAULT_FAVORABILITY_LEVELS",
    "ExternalFavorabilityAdapter",
    "FavorabilityService",
    "build_external_sign_in_adapter",
    "default_favorability_for_id",
    "level_name_for_score",
    "normalize_favorability_levels",
    "normalize_favorability_profile",
]
