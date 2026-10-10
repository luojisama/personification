"""WebUI identities keep Telegram grants separate from legacy numeric QQ IDs."""
from __future__ import annotations

import re
from typing import Any, Iterable


def telegram_user_id(value: Any) -> int | None:
    # Telegram User.id uses at most 52 significant bits. Reject aliases such as
    # signs, leading zeros, Unicode digits and platform-prefixed config values.
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,15}", value):
        return None
    number = int(value)
    return number if number < 2**52 else None


def telegram_identity_id(identity: str) -> int | None:
    if not isinstance(identity, str) or not identity.startswith("telegram:"):
        return None
    return telegram_user_id(identity.removeprefix("telegram:"))


def normalize_telegram_admins(values: Any) -> list[str]:
    if not isinstance(values, list):
        raise ValueError("Telegram 管理员必须为数字用户 ID 的 JSON 数组")
    result: list[str] = []
    for value in values:
        if telegram_user_id(value) is None:
            raise ValueError("Telegram 管理员仅接受正数字用户 ID 字符串，不含平台前缀")
        if value not in result:
            result.append(value)
    return result


def configured_telegram_identities(plugin_config: Any) -> list[str]:
    try:
        values = normalize_telegram_admins(getattr(plugin_config, "personification_telegram_admins", []))
    except ValueError:
        return []
    return [f"telegram:{value}" for value in values]


def is_qq_identity(identity: str) -> bool:
    return isinstance(identity, str) and bool(re.fullmatch(r"[0-9]+", identity))


def is_webui_admin(identity: str, *, plugin_config: Any, superusers: Iterable[str]) -> bool:
    if telegram_identity_id(identity) is not None:
        return identity in configured_telegram_identities(plugin_config)
    if not is_qq_identity(identity):
        return False
    from . import admin_acl

    return identity in superusers or admin_acl.is_plugin_admin(identity)


def webui_identity_metadata(identity: str, *, superusers: Iterable[str]) -> dict[str, str]:
    if telegram_identity_id(identity) is not None:
        return {"platform": "telegram", "identity_source": "telegram_admin"}
    return {"platform": "qq", "identity_source": "SUPERUSER" if identity in superusers else "plugin_admin"}
