from __future__ import annotations

import hashlib
import secrets
import time
from typing import Any

from .data_store import get_data_store


_NS_VERIFY_CODES = "webui_verify_codes"
_NS_DEVICES = "webui_devices"
_NS_RATE_LIMIT = "webui_rate_limit"

_VERIFY_TTL_SECONDS = 300
_RATE_WINDOW_SECONDS = 3600
_RATE_MAX_ATTEMPTS = 5


def _now() -> float:
    return time.time()


def _hash_token(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def _hash_ip(ip: str) -> str:
    return hashlib.sha256(str(ip or "").encode("utf-8")).hexdigest()[:16]


def create_verify_code(qq: str) -> str:
    """生成 6 位数字验证码，写 KV，返回明文码（仅本次推送用）。"""
    qq_key = str(qq or "").strip()
    if not qq_key:
        raise ValueError("qq required")
    code = f"{secrets.randbelow(1_000_000):06d}"

    def _mutate(current: object) -> dict[str, Any]:
        data = current if isinstance(current, dict) else {}
        data[qq_key] = {"code": code, "expires_at": _now() + _VERIFY_TTL_SECONDS}
        return _prune_expired_codes(data)

    get_data_store().mutate_sync(_NS_VERIFY_CODES, _mutate)
    return code


def consume_verify_code(qq: str, code: str) -> bool:
    """校验并销毁验证码。成功返 True。"""
    qq_key = str(qq or "").strip()
    target = str(code or "").strip()
    if not qq_key or not target:
        return False
    matched = False

    def _mutate(current: object) -> dict[str, Any]:
        nonlocal matched
        data = current if isinstance(current, dict) else {}
        entry = data.get(qq_key)
        if isinstance(entry, dict):
            if (
                str(entry.get("code", "")) == target
                and float(entry.get("expires_at", 0)) > _now()
            ):
                matched = True
            data.pop(qq_key, None)
        return _prune_expired_codes(data)

    get_data_store().mutate_sync(_NS_VERIFY_CODES, _mutate)
    return matched


def _prune_expired_codes(data: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    return {
        qq: entry
        for qq, entry in data.items()
        if isinstance(entry, dict) and float(entry.get("expires_at", 0)) > now
    }


def issue_device_token(qq: str, ua: str, ip: str, label: str = "") -> str:
    """生成 device token，写 KV，返回明文 token（设到 cookie）。"""
    qq_key = str(qq or "").strip()
    if not qq_key:
        raise ValueError("qq required")
    token = secrets.token_urlsafe(32)
    token_hash = _hash_token(token)
    record = {
        "qq": qq_key,
        "ua": str(ua or "")[:512],
        "ip_hash": _hash_ip(ip),
        "label": str(label or "").strip()[:64] or "未命名设备",
        "created_at": _now(),
        "last_seen": _now(),
    }

    def _mutate(current: object) -> dict[str, Any]:
        data = current if isinstance(current, dict) else {}
        data[token_hash] = record
        return data

    get_data_store().mutate_sync(_NS_DEVICES, _mutate)
    return token


def lookup_device(token: str, *, ua: str = "") -> dict[str, Any] | None:
    """根据 cookie token 查设备记录；命中后刷新 last_seen。"""
    target = str(token or "").strip()
    if not target:
        return None
    token_hash = _hash_token(target)
    matched: dict[str, Any] | None = None

    def _mutate(current: object) -> dict[str, Any]:
        nonlocal matched
        data = current if isinstance(current, dict) else {}
        entry = data.get(token_hash)
        if isinstance(entry, dict):
            stored_ua = str(entry.get("ua", "") or "")
            if ua and stored_ua and stored_ua != str(ua or "")[:512]:
                # UA 不一致：怀疑 cookie 被换设备使用，拒绝
                return data
            entry["last_seen"] = _now()
            matched = dict(entry)
            data[token_hash] = entry
        return data

    get_data_store().mutate_sync(_NS_DEVICES, _mutate)
    return matched


def list_devices(qq: str | None = None) -> list[dict[str, Any]]:
    qq_key = str(qq or "").strip()
    data = get_data_store().load_sync(_NS_DEVICES)
    if not isinstance(data, dict):
        return []
    out: list[dict[str, Any]] = []
    for token_hash, entry in data.items():
        if not isinstance(entry, dict):
            continue
        if qq_key and entry.get("qq") != qq_key:
            continue
        item = dict(entry)
        item["id"] = token_hash
        out.append(item)
    out.sort(key=lambda x: float(x.get("last_seen", 0) or 0), reverse=True)
    return out


def revoke_device(device_id: str) -> bool:
    target = str(device_id or "").strip()
    if not target:
        return False
    removed = False

    def _mutate(current: object) -> dict[str, Any]:
        nonlocal removed
        data = current if isinstance(current, dict) else {}
        if target in data:
            data.pop(target, None)
            removed = True
        return data

    get_data_store().mutate_sync(_NS_DEVICES, _mutate)
    return removed


def record_login_attempt(ip: str) -> int:
    """记录登录尝试，返回当前窗口内的累计计数。"""
    bucket = _hash_ip(ip)
    count = 0

    def _mutate(current: object) -> dict[str, Any]:
        nonlocal count
        data = current if isinstance(current, dict) else {}
        entry = data.get(bucket)
        now = _now()
        if not isinstance(entry, dict) or now - float(entry.get("window_start", 0)) > _RATE_WINDOW_SECONDS:
            entry = {"window_start": now, "count": 0}
        entry["count"] = int(entry.get("count", 0)) + 1
        count = int(entry["count"])
        data[bucket] = entry
        return _prune_expired_rate_buckets(data)

    get_data_store().mutate_sync(_NS_RATE_LIMIT, _mutate)
    return count


def is_login_locked(ip: str) -> bool:
    bucket = _hash_ip(ip)
    data = get_data_store().load_sync(_NS_RATE_LIMIT)
    if not isinstance(data, dict):
        return False
    entry = data.get(bucket)
    if not isinstance(entry, dict):
        return False
    if _now() - float(entry.get("window_start", 0)) > _RATE_WINDOW_SECONDS:
        return False
    return int(entry.get("count", 0)) >= _RATE_MAX_ATTEMPTS


def reset_login_attempts(ip: str) -> None:
    bucket = _hash_ip(ip)

    def _mutate(current: object) -> dict[str, Any]:
        data = current if isinstance(current, dict) else {}
        data.pop(bucket, None)
        return data

    get_data_store().mutate_sync(_NS_RATE_LIMIT, _mutate)


def _prune_expired_rate_buckets(data: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    return {
        bucket: entry
        for bucket, entry in data.items()
        if isinstance(entry, dict)
        and now - float(entry.get("window_start", 0)) <= _RATE_WINDOW_SECONDS
    }


__all__ = [
    "create_verify_code",
    "consume_verify_code",
    "issue_device_token",
    "lookup_device",
    "list_devices",
    "revoke_device",
    "record_login_attempt",
    "is_login_locked",
    "reset_login_attempts",
]
